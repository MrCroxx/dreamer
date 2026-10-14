import asyncio

from . import llm, printer
from .live_cad import LiveCAD
from .output import OutputLog
from .config import Settings
from .examples import example_plan
from .schema import GenerateRequest
from .store import Store


class Service:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store
        self.tasks: set[asyncio.Task] = set()
        self.model_slots = asyncio.Semaphore(2)
        self.slice_slot = asyncio.Semaphore(1)

    def launch(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def error(self, exc: Exception) -> str:
        text = str(exc) or type(exc).__name__
        for secret in [
            self.settings.llm_api_key,
            self.settings.api_token,
            self.settings.printer_access_code,
            self.settings.printer_serial,
            self.settings.printer_host,
        ]:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text[:3000]

    async def generate(self, job_id: str, request: GenerateRequest):
        async with self.model_slots:
            try:
                self.store.transition(
                    job_id, {"queued"}, "generating", "Preparing CAD operations"
                )
                previous = (
                    self.store.get(request.parent_id)["plan"]
                    if request.parent_id
                    else None
                )
                failure = None
                for attempt in range(2):
                    plan = None
                    try:
                        directory = self.store.directory(job_id)
                        async with (
                            asyncio.timeout(180 + self.settings.cad_timeout),
                            LiveCAD(directory, self.settings) as cad,
                            OutputLog(directory, attempt + 1) as output,
                        ):
                            if request.demo_template:
                                plan = example_plan(request.demo_template)
                                output.text("content", plan.model_dump_json(indent=2))
                                for operation in plan.operations:
                                    await cad.operation(operation)
                            else:
                                plan = await llm.generate_plan(
                                    self.settings,
                                    request,
                                    previous,
                                    failure,
                                    on_operation=cad.operation,
                                    on_output=output.text,
                                )
                            (directory / "plan.json").write_text(
                                plan.model_dump_json(indent=2)
                            )
                            self.store.transition(
                                job_id,
                                {"generating", "building"},
                                "building",
                                "Building and validating the solid",
                                plan=plan.model_dump(),
                                name=plan.name,
                            )
                            geometry = await cad.finish(plan)
                        geometry["fits_build_volume"] = all(
                            d <= limit + 0.01
                            for d, limit in zip(
                                geometry["dimensions"], self.settings.build_volume
                            )
                        )
                        self.store.transition(
                            job_id,
                            {"building"},
                            "preview",
                            "Model ready for review",
                            geometry=geometry,
                            model_sha256=printer.digest(directory / "model.stl"),
                            error=None,
                        )
                        return
                    except Exception as exc:
                        failure = self.error(exc)
                        if plan is not None:
                            failure += "\nFailed plan: " + plan.model_dump_json()
                        if request.demo_template or attempt == 1:
                            raise
                        self.store.transition(
                            job_id,
                            {"generating", "building"},
                            "generating",
                            "Repairing the CAD plan after a construction or validation error",
                        )
            except Exception as exc:
                self.store.transition(
                    job_id,
                    {"queued", "generating", "building"},
                    "failed",
                    "Modeling failed",
                    error=self.error(exc),
                )

    async def slice(self, job_id: str):
        async with self.slice_slot:
            try:
                sha = await printer.slice_model(
                    self.settings, self.store.directory(job_id)
                )
                self.store.transition(
                    job_id,
                    {"slicing"},
                    "sliced",
                    "Sliced file ready. Review printer and material before sending.",
                    slice_sha256=sha,
                    error=None,
                )
            except Exception as exc:
                self.store.transition(
                    job_id,
                    {"slicing"},
                    "preview",
                    "Slicing failed; the model is still available",
                    error=self.error(exc),
                )

    async def send(self, job_id: str):
        try:
            await printer.send_print(
                self.settings, self.store.directory(job_id) / "print.gcode.3mf"
            )
            self.store.transition(
                job_id,
                {"sending"},
                "submitted",
                "Print command completed. Check the printer for live progress.",
                error=None,
            )
        except Exception as exc:
            # A timeout can happen after the printer has already accepted the command.
            self.store.transition(
                job_id,
                {"sending"},
                "send_unknown",
                "Dispatch result is uncertain. Check the physical printer; automatic retry is disabled.",
                error=self.error(exc),
            )
