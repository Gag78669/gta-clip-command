"""Unit tests for deterministic audio energy and excitement spike detection."""

import numpy as np
import soundfile as sf
from src.signals.audio_energy import AudioEnergyAnalyzer


def test_audio_energy_spike_detection(tmp_path):
    samplerate = 16000
    duration_sec = 10.0
    total_samples = int(samplerate * duration_sec)

    # Create background quiet audio (-35 dBFS approx)
    t = np.linspace(0, duration_sec, total_samples, endpoint=False)
    audio = 0.02 * np.sin(2 * np.pi * 440 * t)

    # Inject a loud shout / explosion at t = 4.0 to 6.0 (-3 dBFS)
    spike_start = int(4.0 * samplerate)
    spike_end = int(6.0 * samplerate)
    audio[spike_start:spike_end] = 0.7 * np.sin(2 * np.pi * 880 * t[spike_start:spike_end])

    test_wav = tmp_path / "test_shout.wav"
    sf.write(str(test_wav), audio, samplerate)

    analyzer = AudioEnergyAnalyzer(window_size_sec=0.5, hop_size_sec=0.25)
    spikes = analyzer.analyze(test_wav, z_threshold=1.5, min_peak_dbfs=-20.0)

    assert len(spikes) >= 1
    spike = spikes[0]
    # The spike should cover our injected window around 4.0 - 6.0 seconds
    assert spike.start_time <= 4.5
    assert spike.end_time >= 5.5
    assert spike.peak_dbfs > -10.0
    assert spike.score > 50.0
