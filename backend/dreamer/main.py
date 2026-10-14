import asyncio
import fcntl
import hmac
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import printer
from .config import Settings
from .schema import Approval, GenerateRequest
from .service import Service
from .store import Conflict, Store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.data_dir)
    service = Service(settings, store)

    @asynccontextmanager
    async def lifespan(app):
        with (store.root / "worker.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Run exactly one backend worker per data directory"
                ) from exc
            store.recover()
            yield
            tasks = list(service.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Dreamer", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.service = store, service

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if settings.api_token:
                actual = request.headers.get("authorization", "")
                if not hmac.compare_digest(actual, "Bearer " + settings.api_token):
                    return JSONResponse(
                        {"detail": "An API access token is required"}, status_code=401
                    )
            origin = request.headers.get("origin")
            if (
                origin
                and urlparse(origin).netloc != request.headers.get("host")
                and origin not in settings.allowed_origins
            ):
                return JSONResponse(
                    {"detail": "Origin is not allowed"}, status_code=403
                )
            if request.method in {"POST", "PUT", "PATCH"}:
                size, chunks = 0, []
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 21_100_000:
                        return JSONResponse(
                            {"detail": "Request exceeds the upload limit"},
                            status_code=413,
                        )
                    chunks.append(chunk)
                request._body = b"".join(chunks)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Job or artifact not found"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/status")
    async def status():
        return {
            "auth_required": bool(settings.api_token),
            "llm_ready": bool(settings.llm_api_key and settings.llm_model),
            "model": settings.llm_model or "Not configured",
            "slicer_ready": printer.slicer_ready(settings),
            "slicing_presets": printer.preset_names(settings),
            "print_ready": printer.print_ready(settings),
            "printer": settings.printer_name or "A1 mini (not connected)",
            "build_volume": settings.build_volume,
            "filament_slot": settings.filament_slot,
            "bed_type": settings.bed_type,
        }

    @app.get("/healthz", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    @app.get("/api/jobs")
    async def list_jobs():
        return store.list()

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        return store.get(job_id)

    @app.get("/api/jobs/{job_id}/preview")
    async def draft_status(job_id: str):
        job = store.get(job_id)
        preview = None
        if job["state"] in {"generating", "building"}:
            try:
                preview = json.loads(
                    (store.directory(job_id) / "preview.json").read_text()
                )
            except FileNotFoundError:
                pass
        return JSONResponse(
            {"active": job["state"] in {"generating", "building"}, "preview": preview},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/jobs/{job_id}/preview/{revision}")
    async def draft_mesh(job_id: str, revision: str):
        job = store.get(job_id)
        if job["state"] not in {"generating", "building"} or not re.fullmatch(
            r"[a-f0-9]{32}-[0-9]{1,2}", revision
        ):
            raise HTTPException(404, "Draft preview is no longer available")
        try:
            data = (store.directory(job_id) / f"preview-{revision}.stl").read_bytes()
        except FileNotFoundError as exc:
            raise HTTPException(404, "Draft preview is no longer available") from exc
        return Response(
            data, media_type="model/stl", headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/jobs", status_code=202)
    async def generate(body: GenerateRequest):
        if not body.demo_template and not (settings.llm_api_key and settings.llm_model):
            raise HTTPException(503, "Configure the LLM or select a demo template")
        if body.parent_id and not store.get(body.parent_id)["plan"]:
            raise HTTPException(409, "The parent model has no CAD plan to revise")
        if len(service.tasks) >= 8:
            raise HTTPException(
                429, "The local job queue is full; wait for a job to finish"
            )
        job = store.create(body.prompt, body.parent_id, bool(body.demo_template))
        service.launch(service.generate(job["id"], body))
        return job

    @app.post("/api/jobs/{job_id}/slice", status_code=202)
    async def slice_job(job_id: str, approval: Approval):
        job = store.get(job_id)
        if job["state"] != "preview":
            raise Conflict("Only a model ready for preview can be approved for slicing")
        if (
            approval.artifact_sha256 != job["model_sha256"]
            or printer.digest(store.directory(job_id) / "model.stl")
            != job["model_sha256"]
        ):
            raise Conflict("Model changed; review the current model before approving")
        if not job["geometry"]["fits_build_volume"]:
            raise HTTPException(
                422, "Model exceeds the configured printer build volume"
            )
        try:
            printer.validate_profiles(settings)
        except (ValueError, OSError) as exc:
            raise HTTPException(503, service.error(exc)) from exc
        job = store.transition(
            job_id,
            {"preview"},
            "slicing",
            "Model approved; slicing with configured printer presets",
            error=None,
        )
        service.launch(service.slice(job_id))
        return job

    @app.post("/api/jobs/{job_id}/print", status_code=202)
    async def print_job(job_id: str, approval: Approval):
        job = store.get(job_id)
        if job["state"] != "sliced":
            raise Conflict("Only a sliced job can be submitted once")
        if not printer.print_ready(settings):
            raise HTTPException(
                503, "Printing is disabled or the printer transport is not configured"
            )
        if (
            approval.artifact_sha256 != job["slice_sha256"]
            or printer.digest(store.directory(job_id) / "print.gcode.3mf")
            != job["slice_sha256"]
        ):
            raise Conflict("Sliced artifact changed; approval is no longer valid")
        job = store.transition(
            job_id,
            {"sliced"},
            "sending",
            "Print explicitly confirmed; running printer preflight and dispatch",
        )
        service.launch(service.send(job_id))
        return job

    @app.get("/api/jobs/{job_id}/files/{filename}")
    async def artifact(job_id: str, filename: str):
        job = store.get(job_id)
        allowed = {"model.stl", "model.step", "plan.json", "print.gcode.3mf"}
        if filename not in allowed:
            raise HTTPException(404, "Artifact not found")
        if filename == "print.gcode.3mf" and not job["slice_sha256"]:
            raise HTTPException(404, "No verified sliced artifact is available")
        if filename in {"model.stl", "model.step"} and not job["model_sha256"]:
            raise HTTPException(404, "No verified model is available")
        path = store.directory(job_id) / filename
        if not path.is_file():
            raise HTTPException(404, "Artifact not found")
        return FileResponse(path, filename=filename)

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
