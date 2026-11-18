# GTA CLIP COMMAND — Autonomous Local-First Gaming Clipping Workstation

**GTA CLIP COMMAND** is an AI-augmented, local-first clipping engine engineered specifically for creators in the Grand Theft Auto (GTA VI / GTA V) gaming space.

It automates the complete short-form video lifecycle—from multi-hour livestream ingestion to 9:16 vertical videos (TikTok, YouTube Shorts, Instagram Reels)—with a local-first workflow and strict hardware optimization for 6GB VRAM and 16GB system RAM. Optional hosted model providers can be configured in the dashboard and may incur provider charges.

---

## Key Capabilities

1. **Intake & Legal Rights Gate**: Strictly enforces documented creator permission before any footage is queued or processed.
2. **Deterministic Signal Extraction**:
   - **Audio Dynamics**: Short-Time Energy (STE) and rolling $Z$-score spike detection for streamer shouts, laughter, and explosions.
   - **Scene Cuts**: PySceneDetect boundary identification and rapid-cut action hotspot clustering.
   - **Speech Recognition**: Faster-Whisper on CPU (8 threads, int8) providing word-level timestamps with 0 VRAM usage.
3. **Multi-Modal Temporal Fusion**: Synthesizes audio energy, action density, and speech rates into high-probability candidate highlight intervals.
4. **Hardware-Aware Serialized Local LLMs**:
   - **Visual Verification (`Qwen3.5:4B`)**: Inspects keyframes for police wanted stars, weapon usage, vehicular stunts, and streamer facial expressions.
   - **Semantic Virality & Hook Reasoner (`Qwen3:8B`)**: Evaluates comedy timing, scores virality (0-100), writes punchy titles, and authors high-retention hook text overlays.
   - **Strict VRAM Eviction**: Models are automatically unloaded from memory immediately upon inference (`keep_alive: "0s"`), preventing Out-Of-Memory crashes on 6GB VRAM GPUs.
5. **Video Composition & Subtitle Engine**:
   - **Vertical Framing (1080x1920)**: Layouts for `stacked` facecam/gameplay, centered `action_crop`, and `blurred_pillar`.
   - **Animated ASS Subtitles**: Karaoke bounce word highlighting and viral hook card overlays.
   - **Hardware NVENC Encoding**: Fast FFmpeg GPU rendering (`h264_nvenc`) with software fallback.
   - **Audio Loudness Normalization**: EBU R128 loudness normalized to -14 LUFS.
6. **Local Human Approval Dashboard**: Sleek dark-mode FastAPI web interface for side-by-side clip ranking, interactive 9:16 phone mockup playback, timeline trim tweaking, and one-click approvals.
7. **Optional Hosted AI Models**: Configure an OpenAI-compatible provider for reasoning or vision in **Settings**. API keys are encrypted with Windows DPAPI for the current Windows account; cloud calls send transcripts and, for vision, sampled frames to that provider.

---

## System Requirements & Hardware Profile

- **Operating System**: Windows 11
- **CPU**: Intel Core i5-12450HX (8 cores / 12 threads)
- **GPU**: NVIDIA GeForce RTX 3050 Laptop GPU (6GB VRAM, CUDA 13.0)
- **RAM**: 16GB DDR5
- **Local LLM Engine**: Ollama (Port 11434) with models:
  - `qwen3:8b` (Reasoning & Viral Hooks)
  - `qwen3.5:4b` (Visual Event Detection)
- **FFmpeg**: Gyan.dev FFmpeg 9.0.2 full build (with NVENC, NVDEC, libass)
- **Python**: 3.12.14 in dedicated `.venv`

---

## Quickstart Guide

### 1. Initialize SQLite Metadata Store & Seed Creator

```powershell
.\.venv\Scripts\python.exe scripts/init_db.py
```

### 2. Process a Video End-to-End

Run the autonomous clipping pipeline on any gameplay recording:

```powershell
.\.venv\Scripts\python.exe scripts/run_pipeline.py data/raw_footage/test_gta_stream.mp4 --creator "@RockstarGamerHQ" --max-clips 3
```

### 3. Launch the Human Approval Dashboard

Start the local review server:

```powershell
.\.venv\Scripts\python.exe scripts/start_dashboard.py
```

Open your web browser and navigate to:
👉 **`http://127.0.0.1:8000`**

Use **Settings** to choose local Ollama or an OpenAI-compatible hosted provider separately for reasoning and vision. Enter the provider's API base URL and model IDs, test the connection, then save. Leave the API key blank to retain the saved key.

### 4. Execute the Test Suite

```powershell
.\.venv\Scripts\pytest.exe -v
```

---

## Directory Architecture

```
c:\Users\Gagan\Documents\GTA_CLIP_COMMAND\
├── .venv/                   # Dedicated Python 3.12 virtual environment
├── configs/                 # Pipeline settings and platform presets
├── data/                    # Persistent storage
│   ├── gta_clip.db          # SQLite WAL-mode database
│   ├── agreements/          # Signed contracts and creator permissions
│   ├── raw_footage/         # Ingested creator recordings
│   ├── cache/               # Ephemeral audio, transcripts, and keyframes
│   └── exports/             # Rendered 9:16 vertical clips and thumbnails
├── src/                     # Core system modules
│   ├── core/                # Settings, SQLite schema, and structured logging
│   ├── ingestion/           # Media inspection, audio extraction, Rights Gate
│   ├── signals/             # Audio energy, Faster-Whisper, PySceneDetect
│   ├── fusion/              # Multi-modal temporal signal alignment
│   ├── reasoning/           # Ollama client, Qwen3.5:4B vision, Qwen3:8B reasoning
│   ├── composer/            # 9:16 layout framer, animated ASS subtitles, NVENC
│   ├── qa/                  # Format, aspect ratio, and loudness QA validator
│   └── dashboard/           # FastAPI approval UI & REST endpoints
├── scripts/                 # CLI entry points
│   ├── init_db.py           # Database migration & creator seeding
│   ├── run_pipeline.py      # Master autonomous orchestrator
│   └── start_dashboard.py   # Web review server launcher
├── tests/                   # Pytest test suite (12 unit & integration tests)
└── pyproject.toml           # Toolchain configuration
```
