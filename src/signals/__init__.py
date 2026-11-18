"""Deterministic multi-modal signal extraction modules: audio energy, speech recognition, and scene cuts."""

from src.signals.audio_energy import AudioEnergyAnalyzer, AudioSpike
from src.signals.scene_detector import SceneDetector, SceneBoundary
from src.signals.transcriber import SpeechTranscriber, TranscriptSegment, WordTiming

__all__ = [
    "AudioEnergyAnalyzer",
    "AudioSpike",
    "SceneDetector",
    "SceneBoundary",
    "SpeechTranscriber",
    "TranscriptSegment",
    "WordTiming",
]
