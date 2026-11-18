"""Correlates audio peaks, action cut frequency, and dialogue into candidate highlight windows."""

from pathlib import Path
import uuid
from pydantic import BaseModel, Field
from src.signals.audio_energy import AudioSpike
from src.signals.scene_detector import SceneBoundary, ActionSequence, SceneDetector
from src.signals.transcriber import FullTranscript
from src.core.logger import get_logger

logger = get_logger("fusion.candidate_matcher")


class CandidateHighlight(BaseModel):
    """A high-probability highlight span ready for visual and LLM semantic evaluation."""
    candidate_id: str
    start_time: float = Field(description="Recommended candidate start in seconds")
    end_time: float = Field(description="Recommended candidate end in seconds")
    duration: float = Field(description="Candidate length in seconds")
    initial_excitement_score: float = Field(description="Weighted multi-modal excitement score (0-100)")
    audio_peak_dbfs: float
    scene_cut_count: int
    speech_density: float
    dialogue_excerpt: str
    keyframe_paths: list[str] = Field(default_factory=list)


class CandidateMatcher:
    """Fuses multi-modal deterministic signals into cohesive candidate clip windows."""

    def __init__(
        self,
        min_clip_duration: float = 20.0,
        max_clip_duration: float = 75.0,
        lead_in_sec: float = 6.0,
        tail_out_sec: float = 8.0,
    ):
        self.min_clip_duration = min_clip_duration
        self.max_clip_duration = max_clip_duration
        self.lead_in_sec = lead_in_sec
        self.tail_out_sec = tail_out_sec

    def fuse(
        self,
        video_path: Path | str,
        video_duration: float,
        spikes: list[AudioSpike],
        scenes: list[SceneBoundary],
        hotspots: list[ActionSequence],
        transcript: FullTranscript,
        max_candidates: int = 10,
    ) -> list[CandidateHighlight]:
        """Aggregate signals into ranked candidate highlight intervals."""
        logger.info(
            "Fusing signals: %d audio spikes, %d scenes, %d action hotspots",
            len(spikes),
            len(scenes),
            len(hotspots),
        )

        candidates: list[CandidateHighlight] = []
        detector = SceneDetector()

        anchor_intervals = []
        for s in spikes:
            anchor_intervals.append((s.start_time, s.end_time, s.score, s.peak_dbfs, "audio"))
        for h in hotspots:
            anchor_intervals.append((h.start_time, h.end_time, h.score, -20.0, "action"))

        # If no audio spikes or action cuts, fallback to a baseline gameplay interval
        if not anchor_intervals:
            logger.info("No spikes or cut hotspots detected. Generating baseline gameplay candidate window.")
            fallback_len = min(video_duration, 30.0)
            anchor_intervals.append((0.0, fallback_len, 65.0, -20.0, "baseline_gameplay"))

        # Sort by score descending
        anchor_intervals.sort(key=lambda x: x[2], reverse=True)

        used_windows = []

        for idx, (a_start, a_end, score, peak_db, origin) in enumerate(anchor_intervals):
            if len(candidates) >= max_candidates:
                break

            # Form proposed candidate window
            prop_start = max(0.0, a_start - self.lead_in_sec)
            prop_end = min(video_duration, a_end + self.tail_out_sec)

            # Enforce minimum duration
            if (prop_end - prop_start) < self.min_clip_duration:
                prop_end = min(video_duration, prop_start + self.min_clip_duration)

            # Cap maximum duration
            if (prop_end - prop_start) > self.max_clip_duration:
                prop_end = prop_start + self.max_clip_duration

            # Snap start to nearest preceding scene cut if within 3 seconds
            for sc in scenes:
                if 0 <= (prop_start - sc.start_time) <= 3.0:
                    prop_start = sc.start_time
                    break

            # Check overlap with already accepted candidate windows (avoid redundant clips)
            overlap = False
            for u_start, u_end in used_windows:
                # If overlap > 30% of interval
                inter_start = max(prop_start, u_start)
                inter_end = min(prop_end, u_end)
                if inter_end > inter_start:
                    inter_dur = inter_end - inter_start
                    if inter_dur > 0.3 * (prop_end - prop_start):
                        overlap = True
                        break

            if overlap:
                continue

            used_windows.append((prop_start, prop_end))

            # Count cuts in window
            cuts_count = sum(1 for sc in scenes if prop_start <= sc.end_time <= prop_end)

            # Extract dialogue and calculate speech density (words per second)
            words = transcript.words_in_range(prop_start, prop_end)
            dialogue_text = transcript.text_in_range(prop_start, prop_end)
            duration = prop_end - prop_start
            wps = len(words) / max(1.0, duration)
            speech_score = min(100.0, (wps / 3.0) * 100.0)

            # Composite initial excitement score:
            # 50% audio energy, 25% visual cuts, 25% dialogue density
            action_score = min(100.0, (cuts_count / 8.0) * 100.0)
            composite_score = (0.50 * score) + (0.25 * action_score) + (0.25 * speech_score)

            # Sample 3 keyframe timestamps: 15%, 50%, 85% into clip
            t_frames = [
                prop_start + 0.15 * duration,
                prop_start + 0.50 * duration,
                prop_start + 0.85 * duration,
            ]
            # Candidate IDs are database primary keys and must remain unique across jobs.
            cand_id = f"cand_{uuid.uuid4().hex[:12]}"
            keyframe_paths = [
                str(p)
                for p in detector.extract_keyframes(
                    video_path,
                    t_frames,
                    output_prefix=f"{cand_id}_frame",
                )
            ]

            cand = CandidateHighlight(
                candidate_id=cand_id,
                start_time=round(prop_start, 2),
                end_time=round(prop_end, 2),
                duration=round(duration, 2),
                initial_excitement_score=round(composite_score, 1),
                audio_peak_dbfs=round(peak_db, 1),
                scene_cut_count=cuts_count,
                speech_density=round(wps, 2),
                dialogue_excerpt=dialogue_text[:200] if dialogue_text else "[In-game SFX / Music]",
                keyframe_paths=keyframe_paths,
            )
            candidates.append(cand)

        logger.info("Generated %d non-overlapping candidate highlights", len(candidates))
        return candidates
