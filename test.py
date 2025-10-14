#!/usr/bin/env python3
import json
import subprocess
from pathlib import Path

def extract_metadata_ffmpeg(file_path: str) -> dict:
    """
    Extract metadata using ffprobe.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {res.stderr}")

    info = json.loads(res.stdout)
    fmt = info.get("format", {})
    streams = info.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    return {
        "filename": fmt.get("filename"),
        "duration": float(fmt.get("duration", 0)),
        "size": int(fmt.get("size", 0)),
        "bit_rate": int(fmt.get("bit_rate", 0)) if fmt.get("bit_rate") else None,
        "video_codec": video_stream.get("codec_name"),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps": eval(video_stream.get("r_frame_rate", "0")) if video_stream.get("r_frame_rate") else None,
        "audio_codec": audio_stream.get("codec_name"),
        "channels": audio_stream.get("channels"),
        "sample_rate": audio_stream.get("sample_rate")
    }

if __name__ == "__main__":
    # === CONFIG ===
    max_duration = 70  # seconds, change as needed
    video_file = input("Enter path to video file: ").strip()
    path = Path(video_file)

    if not path.exists() or not path.is_file():
        print(f"Error: File {video_file} does not exist")
        exit(1)

    try:
        metadata = extract_metadata_ffmpeg(str(path))
        duration = metadata.get("duration", 0)

        if duration <= 0:
            print("Error: Could not determine video duration")
        elif duration > max_duration:
            print(f"Video too long: {duration:.2f}s (max allowed: {max_duration}s)")
        else:
            print(f"Video duration OK: {duration:.2f}s (max allowed: {max_duration}s)")

    except Exception as e:
        print(f"Error checking video: {e}")
