"""Unit tests for Video Quality Assurance Agent."""

from src.qa.video_qa import VideoQAAgent


def test_qa_rejects_missing_file():
    qa = VideoQAAgent()
    report = qa.verify_clip("non_existent_clip_12345.mp4")
    assert report.passed is False
    assert "File does not exist" in report.issues[0]
