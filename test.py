# test.py
import sys
from pathlib import Path
from transcribe import process_audio_task  # replace with your script filename

def emit_fn(task_id, stage, message, percent, text_chunk=None):
    print(f"[{task_id}] {stage} — {percent}% — {message}")
    if text_chunk:
        print(f"Text chunk: {text_chunk[:50]}...")  # first 50 chars

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 test.py <video_file>")
        sys.exit(1)

    video_file = sys.argv[1]
    test_video = Path(video_file)
    if not test_video.exists():
        print(f"File not found: {test_video}")
        sys.exit(1)

    task_id = "video_cli"

    out_path, full_text = process_audio_task(task_id, test_video, emit_fn)

    if out_path:
        print(f"\nFull transcript saved at: {out_path}")
        print(f"Full text preview:\n{full_text[:500]}")  # show first 500 chars
    else:
        print("Transcription failed.")
