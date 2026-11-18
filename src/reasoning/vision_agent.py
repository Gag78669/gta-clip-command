"""Visual event and streamer reaction classifier using local Qwen3.5:4B vision model."""

from pathlib import Path
from PIL import Image
from pydantic import BaseModel, Field
from src.core.config import get_settings
from src.core.logger import get_logger
from src.reasoning.ollama_client import OllamaClient
from src.reasoning.provider_config import get_ai_config

logger = get_logger("reasoning.vision")
settings = get_settings()


class VisualTags(BaseModel):
    """Visual analysis results for candidate gameplay frames."""
    has_webcam: bool = Field(default=False, description="Whether creator facecam is detected")
    webcam_position: str = Field(default="top_right", description="Location: top_left, top_right, bottom_left, bottom_right, none")
    streamer_reaction: str = Field(default="neutral", description="Detected emotion: laughing, screaming, shocked, laughing_hard, neutral")
    wanted_level_stars: int = Field(default=0, description="Police wanted stars (0 to 5)")
    gameplay_tags: list[str] = Field(default_factory=list, description="Tags: police_chase, crash, shootout, stunt, explosion, sniper")
    action_intensity: int = Field(default=50, description="Visual chaos score from 0 to 100")
    summary: str = Field(default="", description="One-sentence description of visual action")


class VisionAgent:
    """Evaluates sampled keyframes with Qwen3.5:4B to verify actual gameplay events."""

    def __init__(self, ollama: OllamaClient | None = None):
        self.ollama = ollama or OllamaClient()
        provider_config = get_ai_config()
        self.provider = provider_config["vision_provider"]
        self.model = provider_config["vision_model"]

    def analyze_keyframes(self, keyframe_paths: list[str | Path]) -> VisualTags:
        """Inspect keyframes using Qwen3.5:4B and return structured visual metadata."""
        valid_paths = [Path(p) for p in keyframe_paths if Path(p).exists()]
        if not valid_paths:
            logger.warning("No valid keyframes provided for visual inspection.")
            return VisualTags(summary="No keyframes available")

        # Resize primary keyframe to max 768px for token efficiency
        primary_frame = valid_paths[0]
        resized_frame = settings.cache_dir / "keyframes" / f"resized_{primary_frame.name}"
        try:
            with Image.open(primary_frame) as img:
                img.thumbnail((768, 768))
                img.save(resized_frame, "JPEG", quality=85)
            send_frame = resized_frame
        except Exception:
            send_frame = primary_frame

        prompt = """You are an expert short-form video editor analyzing gameplay from an unspecified video game.
Identify only events and interface elements that are visible. Do not assume the game title or invent action.
Look closely at the provided screenshot and return a strictly formatted JSON object with these keys:
{
  "has_webcam": boolean (true if creator facecam box is visible on screen),
  "webcam_position": string ("top_left", "top_right", "bottom_left", "bottom_right", or "none"),
  "streamer_reaction": string ("laughing", "screaming", "shocked", "neutral"),
  "wanted_level_stars": integer (0 to 5 when a GTA wanted-level indicator is visible, otherwise 0),
  "gameplay_tags": array of short factual strings (e.g. ["chase", "crash", "explosion", "combat", "stunt", "goal_scored"]),
  "action_intensity": integer (0 to 100 representing how intense the visual action is),
  "summary": string (a concise 1-sentence description of the visual scene)
}
Return ONLY valid JSON.
"""

        try:
            logger.info("Running visual analysis on keyframe with %s...", self.model)
            res = self.ollama.generate(
                model=self.model,
                prompt=prompt,
                images=[send_frame],
                json_format=True,
                temperature=settings.vision_temperature,
                auto_evict=True,  # Critical: free 3.3GB VRAM immediately
                provider=self.provider,
            )
            tags = VisualTags.model_validate(res)
            logger.info("Visual tags extracted: %s (intensity: %d)", tags.gameplay_tags, tags.action_intensity)
            return tags
        except Exception as e:
            logger.error("Vision agent analysis failed: %s. Falling back to default tags.", e)
            return VisualTags(
                gameplay_tags=["gameplay_action"],
                action_intensity=60,
                summary="Fallback visual tags due to inference failure.",
            )
