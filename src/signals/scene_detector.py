"""Visual scene cut boundary and action density detector using PySceneDetect."""

import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
import scenedetect
from scenedetect import detect, ContentDetector, AdaptiveDetector
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("signals.scene_detector")
settings = get_settings()


class SceneBoundary(BaseModel):
    """A continuous visual shot or scene in the video."""
    scene_index: int
    start_time: float = Field(description="Scene start in seconds")
    end_time: float = Field(description="Scene end in seconds")
    duration: float = Field(description="Scene length in seconds")


class ActionSequence(BaseModel):
    """An interval with high frequency of camera cuts indicating intense gameplay."""
    start_time: float
    end_time: float
    cut_count: int
    cuts_per_minute: float
    score: float


class SceneDetector:
    """Detects scene transitions, rapid action cuts, and samples keyframes for visual inspection."""

    def __init__(self, threshold: float = 27.0, min_scene_len_sec: float = 1.0):
        self.threshold = threshold
        self.min_scene_len_sec = min_scene_len_sec

    def detect_scenes(self, video_path: Path | str) -> list[SceneBoundary]:
        """Detect all shot boundaries in the video file."""
        video_path = Path(video_path).resolve()
        logger.info("Detecting scene cuts in: %s", video_path.name)

        detector = ContentDetector(threshold=self.threshold, min_scene_len=int(self.min_scene_len_sec * 30))
        scene_list = detect(str(video_path), detector)

        boundaries: list[SceneBoundary] = []
        for idx, (start_tc, end_tc) in enumerate(scene_list):
            s_sec = start_tc.get_seconds()
            e_sec = end_tc.get_seconds()
            boundaries.append(
                SceneBoundary(
                    scene_index=idx,
                    start_time=round(s_sec, 2),
                    end_time=round(e_sec, 2),
                    duration=round(e_sec - s_sec, 2),
                )
            )

        logger.info("Identified %d distinct visual scenes in %s", len(boundaries), video_path.name)
        return boundaries

    def find_action_hotspots(
        self,
        scenes: list[SceneBoundary],
        window_sec: float = 30.0,
        min_cuts: int = 5,
    ) -> list[ActionSequence]:
        """Find intervals with unusually high cut density indicating combat, stunts or car chases."""
        if not scenes:
            return []

        max_time = scenes[-1].end_time
        hotspots: list[ActionSequence] = []

        step_sec = 10.0
        current_t = 0.0

        while current_t + window_sec <= max_time:
            w_start = current_t
            w_end = current_t + window_sec

            # Count cuts falling within window
            cuts_in_win = sum(1 for s in scenes if w_start <= s.end_time <= w_end)
            cpm = (cuts_in_win / (window_sec / 60.0))

            if cuts_in_win >= min_cuts:
                norm_score = min(100.0, (cpm / 24.0) * 100.0)
                hotspots.append(
                    ActionSequence(
                        start_time=round(w_start, 2),
                        end_time=round(w_end, 2),
                        cut_count=cuts_in_win,
                        cuts_per_minute=round(cpm, 1),
                        score=round(norm_score, 1),
                    )
                )

            current_t += step_sec

        logger.info("Identified %d high-action visual hotspots", len(hotspots))
        return hotspots

    def extract_keyframes(
        self,
        video_path: Path | str,
        timestamps: list[float],
        output_prefix: str = "frame",
    ) -> list[Path]:
        """Extract exact keyframes at specified timestamps using FFmpeg fast seek."""
        video_path = Path(video_path).resolve()
        cache_dir = settings.cache_dir / "keyframes"
        cache_dir.mkdir(parents=True, exist_ok=True)

        extracted_paths: list[Path] = []
        for i, t in enumerate(timestamps):
            out_img = cache_dir / f"{output_prefix}_{i:03d}_{int(t * 100)}ms.jpg"
            if out_img.exists() and out_img.stat().st_size > 1000:
                extracted_paths.append(out_img)
                continue

            cmd = [
                settings.ffmpeg_bin,
                "-y",
                "-ss", str(t),
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "2",
                str(out_img),
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if out_img.exists():
                extracted_paths.append(out_img)

        return extracted_paths
