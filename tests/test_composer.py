"""Unit tests for subtitle generation and vertical reframing filtergraphs."""

from src.composer.subtitle_builder import SubtitleBuilder
from src.composer.vertical_framer import VerticalFramer
from src.signals.transcriber import WordTiming


def test_subtitle_builder_ass_generation():
    builder = SubtitleBuilder(1080, 1920)
    words = [
        WordTiming(word="LOOK", start=1.0, end=1.5, probability=0.98),
        WordTiming(word="AT", start=1.5, end=1.8, probability=0.99),
        WordTiming(word="THAT", start=1.8, end=2.2, probability=0.95),
        WordTiming(word="COP", start=2.2, end=2.7, probability=0.97),
    ]

    ass_content = builder.build_ass_script(
        words=words,
        clip_start_offset=1.0,
        hook_text="HE DID NOT SEE THIS COMING 💀",
    )

    assert "[Script Info]" in ass_content
    assert "[V4+ Styles]" in ass_content
    assert "PlayResX: 1080" in ass_content
    assert "PlayResY: 1920" in ass_content
    assert "HE DID NOT SEE THIS COMING 💀" in ass_content
    assert "HookCard" in ass_content
    assert "ViralCaption" in ass_content
    # Check that word bounce tags exist
    assert r"{\c&H00D7FF&\fscx108\fscy108}" in ass_content


def test_vertical_framer_stacked_filtergraph():
    framer = VerticalFramer(1080, 1920)
    graph, label = framer.get_filtergraph(layout_mode="stacked", webcam_position="top_right")

    assert "crop=480:360:1440:0" in graph
    assert "vstack=inputs=2" in graph
    assert label == "[stacked]"


def test_vertical_framer_with_subtitles():
    framer = VerticalFramer(1080, 1920)
    graph, label = framer.get_filtergraph(
        layout_mode="action_crop",
        subtitle_ass_path=r"C:\temp\subs.ass",
    )

    assert "action_crop" in graph
    assert "subtitles=" in graph
    assert label == "[v_out]"
