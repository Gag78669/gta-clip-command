"""Hardware-accelerated FFmpeg rendering using NVIDIA NVENC encoder."""

import subprocess
from pathlib import Path
from pydantic import BaseModel
from src.core.config import get_settings
from src.core.logger import get_logger
from src.composer.vertical_framer import VerticalFramer

logger = get_logger("composer.renderer")
settings = get_settings()


class RenderResult(BaseModel):
    """Rendered vertical clip artifacts and metrics."""
    output_path: str
    thumbnail_path: str
    duration_seconds: float
    file_size_mb: float
    used_nvenc: bool


class NvencRenderer:
    """Orchestrates hardware-accelerated FFmpeg encoding of 9:16 vertical clips."""

    def __init__(self):
        self.framer = VerticalFramer(settings.target_width, settings.target_height)

    def render_clip(
        self,
        video_path: Path | str,
        start_time: float,
        end_time: float,
        output_path: Path | str,
        layout_mode: str = "stacked",
        webcam_position: str = "top_right",
        subtitle_ass_path: Path | str | None = None,
        force_software_encoder: bool = False,
    ) -> RenderResult:
        """Render high-bitrate 1080x1920 vertical video with subtitles and audio normalization."""
        video_path = Path(video_path).resolve()
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path = output_path.with_suffix(".jpg")

        sub_str = str(Path(subtitle_ass_path).resolve()) if subtitle_ass_path else None
        filtergraph, v_label = self.framer.get_filtergraph(
            layout_mode=layout_mode,
            webcam_position=webcam_position,
            subtitle_ass_path=sub_str,
        )

        v_codec = "libx264" if force_software_encoder else "h264_nvenc"
        duration = end_time - start_time

        logger.info(
            "Rendering vertical clip (%.1fs): %s -> %s using %s...",
            duration,
            video_path.name,
            output_path.name,
            v_codec,
        )

        cmd = [
            settings.ffmpeg_bin,
            "-y",
            "-ss", str(start_time),
            "-to", str(end_time),
            "-i", str(video_path),
            "-filter_complex", filtergraph,
            "-map", v_label,
            "-map", "0:a?",
            "-c:v", v_codec,
        ]

        if v_codec == "h264_nvenc":
            cmd.extend([
                "-preset", settings.nvenc_preset,
                "-b:v", settings.nvenc_bitrate,
                "-maxrate", "16M",
                "-bufsize", "24M",
                "-pix_fmt", "yuv420p",
            ])
        else:
            cmd.extend([
                "-preset", "faster",
                "-crf", "19",
                "-pix_fmt", "yuv420p",
            ])

        # Normalize audio to -14 LUFS standard and encode to high-bitrate AAC
        cmd.extend([
            "-c:a", "aac",
            "-b:a", "192k",
            "-af", f"loudnorm=I={settings.target_lufs}:TP={settings.true_peak_dbfs}:LRA=11",
            "-movflags", "+faststart",
            str(output_path),
        ])

        # Execute FFmpeg render
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Fallback to software encoding if NVENC failed (e.g. driver busy or limits reached)
        if result.returncode != 0 and v_codec == "h264_nvenc":
            logger.warning(
                "NVENC render failed (exit %s): %s. Retrying with software encoder libx264...",
                result.returncode,
                result.stderr[-2000:].strip(),
            )
            return self.render_clip(
                video_path=video_path,
                start_time=start_time,
                end_time=end_time,
                output_path=output_path,
                layout_mode=layout_mode,
                webcam_position=webcam_position,
                subtitle_ass_path=subtitle_ass_path,
                force_software_encoder=True,
            )

        if result.returncode != 0:
            logger.error("FFmpeg rendering fatal error:\n%s", result.stderr)
            raise RuntimeError(f"FFmpeg render failed with code {result.returncode}")

        # Extract 9:16 high-res thumbnail at 1.5s
        thumb_seek = min(1.5, duration / 2.0)
        thumb_cmd = [
            settings.ffmpeg_bin,
            "-y",
            "-ss", str(thumb_seek),
            "-i", str(output_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(thumb_path),
        ]
        subprocess.run(thumb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        file_size_mb = round(output_path.stat().st_size / (1024 * 1024), 2)
        logger.info("Clip rendered successfully (%.2f MB): %s", file_size_mb, output_path.name)

        return RenderResult(
            output_path=str(output_path),
            thumbnail_path=str(thumb_path) if thumb_path.exists() else "",
            duration_seconds=round(duration, 2),
            file_size_mb=file_size_mb,
            used_nvenc=(v_codec == "h264_nvenc"),
        )
