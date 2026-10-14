# Dreamer

A local web workbench that turns text and reference images into editable CAD solids, previews them in 3D, and sends approved designs through Bambu Studio to a Bambu Lab printer.

The default build volume is **180 x 180 x 180 mm**, suitable for an A1 mini. The LLM adapter works with OpenAI-compatible Chat Completions providers, including self-hosted gateways. Configure your own provider, model, and printer before use.

## Quick start

Requires Linux or macOS, Python 3.11–3.13, Node.js 22+, npm, and [uv](https://docs.astral.sh/uv/). Windows users can run the backend in WSL, with CLI paths and printer networking configured inside that environment.

```sh
make setup
cp .env.example .env
chmod 600 .env
make run
```

Open **http://127.0.0.1:8000**. Do not overwrite an existing `.env`. `make run` builds the frontend and serves everything from one backend process. `make dev` starts the API on port 8000 and Vite on port 5174 with live frontend updates. Override the frontend port with `DREAMER_FRONTEND_PORT`.

Without an LLM key, select **Try demo**, then **Build demo model**. The three fixed templates generate real STEP/STL files through CadQuery; they do not interpret text or images and are explicitly marked as demos.

## Docker deployment

The multi-stage image contains the compiled frontend, API, CadQuery runtime,
and the official Bambu Studio 2.8.2.61 Linux CLI, verified against a pinned SHA-256.
It runs as UID/GID `10001:10001`, listens on container port 8000, and stores all
durable data under `/data`. The supplied Compose file supports a read-only root
filesystem, writable temporary storage, a persistent named volume, resource
limits, and a health check at `/healthz`.

```sh
cp .env.example .env
chmod 600 .env
# Configure the provider and model in .env, then:
docker compose up -d --build
```

Open **http://127.0.0.1:8010**. The published port binds to loopback by default.
To expose the service to a trusted network, set `DREAMER_BIND_ADDRESS` to the
appropriate interface and configure `DREAMER_API_TOKEN` before starting it.
Enter that token in **Connections & settings** in the browser. Use an
authenticated HTTPS reverse proxy when exposing it beyond a trusted network.

Containers have their own loopback interface. For an LLM gateway running on the
Docker host, use `http://host.docker.internal:4000/v1`; Compose adds the host
gateway mapping on Linux. For a gateway in the same Docker network, use its
service name. Never bake credentials or `.env` into the image.

The published image is available at `ghcr.io/mrcroxx/dreamer`. CI tests the
application, builds a `linux/amd64` image, and exercises real CAD generation,
A1 mini slicing, artifact downloads, authentication, disabled dispatch, and
persistence across a container restart. Tests never connect to a printer.
Push builds publish the tested image as `sha-<full-commit>`; only the `main`
branch updates `latest`. Pull requests build and test without publishing. Other
architectures are not currently validated by CI.

```sh
DREAMER_IMAGE=ghcr.io/mrcroxx/dreamer:sha-<full-commit> docker compose pull
DREAMER_IMAGE=ghcr.io/mrcroxx/dreamer:sha-<full-commit> docker compose up -d --no-build
docker compose ps
docker compose logs --tail=100 dreamer
```

The named volume survives container replacement. Back it up before updates;
`docker compose down -v` deletes it. For a bind mount, create a dedicated data
directory writable by UID 10001. Do not run the native backend and container
against the same data directory at the same time. A new named volume starts with
an empty workspace; copying existing data requires stopping writers and copying
the database and artifact directories together.

The image includes flattened official presets for **A1 mini, 0.4 mm nozzle,
0.20 mm Standard, Generic PLA, Textured PEI Plate** under `/opt/dreamer-profiles`.
These are explicit starter settings for slicing, not detected hardware or material.
Verify them against the physical printer before enabling dispatch. The Compose
file supplies the container paths even when the native `.env` example has empty
profile paths. Custom presets can be mounted read-only with their paths configured
in the environment. Bambu Studio runs under Xvfb without a cloud login or network
plugin. The application never contacts a printer at startup or from `/api/status`.

The bundled direct LAN adapter is disabled by default. The optional community
dispatch CLI must still be installed separately. Neither path requires a Docker
socket or privileged mode. Bambu Studio is third-party software distributed under
AGPL-3.0; its corresponding source and license are available at the pinned
[upstream release](https://github.com/bambulab/BambuStudio/tree/v02.08.02.61).

To test an image without any provider or printer credentials:

```sh
docker build -t dreamer:test .
python3 scripts/smoke_container.py dreamer:test
```

## Model connection

Set these server-only variables in `.env` and restart the backend:

```dotenv
DREAMER_LLM_BASE_URL=https://api.openai.com/v1
DREAMER_LLM_API_KEY=your-provider-key
DREAMER_LLM_MODEL=your-vision-capable-model
```

The provider must support `/chat/completions` and `response_format: {"type":"json_object"}`. Image requests use inline base64 image content; the configured alias must route to a vision-capable model. A text-only model cannot interpret reference images. Images are limited to three PNG/JPEG/WebP files, each at most 5 MB and 16 megapixels. The backend validates the actual image bytes.

Streaming is enabled by default. Complete, validated CAD operations are built as
the response arrives, so the viewer can show a **Live draft** before generation
finishes. The first draft requires a complete operation; provider reasoning time
still appears as a wait. Draft updates preserve the camera, grid, and wireframe.
Only the completed, validated model can be approved for slicing.

One CAD worker evaluates each operation once. Draft meshes use independent
geometry copies and coarse tessellation, at most once every 1.5 seconds and eight
times per attempt, with a 4 MB limit per mesh. This adds some CPU, memory, and
network overhead; final STL/STEP precision, validation, and LLM quality settings
are unchanged. Set `DREAMER_LIVE_PREVIEW=false` to avoid draft export costs, or
`DREAMER_LLM_STREAM=false` for providers without Chat Completions SSE support.
In the latter case, CAD starts after the complete response arrives.

Expand **Model output** below the viewer to optionally follow the provider's raw
response text and any returned `reasoning_content` / `reasoning` text. The panel
is closed by default and only fetches output while open. It supports following
the latest text, scrolling back, downloading the displayed transcript, and
replaying output after a page refresh. Attempts and retries remain separate.
Provider-internal reasoning that is not returned by the API cannot be shown.

Output is recorded in the private job directory, not in application logs, with
an 8 MB limit per job; reaching the limit does not stop generation. Treat these
records as part of your private workspace backups. Old jobs have no original
transcript and can only show their saved CAD plan. Non-streaming providers show
their output once the response arrives; demos show their fixed template.

LLM calls go directly to the configured base URL, ignoring shell proxy variables so localhost requests and API credentials cannot accidentally be forwarded through a system proxy. If your provider needs a proxy, configure that upstream in LiteLLM.

Secrets stay out of the frontend, API status responses, source control, and application error messages. Prompts and generated plans are persisted under `data/`. Images are transmitted to the configured LLM and held in memory only; they are not retained in the job history. Refinements receive the previous CAD plan and newly attached images, not old image attachments.

## Workflow

1. Describe the part, attach references if useful, and generate a model.
2. Orbit, pan, zoom, toggle grid/wireframe, inspect dimensions and design assumptions, and download STEP or STL.
3. **Refine this model** creates a new immutable job from the previous CAD plan.
4. **Approve & slice** approves the exact STL hash and invokes Bambu Studio with configured presets.
5. Download the sliced 3MF or **Send to printer**, review the target and material slot, and explicitly confirm that the physical setup is ready.

Models are represented by validated JSON operations: boxes, cylinders, spheres, extruded polygons, unions, cuts, intersections, translations, rotations, fillets, and chamfers. The backend interprets these operations with CadQuery/OpenCascade. It never executes generated Python, shell commands, or arbitrary file paths from the LLM. Each build runs in a separate process with a deadline. Only one connected, valid solid is accepted. Builds are centered and placed at Z=0; oversized models remain downloadable but cannot be sliced through the app.

This initial modeling vocabulary is intended for geometric and functional parts. Organic reconstruction, assemblies, lofts, threads, and guaranteed dimensional reconstruction from a single photo are not supported. Review generated dimensions and assumptions before fabrication. Geometry validation does not certify strength, tolerances, overhangs, or manufacturability.

## Bambu Studio slicing

Install [Bambu Studio](https://github.com/bambulab/BambuStudio/releases) on the machine running the backend. Set the executable and **full, flattened** printer/process/filament preset paths:

```dotenv
DREAMER_SLICER_BIN=/path/to/bambu-studio
DREAMER_MACHINE_PROFILE=/path/to/a1-mini-machine.json
DREAMER_PROCESS_PROFILE=/path/to/process.json
DREAMER_FILAMENT_PROFILE=/path/to/filament.json
DREAMER_BED_TYPE=Textured PEI Plate
DREAMER_BUILD_VOLUME=[180,180,180]
```

Use presets matching your actual **A1 mini, nozzle diameter, build plate, and material**. The raw preset fragments shipped in Bambu Studio's resources are not full presets: inheritance and included G-code templates must be resolved. Files containing nonempty `inherits` or `include` fields are rejected. The image resolves its starter presets at build time. To create different presets from an installed release:

```sh
PYTHONPATH=backend uv run python -m dreamer.profiles /path/to/resources/profiles/BBL ./profiles \
  --machine 'Bambu Lab A1 mini 0.4 nozzle' \
  --process '0.20mm Standard @BBL A1M' \
  --filament 'Generic PLA @BBL A1M'
```

No temperature or G-code values are invented by the resolver. It preserves the
official preset inheritance, includes, and leaf overrides, and fails on missing
parents or cycles. Review the resulting presets before use.

The integration follows [Bambu Studio's official CLI contract](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage), invoking `--load-settings`, `--load-filaments`, `--curr-bed-type`, `--arrange 1`, `--slice 0`, and `--export-3mf`. It verifies that the output is a 3MF archive containing nonempty G-code. It does not auto-orient the part: the reviewed CAD orientation is preserved, while the slicer arranges it on the plate. On headless Linux, some Bambu Studio builds require an X display; point `DREAMER_SLICER_BIN` at a local executable wrapper using `xvfb-run` if needed.

## Printer dispatch

The official Bambu Studio CLI documents slicing, not a general print-dispatch
command. Dreamer provides two optional transports. Slicing and downloads work
independently of dispatch. **Neither adapter is an official Bambu Lab print API.**

### Direct LAN adapter

Select `lan` for the bundled MQTT/implicit-FTPS adapter. It does not change cloud
connectivity, LAN Only, Developer Mode, firmware, or account settings. Whether
cloud mode permits print commands depends on the printer firmware; successful
telemetry or FTP authentication does not establish print-command compatibility.
This implementation has offline protocol and workflow tests, but physical A1 mini
dispatch has not yet been validated.

```dotenv
DREAMER_PRINTER_TRANSPORT=lan
DREAMER_PRINTER_NAME=workbench
DREAMER_PRINTER_HOST=printer-address
DREAMER_PRINTER_SERIAL=printer-serial
DREAMER_PRINTER_ACCESS_CODE=printer-access-code
DREAMER_FILAMENT_SLOT=A1
DREAMER_PRINT_ENABLED=false
```

Keep these values in a private environment file. The transport verifies the Bambu
CA chain and the configured serial as the TLS server identity. Its trust roots are
documented in [certs/README.md](backend/dreamer/certs/README.md); custom roots can
be supplied through `DREAMER_PRINTER_CA_FILE`. Secrets are passed to the bounded
dispatch subprocess through its environment, never command arguments.

Current scope is one A1 mini plate and one filament, using AMS Lite slots A1–A4.
External spools and multiple filaments are rejected. Before uploading, the adapter
requires fresh telemetry showing an idle printer, available SD card, matching
nozzle and slot material, and no reported errors. It uploads a uniquely named file,
reads it back to verify SHA-256, then opens a fresh MQTT session and repeats the
preflight before publishing one print command. A matching successful printer
response is required; a broker send or unrelated status update is not success.
No automatic publish retries or reconnects occur.

The physical build plate, remaining filament, bed clearance, and exclusive use of
the printer still require operator verification. Another client can start a job
after the final status check; the local protocol does not provide an atomic
reservation. Only enable printing after firmware compatibility and the actual
setup have been checked, then explicitly confirm each job in the web UI.

### Community CLI adapter

The default `cli` transport invokes [voska/bambu](https://github.com/voska/bambu).
Its documented hardware validation is on X1 Carbon, and its preflight requires
LAN Only/Developer Mode. This mode is unsuitable when cloud connectivity must be
preserved. A1 mini compatibility requires independent validation.

Install that CLI separately, configure a named printer using its `printer add` and `auth set` commands, and verify `bambu status`. Follow its documentation for LAN mode, Developer Mode, and credentials. Dreamer never changes printer firmware, operating mode, or authentication settings. Printer credentials belong in the CLI's keychain/configuration, not in the browser.

```dotenv
DREAMER_BAMBU_BIN=/path/to/bambu
DREAMER_PRINTER_NAME=workbench
DREAMER_FILAMENT_SLOT=A1
DREAMER_PRINT_ENABLED=true
```

`A1` is an example filament slot, **not the printer model**. Configure the correct slot for your AMS Lite or external spool according to the CLI version you install. The app shows the selected slot before dispatch. The adapter executes:

```sh
bambu --printer workbench --no-input print send /path/to/print.gcode.3mf --slot A1 --confirm
```

The CLI performs printer, nozzle, material, plate, and idle-state preflight. No command is sent until the user explicitly confirms in the web UI. Keep `DREAMER_PRINT_ENABLED=false` until configuration and compatibility are verified.

Successful dispatch is displayed as **Submitted to printer**, not as printing complete. This version does not stream printer telemetry. Follow live printing in the printer's UI. If dispatch fails or times out, the job becomes **send_unknown** and blocks further dispatches; the command might already have reached the printer. Never automatically retry it.

### Resolving uncertain dispatch

Stop the backend, physically inspect the printer and its job history, and determine whether the exact file was accepted. Then use the local administrative command with the job ID:

```sh
uv run python scripts/resolve_dispatch.py JOB_ID --outcome submitted
```

If you have verified that no print was started, use `--outcome not-sent`. This records the resolution and permits a new explicit approval. The resolver acquires the backend lock and refuses to run while the server is active. It never communicates with the printer. Restart the backend afterward.

## Persistence and operation

- SQLite stores jobs, state transitions, approval events, and errors. Artifact folders use server-generated UUIDs. The UI lists the latest 100 jobs.
- Two modeling tasks and one slicing task can run concurrently. New modeling requests are limited to eight outstanding tasks.
- Run **exactly one backend worker** per data directory. A filesystem lock enforces this constraint. SQLite transactions prevent duplicate dispatch, including simultaneous requests.
- Interrupted modeling/slicing jobs fail on restart. An interrupted dispatch becomes `send_unknown`; no hardware operation is automatically resumed.
- Default endpoints bind to loopback. Before exposing the service to a LAN, configure `DREAMER_API_TOKEN` and an authenticated HTTPS reverse proxy. Enter the workspace token in **Connections & settings**; it is stored for the browser session. This is a single-user application, not a multi-tenant service.
- Back up `data/` and local preset files. Keep `.env`, printer credentials, and artifacts out of source control.

## Development and validation

```sh
make check
cd frontend
npx playwright install chromium
npm run test:e2e
```

Browser checks expect the backend and Vite to be running (`make dev`), create explicitly marked demo jobs, and never call a printer. Backend tests exercise actual CAD exports, image/schema validation, approval hashes, concurrency/restart recovery, authentication, timeout behavior, and fake CLI dispatch contracts. CLI contract tests do not replace hardware integration testing. LLM providers, actual Bambu Studio slicing, and printer behavior depend on local external services.

API docs: http://127.0.0.1:8000/docs. Main endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Configuration readiness; no live device probe |
| `GET /api/jobs` | Latest persisted jobs |
| `POST /api/jobs` | Generate a model or revision |
| `GET /api/jobs/{id}` | Job progress, metadata, and events |
| `POST /api/jobs/{id}/slice` | Approve a specific model hash and slice |
| `POST /api/jobs/{id}/print` | Approve a specific slice hash and dispatch once |
| `GET /api/jobs/{id}/files/{filename}` | Download a verified artifact |

Source layout: `backend/dreamer/` contains the API, state store, LLM adapter, CAD worker, and printer adapter; `frontend/src/` contains the workbench and Three.js viewer.

References: [CadQuery export documentation](https://cadquery.readthedocs.io/en/stable/quickstart.html), [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision), [Bambu Studio CLI](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage), [community dispatch CLI](https://github.com/voska/bambu).
