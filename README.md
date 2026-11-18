# GTA CLIP COMMAND

GTA CLIP COMMAND is a local-first workstation for finding, editing, reviewing, and exporting short-form clips from gaming videos. It can be used by individual creators or teams. The project name reflects its original GTA use, while the editing pipeline and visual analysis are designed to work with general gameplay footage.

The application keeps source media, its SQLite database, model settings, and exports on the local machine by default. Optional OpenAI-compatible hosted models can be configured in the dashboard; hosted inference may incur provider charges and sends the selected transcript or sampled frames to that provider.

## Features

- Creator registration and a documented permission gate before footage can be processed.
- Candidate detection from audio energy, speech, scene changes, and gameplay signals.
- Local speech transcription with Faster-Whisper.
- Local vision and reasoning through Ollama, with optional OpenAI-compatible hosted models.
- Vertical reframing, subtitles, loudness normalization, thumbnails, and FFmpeg rendering.
- A browser dashboard for upload, queue monitoring, creator management, clip review, and approval.
- Per-creator rights management with archived permission documents and agreement history.
- Live queue progress and pipeline stage logs, plus a Library for previewing generated clips.
- Windows DPAPI encryption for locally saved cloud keys; Fernet encryption with a server-managed key for hosted per-account API keys.
- Optional hosted account mode with email verification, private account data, a daily free processing allowance, and direct-to-object-storage uploads.

## Requirements

- Windows 10 or 11 for the local desktop-oriented setup (local API-key encryption uses Windows DPAPI; hosted API keys use Fernet).
- Python 3.12.
- FFmpeg and FFprobe available on `PATH`, or set `FFMPEG_BIN` and `FFPROBE_BIN` in `.env`.
- Ollama for local AI inference. The default models are `qwen3:8b` for reasoning and `qwen3.5:4b` for vision; install them with `ollama pull qwen3:8b` and `ollama pull qwen3.5:4b`.
- A GPU is optional. FFmpeg uses NVENC when the installed NVIDIA driver supports the FFmpeg build, and falls back to software encoding otherwise. Model size, transcription speed, and rendering speed depend on your hardware.

## Setup

Open PowerShell in the cloned repository directory:

```powershell
git clone https://github.com/Gag78669/gta-clip-command
Set-Location gta-clip-command
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Install FFmpeg with FFprobe and make both commands available on `PATH`, or edit `.env` with their full paths. Install and start Ollama if you plan to use local models. You can select hosted models instead in the dashboard's **Settings** page.

Initialize the database:

```powershell
python scripts/init_db.py
```

Database initialization does not create a sample creator or fabricate a permission agreement. Register a real creator with a signed permission document:

```powershell
python scripts/register_creator.py `
  --handle "@YourChannel" `
  --name "Your Channel" `
  --channel-url "https://www.youtube.com/@YourChannel" `
  --platform youtube `
  --permission-file "C:\path\to\signed-permission.pdf" `
  --authorized-by "Creator or authorized representative" `
  --agreement-type non_exclusive
```

The permission document is copied into the ignored local `data/agreements/` folder. Confirm you have the necessary rights and permissions before registering a creator or processing footage.

### Manage creator rights

Open **Creators** and select **Rights** on the creator row to view their permission agreements. From the Rights panel you can add an agreement, edit its type, status, authorizer, expiry date, revenue share, and notes, replace its permission document, or revoke it. Archived documents can be opened from the agreement history.

Permission files must be PDF, DOC, DOCX, PNG, or JPG/JPEG and are limited to 20 MB. New agreements require a document and an authorizer. Files are stored under `data/agreements/`, which is local and gitignored. Editing an agreement keeps its existing document unless you select a replacement; revoking changes the agreement status without deleting its record or document.

Only an **active, unexpired** agreement authorizes new uploads. Set an expiry date to the final day the permission is valid, or leave it blank for an agreement without an expiry date. If multiple agreements exist, uploads remain authorized while at least one is active and unexpired. Creator **Content Restrictions** and **Authorized Platforms** are metadata; agreement status and validity control the upload rights gate.

The `scripts/register_creator.py` command remains available for first-time creator registration. When used with an existing handle, it creates an additional agreement; use the dashboard Rights panel to edit or revoke existing agreements.

## Run the application

Place a supported video file in `data/raw_footage/`, or upload it from the dashboard. The data folders are created automatically and are excluded from Git.

To process a video directly:

```powershell
python scripts/run_pipeline.py "data/raw_footage/your-video.mp4" --creator "@YourChannel" --max-clips 3
```

To launch the review dashboard:

