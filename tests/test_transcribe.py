import io
import os
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from transcribe import process_audio_task, extract_thumbnail, generate_article, transcribe_chunks

@pytest.fixture
def mock_emit():
    """Simple mock emit function to collect messages."""
    calls = []
    def _emit(task_id, stage, message, percent, text_chunk=None, download_url=None, article_url=None):
        calls.append({
            "task_id": task_id,
            "stage": stage,
            "message": message,
            "percent": percent,
            "text_chunk": text_chunk,
            "download_url": download_url,
            "article_url": article_url,
        })
    _emit.calls = calls
    return _emit


@pytest.fixture
def dummy_file(tmp_path):
    file_path = tmp_path / "input.mp4"
    file_path.write_text("fake data")
    return str(file_path)


def test_extract_thumbnail_success(tmp_path, mock_emit):
    """Thumbnail extraction should call ffmpeg and return path."""
    dummy_input = tmp_path / "vid.mp4"
    dummy_input.write_text("video")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = extract_thumbnail(str(dummy_input), "abc", mock_emit)
        assert result.endswith(".jpg")
        assert any("thumbnail" in c["stage"] for c in mock_emit.calls)


def test_generate_article_no_api(monkeypatch, mock_emit, tmp_path):
    """If Gemini API key is missing, should return (None, None)."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = generate_article("abc", "fake transcript", str(tmp_path / "in.mp4"), mock_emit)
    assert result == (None, None)


def test_transcribe_chunks_with_mock(mock_emit, tmp_path):
    """Should iterate over chunks and concatenate fake output."""
    chunks = []
    for i in range(2):
        p = tmp_path / f"chunk_{i}.wav"
        p.write_text("chunk")
        chunks.append(p)

    mock_run = MagicMock()
    mock_run.stdout = "hello world"
    with patch("subprocess.run", return_value=mock_run):
        text = transcribe_chunks(chunks, "task1", mock_emit)
        assert "hello world" in text
        assert any("transcribe" in c["stage"] for c in mock_emit.calls)


def test_process_audio_task_success(tmp_path, mock_emit):
    """Full happy-path test for process_audio_task with all subprocesses mocked."""
    dummy_input = tmp_path / "input.mp4"
    dummy_input.write_text("video")

    mock_run = MagicMock()
    mock_run.stdout = "chunk"
    with (
        patch("transcribe.subprocess.run", return_value=mock_run),
        patch("transcribe.generate_article", return_value=(str(tmp_path / "art.md"), "#article")),
        patch("transcribe.extract_thumbnail", return_value=str(tmp_path / "thumb.jpg")),
        patch("models.db") as mock_db,
    ):
        transcript_path, full_text, article_path, article_text, thumb = process_audio_task(
            "task123", str(dummy_input), mock_emit
        )
        # basic sanity checks
        assert os.path.exists(transcript_path)
        assert full_text is not None
        assert article_text.startswith("#")
        assert thumb.endswith(".jpg")
        assert any(c["stage"] == "complete" for c in mock_emit.calls)


def test_process_audio_task_error(monkeypatch, mock_emit, tmp_path):
    """Simulate ffmpeg crash."""
    dummy_input = tmp_path / "input.mp4"
    dummy_input.write_text("fake")

    def fail_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "ffmpeg")

    with patch("subprocess.run", side_effect=fail_run):
        result = process_audio_task("t2", str(dummy_input), mock_emit)
        assert all(x is None for x in result)
        assert any("error" in c["stage"] for c in mock_emit.calls)
