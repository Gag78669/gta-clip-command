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

## Fully hosted deployment (free-tier configuration)

The hosted setup runs the static dashboard on Vercel, the FastAPI API and its single in-process pipeline worker on a Render Free web service, PostgreSQL on Supabase Free, and source footage/exports in an S3-compatible bucket such as Cloudflare R2 Standard. The web app never connects to a user's localhost. Accounts require email verification; set `RESEND_API_KEY` and `SMTP_FROM` for the HTTPS email API (Render Free blocks outbound SMTP ports). Local Ollama remains available when running the app on a workstation; cloud inference uses an OpenAI-compatible provider such as Groq.

This configuration selects only Render's `free` service plan and creates no paid Render worker, database, or Redis service. A separate Render background worker is not available on the free compute plan, so the API service runs one pipeline job at a time. Queued jobs are stored in PostgreSQL and reloaded when the service restarts. This is a hobby deployment, not a production reliability guarantee: Render Free services sleep after 15 minutes without inbound activity, have an ephemeral filesystem, can restart, and have only 512 MB RAM. Keep the Queue page open while a job runs; long or memory-heavy videos may fail on the free instance. See [Render's free-tier limitations](https://render.com/docs/free).

Supabase Free currently includes a 500 MB database, 1 GB of storage and 5 GB egress; projects pause after one week of inactivity. Its free upload size limit is 50 MB, so it is not suitable for typical source videos. Use R2 Standard for media when its current free monthly allowance is sufficient (10 GB-month storage, 1 million Class A and 10 million Class B operations, with no egress charge). R2 requires activating the storage subscription, and usage beyond free allowances is billable. Do not add a payment method or enable paid usage if you require a strict no-charge setup; without billing, storage/deploys may stop at provider limits. Render Hobby includes 5 GB monthly bandwidth, and overages can be billed if a payment method is on file. See [Supabase Free limits](https://supabase.com/pricing), [R2 pricing](https://developers.cloudflare.com/r2/pricing/), and [Render bandwidth limits](https://render.com/docs/free).

### 1. Create the free database and media bucket

Create a Supabase Free project and save its PostgreSQL connection string. Create a Standard Cloudflare R2 bucket and an S3-compatible API token scoped only to that bucket. Configure bucket CORS to allow the exact Vercel origin, `PUT`, `GET`, `HEAD`, and the `Content-Type` header. Do not put storage credentials in Vercel or commit them to Git. Supabase's Storage can be used only for test videos under its per-file Free limit; this app's preferred direct upload path expects S3-compatible storage.

### 2. Create the Render API service

Import this GitHub repository into the Render workspace as a Blueprint. The committed [`render.yaml`](render.yaml) selects one Free Docker web service. Before applying it, check that every service shows **Free** and that no paid database, Key Value, disk, or worker is being created. The service's URL is configured as `https://gta-clip-command-api-gag78669.onrender.com` in [`vercel.json`](vercel.json); if Render assigns a different hostname, update that destination and push the change before deploying the Vercel project.

Set these Render environment values after creating the service:

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Supabase PostgreSQL connection string (`postgresql://...`) |
| `FRONTEND_ORIGIN` | Exact production Vercel origin, e.g. `https://gta-clip-command.vercel.app` |
| `SMTP_FROM` | Verified sender address from Resend |
| `RESEND_API_KEY` | Resend API key (keep secret) |
| `STORAGE_BUCKET` | R2 bucket name |
| `STORAGE_ENDPOINT_URL` | `https://<account-id>.r2.cloudflarestorage.com` |
| `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` | R2 S3 token credentials (keep secret) |
| `CLOUD_KEY_ENCRYPTION_KEY` | Fernet key generated once and preserved across deploys (command below) |

Keep `AUTH_ENABLED=true`, `DAILY_FREE_SECONDS=1200` and `MAX_VIDEO_SECONDS=600`. Do not set `REDIS_URL` for the free single-service deployment. The service uses PostgreSQL-backed job records and its local worker. The app measures video duration with FFprobe, caps a source video at 10 minutes, reserves measured duration atomically, and limits each account to 20 minutes per UTC day.

Generate the encryption key locally, then paste it directly into the Render Environment page. Do not add it to Git or Vercel:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Deploy the Vercel dashboard

Import the same GitHub repository into a Vercel Hobby project with the repository root as its Root Directory. Keep the build command and output directory from `vercel.json`; its external rewrites proxy `/api`, `/video`, and `/thumb` to the Render API, so browser sessions remain same-origin and large video files go directly between the browser and object storage. The static build detects Vercel automatically and does not show a localhost connection prompt. The Vercel Hobby plan is free for personal projects and subject to its [usage limits](https://vercel.com/docs/plans/hobby).

### 4. Verify the hosted app

Open `https://gta-clip-command-api-gag78669.onrender.com/healthz` and confirm it returns `{"status":"ok"}`. Then open the Vercel URL, register an account, verify its email, register a creator with a signed rights agreement, and upload a short test video. Configure and test the reasoning/vision provider in Settings. Review the result, approve or reject it, and download a clip. Monitor Render bandwidth and Supabase/R2 quotas; keep all provider tiers on Free and stop uploads when a free allowance is reached.

**Cost and reliability note:** Free service plans do not guarantee that every provider feature is free or that usage never exceeds included limits. The app's per-account minute quota does not cap total users, storage, provider API calls, or aggregate egress. If a provider account has a payment method and automatic overages enabled, exceeding an included allowance can charge it. For strict no-charge use, disable paid overages where possible, do not upgrade services, and check provider billing settings before applying the Blueprint. A free Render service can sleep/restart during processing, and the free memory limit may prevent large-video jobs from completing.

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