```powershell
python scripts/start_dashboard.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The dashboard can also upload videos, manage creators, queue jobs, review clips, and approve or reject results.

### Queue, monitor, and review workflow

- **Queue** shows saved job status, current pipeline stage, progress, candidate count, and produced clips. Start queued jobs, pause/resume or cancel active jobs, retry failed or cancelled jobs, and remove eligible jobs.
- **Retry** starts failed/cancelled jobs again from intake and clears their previous intermediate candidates. Jobs that already produced clips cannot be retried, to avoid duplicating or overwriting reviewable results.
- **Delete** removes an eligible queued, failed, or cancelled job. Jobs with generated clips are protected; review or export those clips before removing associated records.
- **Monitor** follows the pipeline stages and shows per-job log output. It refreshes while open.
- **Library** previews exported clips and their thumbnails. **Review** lists clips for approval or rejection and lets you select a clip to inspect its video and details.

The dashboard server runs without automatic Python code reload. After pulling code updates, stop it with `Ctrl+C` in its terminal and start it again with `python scripts/start_dashboard.py` so API changes take effect. Refresh the browser after the server is ready.

### Optional hosted models

Open **Settings**, select an OpenAI-compatible provider for reasoning and/or vision, enter its API base URL and model IDs, then test and save the connection. The test checks authentication and makes a minimal inference call for each selected model. For Groq, use `https://api.groq.com/openai/v1`. Locally, a user-supplied key is protected with Windows DPAPI; hosted account keys are encrypted with `CLOUD_KEY_ENCRYPTION_KEY`. Neither key is returned to the browser. Keep the key blank to retain a saved user key. Hosted inference sends the relevant transcript and, for vision, sampled frames to the configured provider.

## Zero-cost web UI with local processing

The free deployment keeps the browser interface on Vercel and runs the API, SQLite database, worker, FFmpeg, and Ollama on your Windows computer. Source videos, generated clips, rights documents, and AI keys stay on that computer. Your PC must be on and the dashboard server must be running whenever you want to use the deployed page. This avoids paid Render compute, cloud object storage, and cloud model charges.

Render does offer a Hobby workspace plan and free compute for selected service types. The earlier Render Blueprint selected paid compute plans, so do not approve that pending Blueprint sync. Render Free does not provide a separate background worker, and its free Postgres and Key Value services have data-retention/restart limits. See [Render's free-tier limits](https://render.com/docs/free). The local worker is what keeps the video pipeline available without a paid Render worker.

### 1. Deploy the static UI to Vercel Hobby

Connect this GitHub repository to a Vercel project. Keep the project root at the repository root. The build command in `vercel.json` creates `frontend_dist/`; no `API_ORIGIN`, database, storage, or model secrets belong in Vercel. The static UI asks you to connect to the local API when opened.

### 2. Allow the deployed page to connect to localhost

After Vercel assigns the project URL, copy its exact origin (for example, `https://your-project.vercel.app`) into the local `.env` file:

```dotenv
AUTH_ENABLED=false
REDIS_URL=
FRONTEND_ORIGIN=https://your-project.vercel.app
```

Keep the local SQLite `DATABASE_URL` from `.env.example`, and leave storage credentials empty. Restart the dashboard after changing `.env`. The local API remains bound to `127.0.0.1`; it is not exposed to the public internet. When Chrome asks to let the Vercel site access your local network, allow it. The page only accepts loopback API addresses such as `http://127.0.0.1:8000` or `http://localhost:8000`.

### 3. Start local processing

Install FFmpeg/FFprobe, install the Python requirements, and start Ollama with the models configured in `.env`. Then run:

```powershell
python scripts/start_dashboard.py
```

Open the Vercel URL, choose **Connect to local app**, and click **Connect to local app**. Uploads are sent directly to the local API and processing starts on the local worker. Keep the terminal and computer running while jobs process. You can also use `http://127.0.0.1:8000` directly for local-only access.

The zero-cost hybrid is a single-user local installation; it does not provide public cloud accounts or shared server-side data. The 10-minute maximum video duration remains enforced by FFprobe on your computer. Cloud model APIs are optional and can incur provider charges; Ollama remains the no-provider-bill option. Vercel Hobby has usage limits and is intended for personal projects; check its current [Hobby plan terms](https://vercel.com/docs/plans/hobby).

## Tests

```powershell
python -m pytest -v
```

## Local files and privacy

The following items are local runtime data and are intentionally ignored by Git: video footage, exports, thumbnails, keyframes, transcripts, audio caches, creator permission documents, logs, the SQLite database, virtual environments, and `.env` credentials. Do not commit private creator media or agreements.

## Project layout

```text
configs/                 Optional local configuration
data/                    Local database, agreements, footage, caches, and exports (gitignored)
scripts/                 Database, creator registration, pipeline, and dashboard entry points
src/core/                 Settings, database models, migrations, account/quota helpers, storage, and logging
src/ingestion/            Media inspection, audio extraction, rights checks
src/signals/               Audio, transcription, and scene analysis
src/fusion/                Candidate highlight matching
src/reasoning/             Ollama and OpenAI-compatible model clients
src/composer/              Vertical framing, subtitles, and rendering
src/qa/                    Export validation
src/dashboard/             FastAPI application, local worker, and browser UI
tests/                     Automated tests
```
