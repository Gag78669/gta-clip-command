"""Narrative reasoning, viral scoring, and hook generation using local Qwen3:8B."""

from pydantic import BaseModel, Field
from src.core.config import get_settings
from src.core.logger import get_logger
from src.reasoning.ollama_client import OllamaClient
from src.reasoning.provider_config import get_ai_config
from src.reasoning.vision_agent import VisualTags
from src.fusion.candidate_matcher import CandidateHighlight

logger = get_logger("reasoning.narrative")
settings = get_settings()


class RankedClipSpec(BaseModel):
    """Complete creative and technical specification for an approved or ranked vertical clip."""
    candidate_id: str
    title: str = Field(description="High-CTR short-form title")
    hook_text: str = Field(description="Viral on-screen hook caption for the first 3 seconds")
    optimal_start: float = Field(description="Refined start timestamp in seconds")
    optimal_end: float = Field(description="Refined end timestamp in seconds")
    duration: float = Field(description="Total clip duration")
    virality_score: int = Field(description="Predicted retention and shareability from 0 to 100")
    humor_score: int = Field(description="Comedy rating from 0 to 100")
    action_score: int = Field(description="Gameplay intensity rating from 0 to 100")
    layout_mode: str = Field(default="fit_blur", description="podcast_split, stacked, fit_blur, action_crop, or blurred_pillar")
    subtitle_style: str = Field(default="neon_yellow_bounce", description="Subtitle animation style")
    call_to_action: str = Field(default="Follow for more gaming moments!", description="Ending CTA")


