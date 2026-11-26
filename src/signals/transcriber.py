"""High-speed local speech-to-text with word-level timestamps using Faster-Whisper."""

import json
import subprocess
import tempfile
import time
from pathlib import Path
import httpx
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
        from src.reasoning.provider_config import get_ai_config
        ai = get_ai_config()
        cloud_transcription = ai.get("transcription_provider") == "openai_compatible"
        model_id = ai.get("cloud_transcription_model") or "whisper-large-v3-turbo"
        cache_suffix = f"_cloud_{model_id.replace('/', '_')}_transcript.json" if cloud_transcription else "_transcript.json"
        cache_file = cache_dir / f"{stem}{cache_suffix}"

        # Check cached transcript
        if cache_file.exists() and cache_file.stat().st_size > 10:
            logger.info("Loading cached transcript from: %s", cache_file)
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return FullTranscript.model_validate(data)

        if cloud_transcription:
            return self._transcribe_cloud(audio_path, cache_file, ai, model_id)

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

    def _transcribe_cloud(
        self, audio_path: Path, cache_file: Path, ai: dict[str, str], model_id: str
    ) -> FullTranscript:
        """Transcribe via an OpenAI-compatible audio endpoint in bounded MP3 chunks."""
        api_key = ai.get("cloud_api_key", "")
        if not api_key:
            raise RuntimeError("Cloud transcription is selected, but no API key is saved in Settings.")
        base_url = ai.get("cloud_base_url", "https://api.openai.com/v1").rstrip("/")
        duration = float(sf.info(str(audio_path)).duration)
        chunk_seconds = 300
        all_segments: list[TranscriptSegment] = []
        language = "unknown"
        chunk_count = max(1, int((duration + chunk_seconds - 1) // chunk_seconds))
        logger.info("Cloud transcription with %s: %.1f seconds in %d chunks.", model_id, duration, chunk_count)

        with tempfile.TemporaryDirectory(prefix="gta_whisper_") as tmp:
            for chunk_index, start in enumerate(range(0, max(1, int(duration + 0.999)), chunk_seconds)):
                chunk_path = Path(tmp) / f"audio_{chunk_index:04d}.mp3"
                command = [
                    settings.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(start), "-t", str(chunk_seconds), "-i", str(audio_path),
                    "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(chunk_path),
                ]
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                if result.returncode or not chunk_path.exists():
                    raise RuntimeError("Could not prepare audio for cloud transcription: " + (result.stderr[-1200:].strip() or "FFmpeg failed."))

                payload = None
                for attempt in range(3):
                    try:
                        with chunk_path.open("rb") as audio_file:
                            response = httpx.post(
                                f"{base_url}/audio/transcriptions",
                                headers={"Authorization": f"Bearer {api_key}"},
                                files={"file": (chunk_path.name, audio_file, "audio/mpeg")},
                                data={
                                    "model": model_id,
                                    "response_format": "verbose_json",
                                    "timestamp_granularities[]": ["word", "segment"],
                                },
                                timeout=300.0,
                            )
                        if response.status_code == 429 or response.status_code >= 500:
                            if attempt < 2:
                                retry_after = response.headers.get("Retry-After", "")
                                delay = min(20.0, float(retry_after)) if retry_after.isdigit() else 2.0 * (attempt + 1)
                                logger.warning("Transcription provider returned HTTP %d; retrying chunk %d after %.0fs.", response.status_code, chunk_index + 1, delay)
                                time.sleep(delay)
                                continue
                        response.raise_for_status()
                        payload = response.json()
                        break
                    except httpx.HTTPStatusError as exc:
                        detail = exc.response.text[:500].replace(api_key, "[redacted]")
                        raise RuntimeError(f"Cloud transcription failed (HTTP {exc.response.status_code}): {detail}") from None
                    except httpx.RequestError as exc:
                        if attempt < 2:
                            time.sleep(2.0 * (attempt + 1))
                            continue
                        raise RuntimeError(f"Could not reach the cloud transcription provider: {type(exc).__name__}") from None

                if not isinstance(payload, dict):
                    raise RuntimeError("The transcription provider returned an invalid response.")
                language = payload.get("language") or language
                raw_segments = payload.get("segments") or []
                flat_words = payload.get("words") or []
                for raw in raw_segments:
                    seg_start = float(raw.get("start", 0))
                    seg_end = float(raw.get("end", seg_start))
                    raw_words = raw.get("words") or [
                        word for word in flat_words
                        if float(word.get("start", -1)) >= seg_start - 0.05
                        and float(word.get("start", -1)) <= seg_end + 0.05
                    ]
                    words = [
                        WordTiming(
                            word=str(word.get("word", word.get("text", ""))).strip(),
                            start=round(start + float(word.get("start", 0)), 2),
                            end=round(start + float(word.get("end", word.get("start", 0))), 2),
                            probability=1.0,
                        )
                        for word in raw_words if word.get("word", word.get("text"))
                    ]
                    all_segments.append(TranscriptSegment(
                        segment_id=len(all_segments),
                        start=round(start + seg_start, 2),
                        end=round(start + seg_end, 2),
                        text=str(raw.get("text", "")).strip(),
                        words=words,
                    ))
                logger.info("Cloud transcription chunk %d/%d complete (%d segments).", chunk_index + 1, chunk_count, len(raw_segments))

        transcript = FullTranscript(
            audio_path=str(audio_path), language=language,
            duration_seconds=round(duration, 2), segments=all_segments,
        )
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump(transcript.model_dump(), f, indent=2)
        logger.info("Cloud transcription completed: %d segments in %s.", len(all_segments), language)
        return transcript
