# transcribe.py
import os
import subprocess
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Optional: Gemini (Google) API use. keep safe guard.
try:
    import google.generativeai as genai
except Exception:
    genai = None

load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
CHUNK_DIR = BASE_DIR / "chunks"
OUTPUT_DIR = BASE_DIR / "transcripts"
ARTICLES_DIR = BASE_DIR / "articles"
THUMBS_DIR = BASE_DIR / "uploads" / "thumbnails"   # thumbnails saved here
YTDLP_DIR = BASE_DIR / "yt_downloads"
WHISPER_CLI = str(BASE_DIR / "whisper.cpp" / "build" / "bin" / "whisper-cli")
MODEL_PATH = str(BASE_DIR / "whisper.cpp" / "models" / "ggml-base.en.bin")

# ensure directories exist
for d in (CHUNK_DIR, OUTPUT_DIR, ARTICLES_DIR, THUMBS_DIR, YTDLP_DIR):
    d.mkdir(parents=True, exist_ok=True)


def emit_progress(emit_fn, task_id, stage, message, percent, text_chunk=None, download_url=None, article_url=None):
    """Helper wrapper to call GUI/websocket emit function consistently."""
    try:
        emit_fn(task_id, stage, message, percent, text_chunk, download_url, article_url)
    except Exception:
        # don't crash if emit_fn fails
        pass


def construct_gemini_prompt(transcript: str, metadata: dict) -> str:
    """Return a formatted prompt for Gemini (kept same as your earlier prompt)."""
    meta_str = json.dumps(metadata, indent=2) if metadata else "No metadata available"
    return f"""You are a highly experienced technical writer...
Transcript:
{transcript}

Metadata:
{meta_str}

Generate a clean markdown article..."""
    # (Shortened here; replace with your detailed prompt if preferred)


def generate_article_with_gemini(task_id: str, transcript: str, input_file: str, emit_fn):
    """Call Gemini to generate an article. Returns (article_path, article_text) or (None, None)."""
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or genai is None:
            raise RuntimeError("Gemini API not available or GEMINI_API_KEY missing")

        genai.configure(api_key=api_key)
        metadata = {}
        # try to load associated metadata (for youtube downloads)
        meta_path = Path(input_file).with_suffix(".info.json")
        if meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = {}

        prompt = construct_gemini_prompt(transcript, metadata)
        emit_progress(emit_fn, task_id, "article", "Calling Gemini API...", 94)

        model_name = "gemini-2.5-flash"  # adjust if you prefer another
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)

        if not getattr(response, "text", None):
            raise RuntimeError("Empty response from Gemini")

        article_title = metadata.get("title", Path(input_file).stem)
        article_text = f"# {article_title}\n\n{response.text}\n\n*Generated: {datetime.utcnow().isoformat()}*"
        article_path = ARTICLES_DIR / f"article_{task_id}.md"
        article_path.write_text(article_text, encoding="utf-8")
        return str(article_path), article_text

    except Exception as e:
        emit_progress(emit_fn, task_id, "article", f"Gemini unavailable: {e}", 95)
        return None, None


