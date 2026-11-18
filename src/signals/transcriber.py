"""High-speed local speech-to-text with word-level timestamps using Faster-Whisper."""

import json
from pathlib import Path
import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field
from faster_whisper import WhisperModel
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("signals.transcriber")
settings = get_settings()


class WordTiming(BaseModel):
    """Single word with high-precision start and end timestamps for animated subtitles."""
    word: str
    start: float
    end: float
    probability: float


class TranscriptSegment(BaseModel):
    """Contiguous spoken sentence or phrase."""
    segment_id: int
    start: float
    end: float
    text: str
    words: list[WordTiming] = Field(default_factory=list)


class FullTranscript(BaseModel):
    """Complete video transcript with word-level timing and lexical density metrics."""
    audio_path: str
    language: str
    duration_seconds: float
    segments: list[TranscriptSegment] = Field(default_factory=list)

    def text_in_range(self, start_time: float, end_time: float) -> str:
        """Extract all dialogue spoken between start_time and end_time."""
        matched = []
        for s in self.segments:
            if s.end >= start_time and s.start <= end_time:
                matched.append(s.text.strip())
        return " ".join(matched)

    def words_in_range(self, start_time: float, end_time: float) -> list[WordTiming]:
        """Extract all word timestamps falling within an interval."""
        matched = []
        for s in self.segments:
            for w in s.words:
                if w.end >= start_time and w.start <= end_time:
                    matched.append(w)
        return matched


class SpeechTranscriber:
    """Wraps Faster-Whisper for zero-cloud, hardware-conscious speech recognition."""

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ):
        self.model_size = model_size or settings.whisper_model_size
        self.device = device or settings.whisper_device
        self.compute_type = compute_type or settings.whisper_compute_type
        self.cpu_threads = settings.whisper_cpu_threads
        self._model = None

    def _load_model(self) -> WhisperModel:
        if self._model is None:
            logger.info(
                "Loading Faster-Whisper model '%s' on %s (%s)...",
                self.model_size,
                self.device,
                self.compute_type,
            )
            self._model = WhisperModel(
                model_size_or_path=self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads,
            )
            logger.info("Faster-Whisper model loaded successfully.")
        return self._model

    def unload(self) -> None:
        """Explicitly release model from memory."""
        if self._model is not None:
            del self._model
            self._model = None
            logger.info("Faster-Whisper model unloaded from memory.")

    def transcribe(
        self,
        audio_path: Path | str,
        cache_name: str | None = None,
    ) -> FullTranscript:
        """Generate full word-level transcript from 16kHz audio."""
        audio_path = Path(audio_path).resolve()
        cache_dir = settings.cache_dir / "transcripts"
        cache_dir.mkdir(parents=True, exist_ok=True)

        stem = cache_name or audio_path.stem
        cache_file = cache_dir / f"{stem}_transcript.json"

        # Check cached transcript
        if cache_file.exists() and cache_file.stat().st_size > 10:
            logger.info("Loading cached transcript from: %s", cache_file)
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return FullTranscript.model_validate(data)

        model = self._load_model()
        logger.info("Transcribing audio: %s", audio_path.name)

        # Load audio into float32 numpy array to bypass PyAV version incompatibilities
        audio_array, sr = sf.read(str(audio_path), dtype="float32")
        if audio_array.ndim > 1:
            audio_array = np.mean(audio_array, axis=1)

        segments_generator, info = model.transcribe(
            audio_array,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        segments: list[TranscriptSegment] = []
        for i, seg in enumerate(segments_generator):
            words = [
                WordTiming(
                    word=w.word.strip(),
                    start=round(w.start, 2),
                    end=round(w.end, 2),
                    probability=round(w.probability, 2),
                )
                for w in (seg.words or [])
            ]
            segments.append(
                TranscriptSegment(
                    segment_id=i,
                    start=round(seg.start, 2),
                    end=round(seg.end, 2),
                    text=seg.text.strip(),
                    words=words,
                )
            )

        transcript = FullTranscript(
            audio_path=str(audio_path),
            language=info.language,
            duration_seconds=round(info.duration, 2),
            segments=segments,
        )

        # Cache to disk
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(transcript.model_dump(), f, indent=2)

        logger.info(
            "Transcription completed: %d segments in %s (saved to %s)",
            len(segments),
            info.language,
            cache_file.name,
        )
        return transcript
