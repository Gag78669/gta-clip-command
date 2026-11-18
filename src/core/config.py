"""System configuration and hardware parameters for GTA CLIP COMMAND."""

from pathlib import Path
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for paths, local models, hardware budgets and encoding rules."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base Paths
    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent
    )
    data_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data"
    )
    raw_footage_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data" / "raw_footage"
    )
    cache_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data" / "cache"
    )
    exports_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data" / "exports"
    )
    agreements_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data" / "agreements"
    )
    configs_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "configs"
    )

    # SQLite Persistent Storage
    database_url: str = Field(
        default_factory=lambda: f"sqlite:///{Path(__file__).resolve().parent.parent.parent / 'data' / 'gta_clip.db'}"
    )
    auth_enabled: bool = False
    app_secret_key: str = ""
    api_base_url: str = ""
    groq_api_key: str = ""
    cloud_key_encryption_key: str = ""
    daily_free_seconds: int = 1200
    max_video_seconds: int = 600
    max_upload_bytes: int = 5368709120
    redis_url: str = ""
    storage_bucket: str = ""
    storage_endpoint_url: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_region: str = "auto"
    frontend_origin: str = "http://127.0.0.1:8000"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # FFmpeg Toolchain
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    # Ollama Local LLM Configuration
    ollama_base_url: str = "http://127.0.0.1:11434"
    reasoning_model: str = "qwen3:8b"
    vision_model: str = "qwen3.5:4b"
    reasoning_temperature: float = 0.3
    vision_temperature: float = 0.2
    model_keep_alive: str = "0s"  # Immediate VRAM eviction after generation

    # Faster-Whisper Settings (CPU default protects 6GB VRAM for LLMs)
    whisper_model_size: str = "small"  # 'small' or 'medium'
    whisper_device: str = "cpu"       # 'cpu' with 8 threads; zero VRAM impact
    whisper_compute_type: str = "int8"
    whisper_cpu_threads: int = 8

    # Hardware & Memory Guardrails
    max_vram_gb: float = 5.4
    system_ram_limit_gb: float = 12.0
    nvenc_preset: str = "p4"           # Medium-high quality balanced NVENC preset
    nvenc_bitrate: str = "12M"

    # Audio Compliance Targets
    target_lufs: float = -14.0         # Standard YouTube Shorts / TikTok loudness
    true_peak_dbfs: float = -1.0

    # Vertical Video Dimensions (9:16)
    target_width: int = 1080
    target_height: int = 1920
    target_fps: int = 60

    # API Server
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    def ensure_directories(self) -> None:
        """Create all required working and storage directories if they do not exist."""
        for path in [
            self.data_dir,
            self.raw_footage_dir,
            self.cache_dir,
            self.cache_dir / "audio",
            self.cache_dir / "transcripts",
            self.cache_dir / "keyframes",
            self.exports_dir,
            self.agreements_dir,
            self.configs_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    """Singleton getter for application configuration."""
    settings = Settings()
    settings.ensure_directories()
    return settings