def extract_thumbnail_at_10s(input_file: str, task_id: str, emit_fn):
    """
    Extract a single frame at 10s and save to THUMBS_DIR.
    Returns the thumbnail_path (str) on success, else None.
    """
    try:
        emit_progress(emit_fn, task_id, "thumbnail", "Extracting thumbnail (10s)...", 5)
        in_path = Path(input_file)
        # create thumbnail name: thumbnail_<taskid>_<originalname>.jpg
        thumbnail_name = f"thumb_{task_id}_{in_path.stem}.jpg"
        thumbnail_path = THUMBS_DIR / thumbnail_name

        # ffmpeg: seek to 10s then grab a frame
        # Important: -ss before -i is fast seek; -ss after is accurate but slower.
        cmd = [
            "ffmpeg", "-y", "-ss", "10", "-i", str(in_path),
            "-frames:v", "1", "-q:v", "2", str(thumbnail_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        emit_progress(emit_fn, task_id, "thumbnail", f"Thumbnail saved: {thumbnail_path.name}", 10)
        return str(thumbnail_path)
    except Exception as e:
        emit_progress(emit_fn, task_id, "thumbnail", f"Thumbnail extraction failed: {e}", 10)
        return None


def transcribe_chunks_with_whisper(chunks, task_id, emit_fn):
    """
    For each chunk, run whisper-cli (if available) and return the concatenated transcript text.
    """
    full_text = ""
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        percent = 20 + int((idx / max(1, total)) * 70)
        emit_progress(emit_fn, task_id, "whisper", f"Transcribing chunk {idx}/{total}...", percent)
        try:
            cmd = [WHISPER_CLI, "-m", MODEL_PATH, "-f", str(chunk)]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            chunk_text = proc.stdout.strip()
            # fallback to txt file near the chunk
            if not chunk_text:
                txt_file = chunk.with_suffix(".txt")
                if txt_file.exists():
                    chunk_text = txt_file.read_text(encoding="utf-8").strip()
            if chunk_text:
                # emit lines as they come
                for line in chunk_text.splitlines():
                    emit_progress(emit_fn, task_id, "whisper", f"Transcribing chunk {idx}/{total}...", percent, text_chunk=line)
            full_text += (chunk_text + "\n") if chunk_text else ""
        except Exception as e:
            emit_progress(emit_fn, task_id, "whisper", f"Chunk {idx} transcribe failed: {e}", percent)
    return full_text


def process_audio_task(task_id: str, input_file: str, emit_fn, user_id=None, video_id=None):
    """
    High-level pipeline:
      1. extract thumbnail at 10s
      2. convert to WAV
      3. split into 10s chunks
      4. transcribe chunks (whisper-cli)
      5. save transcript file
      6. call Gemini to produce article (optional)
      7. update Video DB record with transcript/article/thumbnail
    Returns tuple: (transcript_path, full_text, article_path, article_text, thumbnail_path)
    """
    from models import db, Video  # local import to avoid cycles

    try:
        # 0. basic logging to frontend
        emit_progress(emit_fn, task_id, "start", "Processing started", 2)

        # 1. thumbnail extraction
        thumbnail_path = extract_thumbnail_at_10s(input_file, task_id, emit_fn)

        # 2. convert to wav (16k mono recommended for whisper, but follow your preferences)
        emit_progress(emit_fn, task_id, "ffmpeg", "Converting to WAV...", 12)
        wav_path = Path(input_file).with_suffix(".wav")
        cmd_convert = [
            "ffmpeg", "-y", "-i", str(input_file),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav_path)
        ]
        subprocess.run(cmd_convert, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        emit_progress(emit_fn, task_id, "ffmpeg", "Conversion complete ✅", 15)

        # 3. remove old chunks and create new 10s segments
        for f in CHUNK_DIR.glob("chunk_*.wav"):
            try:
                f.unlink()
            except Exception:
                pass

        emit_progress(emit_fn, task_id, "split", "Splitting into 10s chunks...", 17)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path), "-f", "segment", "-segment_time", "10", "-c", "copy", str(CHUNK_DIR / "chunk_%03d.wav")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
        )

        chunks = sorted(CHUNK_DIR.glob("chunk_*.wav"))
        emit_progress(emit_fn, task_id, "split", f"Created {len(chunks)} chunks", 20)

        # 4. transcribe chunks
        full_text = transcribe_chunks_with_whisper(chunks, task_id, emit_fn)

        # 5. save full transcript
        transcript_filename = f"full_transcript_{task_id}.txt"
        transcript_path = OUTPUT_DIR / transcript_filename
        transcript_path.write_text(full_text, encoding="utf-8")
        emit_progress(emit_fn, task_id, "done", f"Transcription saved: {transcript_filename}", 90, download_url=f"/download/{video_id or task_id}")

        # 6. generate article via Gemini if available
        emit_progress(emit_fn, task_id, "article", "Generating article...", 92)
        article_path, article_text = generate_article_with_gemini(task_id, full_text, input_file, emit_fn)
        if article_path:
            emit_progress(emit_fn, task_id, "article", "Article generated", 98, article_url=f"/article/{video_id or task_id}")
        else:
            article_text = None
            article_path = None

        # 7. update DB Video record
        try:
            if video_id is not None:
                video = db.session.get(Video, int(video_id))
                if video:
                    video.transcript = full_text
                    video.transcript_path = str(transcript_path)
                    video.article = article_text
                    video.article_path = str(article_path) if article_path else None
                    video.thumbnail_path = thumbnail_path
                    db.session.commit()
                    emit_progress(emit_fn, task_id, "db", "Video updated in database", 99)
            else:
                emit_progress(emit_fn, task_id, "db", "No video_id provided; skipping DB update", 99)
        except Exception as e:
            emit_progress(emit_fn, task_id, "db", f"Failed to update DB: {e}", 99)

        # 8. cleanup chunks
        for f in CHUNK_DIR.glob("chunk_*.wav"):
            try:
                f.unlink()
            except Exception:
                pass

        emit_progress(emit_fn, task_id, "complete", "Processing complete", 100, download_url=f"/download/{video_id or task_id}", article_url=f"/article/{video_id or task_id}")
        return str(transcript_path), full_text, (str(article_path) if article_path else None), article_text, thumbnail_path

    except subprocess.CalledProcessError as cpe:
        emit_progress(emit_fn, task_id, "error", f"Subprocess failed: {cpe}", 0)
        return None, None, None, None, None
    except Exception as e:
        emit_progress(emit_fn, task_id, "error", f"Error: {e}", 0)
        return None, None, None, None, None