class NarrativeAgent:
    """Evaluates multi-modal context using Qwen3:8B to score virality and author hooks."""

    def __init__(self, ollama: OllamaClient | None = None):
        self.ollama = ollama or OllamaClient()
        provider_config = get_ai_config()
        self.provider = provider_config["reasoning_provider"]
        self.model = provider_config["reasoning_model"]

    def evaluate_candidate(
        self,
        candidate: CandidateHighlight,
        visual_tags: VisualTags,
        creator_handle: str = "@Creator",
    ) -> RankedClipSpec:
        """Score candidate highlight and generate viral hooks with Qwen3:8B."""
        prompt = f"""You are a short-form video editor creating a portrait clip from landscape creator footage.
Evaluate this moment and choose a layout that keeps the visible people and important content inside the frame.

CONTEXT:
Creator: {creator_handle}
Candidate Time: {candidate.start_time:.1f}s to {candidate.end_time:.1f}s (Duration: {candidate.duration:.1f}s)
Audio Peak: {candidate.audio_peak_dbfs:.1f} dBFS (Initial Excitement: {candidate.initial_excitement_score}/100)
Visual Gameplay Events: {', '.join(visual_tags.gameplay_tags)}
Wanted Level Stars: {visual_tags.wanted_level_stars} Stars
Facecam Reaction: {visual_tags.streamer_reaction} (Webcam detected: {visual_tags.has_webcam})
Two people side by side: {visual_tags.two_person_side_by_side}
Visual Summary: {visual_tags.summary}
Dialogue / Transcript:
"{candidate.dialogue_excerpt}"

INSTRUCTIONS:
1. Determine optimal trim: Trim dead air at the beginning and snap tightly to the setup, climax, and funny punchline. Start must be >= {candidate.start_time:.1f} and end <= {candidate.end_time:.1f}. Duration should ideally be between 25s and 60s.
2. Craft a viral Title (high click-through rate, curiosity inducing, e.g. "When a 5-Star Wanted Level Goes Completely Wrong").
3. Craft a 3-6 word Hook Text to display on screen during the first 3 seconds (e.g. "DID NOT SEE THAT COMING 💀").
4. Choose layout mode to avoid losing off-center subjects or game UI:
   - "podcast_split" when two people/speakers sit on opposite left and right sides; each half is shown in its own portrait panel.
   - "stacked" when a streamer facecam and gameplay are both visible; the full gameplay frame will be preserved in the lower panel.
   - "action_crop" only when there is one clear subject centered in the landscape frame and a crop will not cut off important content.
   - "fit_blur" when unsure, when important content is near the edges, or when the whole landscape scene should remain visible.
   - "blurred_pillar" for wide scenic footage that benefits from a centered, uncropped landscape image.
5. Rate virality_score, humor_score, and action_score on a 0-100 scale.

Return ONLY a valid JSON object matching this schema:
{{
  "candidate_id": "{candidate.candidate_id}",
  "title": "string",
  "hook_text": "string (UPPERCASE with emoji)",
  "optimal_start": float,
  "optimal_end": float,
  "virality_score": int (0-100),
  "humor_score": int (0-100),
  "action_score": int (0-100),
  "layout_mode": "podcast_split" | "stacked" | "fit_blur" | "action_crop" | "blurred_pillar",
  "subtitle_style": "neon_yellow_bounce" | "clean_white" | "danger_red",
  "call_to_action": "string"
}}
"""

        try:
            logger.info("Evaluating candidate %s with reasoning model %s...", candidate.candidate_id, self.model)
            res = self.ollama.generate(
                model=self.model,
                prompt=prompt,
                json_format=True,
                temperature=settings.reasoning_temperature,
                auto_evict=True,  # Critical: free 5.2GB VRAM immediately
                provider=self.provider,
            )

            # Enforce candidate bounds
            opt_start = float(res.get("optimal_start", candidate.start_time))
            opt_end = float(res.get("optimal_end", candidate.end_time))
            opt_start = max(candidate.start_time, min(opt_start, candidate.end_time - 10.0))
            opt_end = min(candidate.end_time, max(opt_end, opt_start + 10.0))

            layout_mode = str(res.get("layout_mode", "fit_blur"))
            if visual_tags.two_person_side_by_side:
                layout_mode = "podcast_split"
            elif visual_tags.has_webcam:
                layout_mode = "stacked"
            elif layout_mode not in {"podcast_split", "stacked", "fit_blur", "action_crop", "blurred_pillar"}:
                layout_mode = "fit_blur"

            clip_spec = RankedClipSpec(
                candidate_id=candidate.candidate_id,
                title=res.get("title", f"Gaming highlight - {candidate.candidate_id}"),
                hook_text=res.get("hook_text", "WAIT FOR IT... 💀"),
                optimal_start=round(opt_start, 2),
                optimal_end=round(opt_end, 2),
                duration=round(opt_end - opt_start, 2),
                virality_score=int(res.get("virality_score", 80)),
                humor_score=int(res.get("humor_score", 75)),
                action_score=int(res.get("action_score", 85)),
                layout_mode=layout_mode,
                subtitle_style=res.get("subtitle_style", "neon_yellow_bounce"),
                call_to_action=res.get("call_to_action", "Follow for more gaming moments!"),
            )
            logger.info(
                "Candidate %s scored! Virality: %d | Title: '%s'",
                clip_spec.candidate_id,
                clip_spec.virality_score,
                clip_spec.title,
            )
            return clip_spec

        except Exception as e:
            logger.error("Narrative reasoning failed: %s. Generating heuristic fallback spec.", e)
            return RankedClipSpec(
                candidate_id=candidate.candidate_id,
                title=f"Gaming highlight #{candidate.candidate_id}",
                hook_text="HE COULD NOT BELIEVE THIS 💀",
                optimal_start=candidate.start_time,
                optimal_end=candidate.end_time,
                duration=candidate.duration,
                virality_score=int(candidate.initial_excitement_score),
                humor_score=70,
                action_score=80,
                layout_mode=("podcast_split" if visual_tags.two_person_side_by_side else "stacked" if visual_tags.has_webcam else "fit_blur"),
                subtitle_style="neon_yellow_bounce",
                call_to_action="Follow for more gaming moments!",
            )
