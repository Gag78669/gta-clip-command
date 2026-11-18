"""Video inspection and metadata extraction using ffprobe and SHA-256 hashing."""

import hashlib
import json
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from src.core.config import get_settings
from src.core.models import MediaSource, Creator
from src.core.logger import get_logger
from src.ingestion.rights_gate import RightsGate

logger = get_logger("ingestion.media_inspector")
settings = get_settings()


class VideoMetadata(BaseModel):
    """Normalized technical metadata for an ingested video file."""
    file_path: str
    duration_seconds: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    sha256_hash: str


class MediaInspector:
    """Extracts technical specs and registers authorized media sources in SQLite."""

    def __init__(self, db: Session):
        self.db = db
        self.rights_gate = RightsGate(db)

    @staticmethod
    def compute_sha256(file_path: Path, max_bytes: int = 64 * 1024 * 1024) -> str:
        """Compute SHA256 of the first 64MB of file for fast, reliable fingerprinting."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            chunk = f.read(max_bytes)
            hasher.update(chunk)
        return hasher.hexdigest()

    def probe(self, file_path: Path | str) -> VideoMetadata:
        """Run ffprobe to extract stream details and audio/video codecs."""
        file_path = Path(file_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found at: {file_path}")

        cmd = [
            settings.ffprobe_bin,
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-print_format", "json",
            str(file_path),
        ]

        logger.debug("Executing ffprobe: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

        probe_data = json.loads(result.stdout)
        format_info = probe_data.get("format", {})
        duration = float(format_info.get("duration", 0.0))

        v_stream = next(
            (s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"),
            None,
        )
        if not v_stream:
            raise ValueError(f"No video stream found in: {file_path}")

        width = int(v_stream.get("width", 1920))
        height = int(v_stream.get("height", 1080))
        v_codec = v_stream.get("codec_name", "unknown")

        # Parse FPS
        r_frame_rate = v_stream.get("r_frame_rate", "30/1")
        try:
            num, den = map(int, r_frame_rate.split("/"))
            fps = round(num / den, 2) if den != 0 else 30.0
        except Exception:
            fps = 30.0

        # Audio stream
        a_stream = next(
            (s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"),
            None,
        )
        a_codec = a_stream.get("codec_name") if a_stream else None
        a_rate = int(a_stream.get("sample_rate", 44100)) if a_stream else None
        a_channels = int(a_stream.get("channels", 2)) if a_stream else None

        sha256 = self.compute_sha256(file_path)

        return VideoMetadata(
            file_path=str(file_path),
            duration_seconds=round(duration, 2),
            width=width,
            height=height,
            fps=fps,
            video_codec=v_codec,
            audio_codec=a_codec,
            audio_sample_rate=a_rate,
            audio_channels=a_channels,
            sha256_hash=sha256,
        )

    def register_media(
        self,
        creator_id: str,
        file_path: Path | str,
        title: str,
        source_type: str = "vod",
    ) -> MediaSource:
        """Verify creator authorization, inspect media, and register in database."""
        # Enforce rights gate
        self.rights_gate.verify_creator(creator_id, strict=True)

        meta = self.probe(file_path)

        # Check for existing media by SHA-256
        existing = (
            self.db.query(MediaSource)
            .filter(MediaSource.sha256_hash == meta.sha256_hash)
            .first()
        )
        if existing:
            logger.info("Media source already registered (id: %s, title: %s)", existing.id, existing.title)
            return existing

        media = MediaSource(
            creator_id=creator_id,
            title=title,
            file_path=meta.file_path,
            source_type=source_type,
            duration_seconds=meta.duration_seconds,
            width=meta.width,
            height=meta.height,
            fps=meta.fps,
            sha256_hash=meta.sha256_hash,
        )
        self.db.add(media)
        self.db.commit()
        self.db.refresh(media)
        logger.info(
            "Registered MediaSource %s: '%s' (%.1fs, %dx%d, %.1ffps)",
            media.id,
            media.title,
            media.duration_seconds,
            media.width,
            media.height,
            media.fps,
        )
        return media
