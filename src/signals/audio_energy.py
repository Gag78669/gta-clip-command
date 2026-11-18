"""Deterministic audio energy, peak volume, and excitement spike detection."""

from pathlib import Path
import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field
from src.core.logger import get_logger

logger = get_logger("signals.audio_energy")


class AudioSpike(BaseModel):
    """An interval of heightened audio energy indicating creator reaction or explosive in-game action."""
    start_time: float = Field(description="Interval start in seconds")
    end_time: float = Field(description="Interval end in seconds")
    peak_dbfs: float = Field(description="Peak decibel level relative to full scale")
    mean_dbfs: float = Field(description="Average loudness during the interval")
    prominence: float = Field(description="Z-score / standard deviations above baseline")
    score: float = Field(description="Normalized 0-100 excitement score")


class AudioEnergyAnalyzer:
    """Analyzes audio waveform dynamics to isolate high-reaction moments without LLM hallucination."""

    def __init__(self, window_size_sec: float = 0.5, hop_size_sec: float = 0.25):
        self.window_size_sec = window_size_sec
        self.hop_size_sec = hop_size_sec

    def analyze(
        self,
        audio_path: Path | str,
        z_threshold: float = 2.0,
        min_peak_dbfs: float = -28.0,
        merge_gap_sec: float = 4.0,
    ) -> list[AudioSpike]:
        """Detect excitement spikes in an audio file using rolling statistical thresholds."""
        audio_path = Path(audio_path).resolve()
        logger.info("Analyzing audio energy dynamics: %s", audio_path.name)

        data, samplerate = sf.read(str(audio_path), dtype="float32")
        # Convert stereo to mono if necessary
        if data.ndim > 1:
            mono = np.mean(data, axis=1)
        else:
            mono = data

        total_samples = len(mono)
        duration_sec = total_samples / samplerate
        logger.info("Audio loaded: %.2f seconds at %d Hz", duration_sec, samplerate)

        window_samples = int(self.window_size_sec * samplerate)
        hop_samples = int(self.hop_size_sec * samplerate)

        if total_samples < window_samples:
            logger.warning("Audio duration too short for window analysis.")
            return []

        # Compute RMS energy per frame
        num_frames = (total_samples - window_samples) // hop_samples + 1
        rms_values = np.zeros(num_frames, dtype=np.float32)
        timestamps = np.zeros(num_frames, dtype=np.float32)

        for i in range(num_frames):
            start = i * hop_samples
            end = start + window_samples
            frame = mono[start:end]
            rms = np.sqrt(np.mean(frame**2))
            rms_values[i] = rms
            timestamps[i] = (start + end) / 2.0 / samplerate

        # Convert to dBFS (clamp minimum to prevent log(0))
        eps = 1e-9
        dbfs_values = 20.0 * np.log10(np.maximum(rms_values, eps))

        # Baseline statistics ignoring pure silence (< -50 dBFS)
        active_mask = dbfs_values > -50.0
        if not np.any(active_mask):
            logger.warning("No active audio detected above silence floor.")
            return []

        baseline_mean = np.mean(dbfs_values[active_mask])
        baseline_std = np.std(dbfs_values[active_mask])
        if baseline_std < 1.0:
            baseline_std = 1.0

        logger.info("Audio baseline: mean = %.1f dBFS, std = %.1f dB", baseline_mean, baseline_std)

        # Detect candidate spike frames
        spike_frames = []
        for i in range(num_frames):
            db = dbfs_values[i]
            z = (db - baseline_mean) / baseline_std
            if z >= z_threshold and db >= min_peak_dbfs:
                spike_frames.append((timestamps[i], db, z))

        if not spike_frames:
            logger.info("No spikes exceeded the threshold (z=%.1f, min_db=%.1f)", z_threshold, min_peak_dbfs)
            return []

        # Merge adjacent spike frames into continuous intervals
        intervals: list[AudioSpike] = []
        curr_start = spike_frames[0][0] - (self.window_size_sec / 2)
        curr_end = spike_frames[0][0] + (self.window_size_sec / 2)
        curr_dbs = [spike_frames[0][1]]
        curr_zs = [spike_frames[0][2]]

        for t, db, z in spike_frames[1:]:
            frame_start = t - (self.window_size_sec / 2)
            frame_end = t + (self.window_size_sec / 2)

            if frame_start - curr_end <= merge_gap_sec:
                # Merge into current interval
                curr_end = frame_end
                curr_dbs.append(db)
                curr_zs.append(z)
            else:
                # Finalize previous interval
                peak_db = max(curr_dbs)
                mean_db = float(np.mean(curr_dbs))
                max_z = max(curr_zs)
                norm_score = min(100.0, max(0.0, (max_z / 3.0) * 100.0))

                intervals.append(
                    AudioSpike(
                        start_time=round(max(0.0, curr_start), 2),
                        end_time=round(min(duration_sec, curr_end), 2),
                        peak_dbfs=round(peak_db, 1),
                        mean_dbfs=round(mean_db, 1),
                        prominence=round(max_z, 2),
                        score=round(norm_score, 1),
                    )
                )

                curr_start = frame_start
                curr_end = frame_end
                curr_dbs = [db]
                curr_zs = [z]

        # Append last interval
        peak_db = max(curr_dbs)
        mean_db = float(np.mean(curr_dbs))
        max_z = max(curr_zs)
        norm_score = min(100.0, max(0.0, (max_z / 3.0) * 100.0))
        intervals.append(
            AudioSpike(
                start_time=round(max(0.0, curr_start), 2),
                end_time=round(min(duration_sec, curr_end), 2),
                peak_dbfs=round(peak_db, 1),
                mean_dbfs=round(mean_db, 1),
                prominence=round(max_z, 2),
                score=round(norm_score, 1),
            )
        )

        logger.info("Detected %d audio excitement intervals in %s", len(intervals), audio_path.name)
        return intervals
