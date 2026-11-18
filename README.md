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

## Hosted deployment: Vercel frontend and Render services

The repository includes a Vercel static frontend build and a Render Blueprint for the API, worker, PostgreSQL database, and Redis-compatible queue. The hosted configuration also needs an S3-compatible object-storage bucket (for example, AWS S3 or Cloudflare R2) and an SMTP account. Render's local filesystem is ephemeral, so source footage, generated clips, thumbnails, and rights documents are stored in object storage. Large video bytes go directly from the browser to that storage; the Vercel function proxy only handles API requests and media redirects.

### 1. Configure Render

Create a Render Blueprint from `render.yaml`. It defines the FastAPI web service, a separate background worker, PostgreSQL, and Key Value. The Blueprint generates one shared `CLOUD_KEY_ENCRYPTION_KEY`; keep it unchanged across deploys and backups. The API and worker each prompt for storage credentials, and the API prompts for SMTP credentials. Enter the same storage credentials for both services. Update `FRONTEND_ORIGIN` in the shared environment group after Vercel assigns the final hostname:

- `FRONTEND_ORIGIN`: the deployed Vercel origin, such as `https://your-project.vercel.app`.
- `CLOUD_KEY_ENCRYPTION_KEY`: the Blueprint generates this once. For manual setup, generate it with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep the same key across deploys and backups; changing it makes already-stored user keys unreadable.
- `GROQ_API_KEY`: optional platform-owned key; enter the same value for the API and worker. Leave empty to require users to provide their own key in Settings.
- `STORAGE_BUCKET`, `STORAGE_ENDPOINT_URL`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY`, and `STORAGE_REGION`: credentials for your S3-compatible bucket. Set the bucket's CORS policy to allow `PUT` from the Vercel origin, `AllowedHeaders: *`, and expose `ETag` if required by the storage provider.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM`: used to send account verification email.

The API fails startup when required hosted services are missing. The Render services install the pinned Python runtime from `.python-version` and FFmpeg/FFprobe using `apt.txt`. The Blueprint uses a 2 GB API plan and a 4 GB worker plan because media processing and local transcription still need CPU and memory. A GPU is not expected in the hosted worker; model inference uses the configured cloud provider.

### 2. Configure Vercel

Import this repository as a Vercel project, leave the project root at the repository root, and set `API_ORIGIN` to the Render API's public HTTPS URL (no trailing slash). `vercel.json` builds `frontend_dist/` and routes the API, video, and thumbnail paths through a same-origin proxy so the browser's HTTP-only account cookie remains first-party. Direct storage uploads go from the browser to the signed S3 URL and do not pass through Vercel. Configure the storage bucket's CORS `AllowedOrigins` to the final Vercel project/custom-domain origin.

### 3. Accounts, keys, and free usage

Hosted mode enables public signup. New users must verify their email before sign-in. Sessions use random tokens stored server-side as hashes and are sent in Secure, HttpOnly cookies. The database applies account ownership filters to creators, agreements, media, jobs, clips, and settings.

Each account receives 1,200 seconds of processing time per UTC day. Each source video is limited to 600 seconds, measured server-side with FFprobe. Accepted jobs reserve their measured duration immediately to prevent concurrent uploads from bypassing the allowance. The reservation is consumed once processing reaches AI/transcription work, or on successful completion; failures and cancellations before that stage release it. Failed jobs that used the AI stage remain charged and need an available allowance to retry. The account badge shows remaining minutes for the current UTC day.

Users may save their own OpenAI-compatible provider key in Settings, encrypted with the hosted Fernet key. If `GROQ_API_KEY` is set, it is available as a platform key when a user has not saved a personal key. Provider quotas are shared at the Groq account/project level, so the app's per-account time limit cannot guarantee that provider rate limits will never be reached. Requests handle transient 429 and 5xx responses with bounded retries.

### 4. Verify the deployment

After both providers report healthy, create an account, verify its email, and sign in. In Settings, select Groq (or another OpenAI-compatible endpoint), provide at least one model ID, and run **Test Cloud Connection**. The test calls `/models`, then runs minimal text and/or image inference for the chosen model IDs. Upload one short test video, start its queue job, verify it completes, and open its generated clip. Confirm a video longer than 10 minutes is rejected and that the displayed daily allowance drops by the duration of an accepted job.

Do not put API keys, database URLs, SMTP credentials, or storage credentials into the repository or Vercel static files. Configure secrets in Render's environment settings; only `API_ORIGIN` belongs in Vercel's server-side environment.

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
src/dashboard/             FastAPI application, local/hosted worker, and browser UI
api/                      Vercel same-origin API/media proxy
tests/                     Automated tests
```
