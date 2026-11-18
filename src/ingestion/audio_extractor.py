"""Fast audio stream extraction and normalization using FFmpeg."""

import subprocess
from pathlib import Path
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("ingestion.audio_extractor")
settings = get_settings()


class AudioExtractionResult:
    """Paths to extracted audio representations."""
    def __init__(self, whisper_wav: Path, analysis_wav: Path):
        self.whisper_wav = whisper_wav
        self.analysis_wav = analysis_wav


class AudioExtractor:
    """Extracts synchronized audio tracks from video containers for speech recognition and energy analysis."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or (settings.cache_dir / "audio")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def extract_whisper_audio(self, video_path: Path | str, output_name: str | None = None) -> Path:
        """Extract a 16kHz mono 16-bit PCM WAV stream optimal for Faster-Whisper."""
        video_path = Path(video_path).resolve()
        stem = output_name or video_path.stem
        out_wav = self.cache_dir / f"{stem}_16k_mono.wav"

        # If already cached and size > 0, return
        if out_wav.exists() and out_wav.stat().st_size > 44:
            logger.info("Using cached Whisper audio: %s", out_wav)
            return out_wav

        logger.info("Extracting 16kHz mono audio for Whisper from: %s", video_path.name)
        cmd = [
            settings.ffmpeg_bin,
            "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            str(out_wav),
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            logger.error("FFmpeg audio extraction failed:\n%s", result.stderr)
            raise RuntimeError(f"FFmpeg failed with exit code {result.returncode}")

        logger.info("Whisper audio extracted successfully (%.2f MB): %s", out_wav.stat().st_size / (1024 * 1024), out_wav)
        return out_wav

    def extract_analysis_audio(self, video_path: Path | str, output_name: str | None = None) -> Path:
        """Extract high-resolution stereo WAV for loudness and energy spike analysis."""
        video_path = Path(video_path).resolve()
        stem = output_name or video_path.stem
        out_wav = self.cache_dir / f"{stem}_analysis.wav"

        if out_wav.exists() and out_wav.stat().st_size > 44:
            logger.info("Using cached analysis audio: %s", out_wav)
            return out_wav

        logger.info("Extracting high-resolution analysis audio from: %s", video_path.name)
        cmd = [
            settings.ffmpeg_bin,
            "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "2",
            str(out_wav),
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            logger.error("FFmpeg audio extraction failed:\n%s", result.stderr)
            raise RuntimeError(f"FFmpeg failed with exit code {result.returncode}")

        return out_wav

    def extract_both(self, video_path: Path | str, output_name: str | None = None) -> AudioExtractionResult:
        """Convenience method to extract both Whisper and analysis audio streams."""
        w_wav = self.extract_whisper_audio(video_path, output_name=output_name)
        a_wav = self.extract_analysis_audio(video_path, output_name=output_name)
        return AudioExtractionResult(whisper_wav=w_wav, analysis_wav=a_wav)
