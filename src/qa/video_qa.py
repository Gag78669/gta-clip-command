"""Deterministic Quality Assurance (QA) verification for rendered short-form video."""

import json
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("qa.validator")
settings = get_settings()


class QAReport(BaseModel):
    """Quality assurance and compliance assessment."""
    passed: bool
    clip_path: str
    width: int
    height: int
    aspect_ratio: str
    duration_seconds: float
    integrated_lufs: float
    issues: list[str] = Field(default_factory=list)


class VideoQAAgent:
    """Verifies format compliance, audio loudness, and visual integrity."""

    def __init__(self):
        self.ffprobe = settings.ffprobe_bin
        self.ffmpeg = settings.ffmpeg_bin

    def verify_clip(self, clip_path: Path | str) -> QAReport:
        """Inspect rendered clip against platform publishing standards."""
        clip_path = Path(clip_path).resolve()
        if not clip_path.exists():
            return QAReport(
                passed=False,
                clip_path=str(clip_path),
                width=0,
                height=0,
                aspect_ratio="unknown",
                duration_seconds=0.0,
                integrated_lufs=-100.0,
                issues=["File does not exist on disk."],
            )

        issues = []

        # 1. Probe streams
        cmd = [
            self.ffprobe,
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-print_format", "json",
            str(clip_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(res.stdout) if res.stdout else {}

        format_info = data.get("format", {})
        duration = float(format_info.get("duration", 0.0))

        v_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

        if not v_stream:
            issues.append("Missing video stream.")
            width, height = 0, 0
        else:
            width = int(v_stream.get("width", 0))
            height = int(v_stream.get("height", 0))

        if not a_stream:
            issues.append("Missing audio stream.")

        # Check Dimensions & Aspect Ratio (Must be 1080x1920 or 9:16)
        if width != 1080 or height != 1920:
            issues.append(f"Invalid dimensions: {width}x{height} (expected 1080x1920).")

        # Check Duration (Between 10s and 90s)
        if duration < 10.0:
            issues.append(f"Duration too short ({duration:.1f}s, min 10s).")
        elif duration > 90.0:
            issues.append(f"Duration too long for vertical short ({duration:.1f}s, max 90s).")

        # Check File Size
        size_bytes = clip_path.stat().st_size
        if size_bytes < 500 * 1024:
            issues.append(f"File size suspiciously small ({size_bytes / 1024:.1f} KB).")

        # Check Integrated Loudness via FFmpeg ebur128 filter
        lufs_val = -14.0
        try:
            ebur_cmd = [
                self.ffmpeg,
                "-i", str(clip_path),
                "-af", "ebur128=framelog=verbose",
                "-f", "null",
                "-",
            ]
            ebur_res = subprocess.run(ebur_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # Parse Summary Integrated loudness
            for line in ebur_res.stderr.splitlines():
                if "I:" in line and "LUFS" in line:
                    parts = line.split("I:")[1].split("LUFS")[0].strip()
                    try:
                        lufs_val = float(parts)
                    except Exception:
                        pass
                    break

            if abs(lufs_val - settings.target_lufs) > 3.0:
                issues.append(f"Loudness deviation: {lufs_val:.1f} LUFS (target: {settings.target_lufs:.1f} LUFS).")
        except Exception as e:
            logger.warning("Could not compute ebur128 loudness: %s", e)

        passed = len(issues) == 0
        logger.info("QA %s for %s (Issues: %s)", "PASSED" if passed else "FAILED", clip_path.name, issues)

        return QAReport(
            passed=passed,
            clip_path=str(clip_path),
            width=width,
            height=height,
            aspect_ratio="9:16" if (width == 1080 and height == 1920) else f"{width}:{height}",
            duration_seconds=round(duration, 2),
            integrated_lufs=round(lufs_val, 1),
            issues=issues,
        )
