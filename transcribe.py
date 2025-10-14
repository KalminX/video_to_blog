# transcribe.py
import os
import subprocess
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Optional AI library
try:
    import google.generativeai as genai
except Exception:
    genai = None

load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
CHUNK_DIR = BASE_DIR / "chunks"
OUTPUT_DIR = BASE_DIR / "transcripts"
ARTICLES_DIR = BASE_DIR / "articles"
THUMBS_DIR = BASE_DIR / "uploads" / "thumbnails"
YTDLP_DIR = BASE_DIR / "yt_downloads"
WHISPER_CLI = str(BASE_DIR / "whisper.cpp" / "build" / "bin" / "whisper-cli")
MODEL_PATH = str(BASE_DIR / "whisper.cpp" / "models" / "ggml-base.en.bin")

# Ensure directories exist
for d in (CHUNK_DIR, OUTPUT_DIR, ARTICLES_DIR, THUMBS_DIR, YTDLP_DIR):
    d.mkdir(parents=True, exist_ok=True)


def emit_progress(emit_fn, task_id, stage, message, percent, text_chunk=None, download_url=None, article_url=None):
    """Send safe progress messages to frontend."""
    try:
        emit_fn(task_id, stage, message, percent, text_chunk, download_url, article_url)
    except Exception:
        pass


def construct_prompt(transcript: str, metadata: dict) -> str:
    """
        Construct the prompt for Gemini API based on the transcript and optional metadata.
        Returns a formatted string.
    """
    return f"""
                You are a highly experienced technical writer with extensive expertise
                in transforming complex subjects into clear, relatable, and engaging content
                for diverse audiences. Your task is to analyze the transcript of a video provided
                below, which may include spoken dialogue, narration, interviews, or other audio content,
                and generate a detailed, structured, and human-centered article. The article should deeply
                explore the video’s core themes, emotions, and context, connecting with readers on a personal
                level while maintaining clarity and professionalism.
                ## Input Data
                **Transcript**:  
                {transcript}

                **YouTube Metadata (if available)**:  
                {json.dumps(metadata, indent=2) if metadata else "No metadata available"}

                ## Output Format
                Generate the article in Markdown as described in instructions.
            """


def generate_article(task_id: str, transcript: str, input_file: str, emit_fn):
    """Generate a readable article from transcript."""
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or genai is None:
            raise RuntimeError("AI article generation unavailable")

        genai.configure(api_key=api_key)
        metadata = {}
        meta_path = Path(input_file).with_suffix(".info.json")
        if meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = {}

        prompt = construct_prompt(transcript, metadata)
        emit_progress(emit_fn, task_id, "article", "Polishing your transcript into an article...", 94)

        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt)

        if not getattr(response, "text", None):
            raise RuntimeError("No article generated")

        article_title = metadata.get("title", Path(input_file).stem)
        article_text = f"#{response.text}\n\n*Generated: {datetime.utcnow().isoformat()}*"
        article_path = ARTICLES_DIR / f"article_{task_id}.md"
        article_path.write_text(article_text, encoding="utf-8")
        return str(article_path), article_text

    except Exception:
        emit_progress(emit_fn, task_id, "article", "Couldn’t generate article at this time.", 95)
        return None, None


def extract_thumbnail(input_file: str, task_id: str, emit_fn):
    """Capture a thumbnail from the video."""
    try:
        emit_progress(emit_fn, task_id, "thumbnail", "Capturing a key moment from your video...", 5)
        in_path = Path(input_file)
        thumbnail_name = f"thumb_{task_id}_{in_path.stem}.jpg"
        thumbnail_path = THUMBS_DIR / thumbnail_name

        cmd = [
            "ffmpeg", "-y", "-ss", "10", "-i", str(in_path),
            "-frames:v", "1", "-q:v", "2", str(thumbnail_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        emit_progress(emit_fn, task_id, "thumbnail", "Thumbnail captured successfully.", 10)
        return str(thumbnail_path)
    except Exception:
        emit_progress(emit_fn, task_id, "thumbnail", "Couldn’t capture thumbnail — continuing...", 10)
        return None


def transcribe_chunks(chunks, task_id, emit_fn):
    """Turn audio chunks into text."""
    full_text = ""
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        percent = 20 + int((idx / max(1, total)) * 70)
        emit_progress(emit_fn, task_id, "transcribe", f"Listening to part {idx}/{total}...", percent)
        try:
            cmd = [WHISPER_CLI, "-m", MODEL_PATH, "-f", str(chunk)]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            chunk_text = proc.stdout.strip()

            if not chunk_text:
                txt_file = chunk.with_suffix(".txt")
                if txt_file.exists():
                    chunk_text = txt_file.read_text(encoding="utf-8").strip()

            if chunk_text:
                for line in chunk_text.splitlines():
                    emit_progress(emit_fn, task_id, "transcribe", f"Processing audio...", percent, text_chunk=line)
            full_text += (chunk_text + "\n") if chunk_text else ""
        except Exception:
            emit_progress(emit_fn, task_id, "transcribe", "Skipped a noisy segment.", percent)
    return full_text


def process_audio_task(task_id: str, input_file: str, emit_fn, user_id=None, video_id=None):
    """Main video-to-article pipeline."""
    from models import db, Video

    try:
        emit_progress(emit_fn, task_id, "start", "Getting things ready...", 2)

        # 1. Thumbnail
        thumbnail_path = extract_thumbnail(input_file, task_id, emit_fn)

        # 2. Convert to wav
        emit_progress(emit_fn, task_id, "convert", "Optimizing audio quality...", 12)
        wav_path = Path(input_file).with_suffix(".wav")
        subprocess.run([
            "ffmpeg", "-y", "-i", str(input_file),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav_path)
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        emit_progress(emit_fn, task_id, "convert", "Audio prepared.", 15)

        # 3. Split audio
        for f in CHUNK_DIR.glob("chunk_*.wav"):
            try: f.unlink()
            except: pass

        emit_progress(emit_fn, task_id, "split", "Breaking audio into parts...", 17)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(wav_path),
            "-f", "segment", "-segment_time", "10", "-c", "copy", str(CHUNK_DIR / "chunk_%03d.wav")
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        chunks = sorted(CHUNK_DIR.glob("chunk_*.wav"))
        emit_progress(emit_fn, task_id, "split", f"Split into {len(chunks)} short parts.", 20)

        # 4. Transcribe
        full_text = transcribe_chunks(chunks, task_id, emit_fn)

        # 5. Save transcript
        transcript_filename = f"transcript_{task_id}.txt"
        transcript_path = OUTPUT_DIR / transcript_filename
        transcript_path.write_text(full_text, encoding="utf-8")
        emit_progress(emit_fn, task_id, "done", "Transcription complete.", 90,
                      download_url=f"/download/{video_id or task_id}")

        # 6. Generate article
        emit_progress(emit_fn, task_id, "article", "Creating a readable article from your transcript...", 92)
        article_path, article_text = generate_article(task_id, full_text, input_file, emit_fn)
        if article_path:
            emit_progress(emit_fn, task_id, "article", "Your article is ready to view!", 98,
                          article_url=f"/article/{video_id or task_id}")

        # 7. Update DB
        if video_id is not None:
            try:
                video = db.session.get(Video, int(video_id))
                if video:
                    video.transcript = full_text
                    video.transcript_path = str(transcript_path)
                    video.article = article_text
                    video.article_path = str(article_path) if article_path else None
                    video.thumbnail_path = thumbnail_path
                    db.session.commit()
                    emit_progress(emit_fn, task_id, "db", "Saved your progress successfully.", 99)
            except Exception:
                emit_progress(emit_fn, task_id, "db", "Couldn’t update database, but processing finished.", 99)
        else:
            emit_progress(emit_fn, task_id, "db", "No linked video record. Skipping database save.", 99)

        # 8. Cleanup
        for f in CHUNK_DIR.glob("chunk_*.wav"):
            try: f.unlink()
            except: pass

        emit_progress(emit_fn, task_id, "complete", "✅ All done! Your transcript and article are ready.", 100,
                      download_url=f"/download/{video_id or task_id}",
                      article_url=f"/article/{video_id or task_id}")
        return str(transcript_path), full_text, str(article_path) if article_path else None, article_text, thumbnail_path

    except subprocess.CalledProcessError:
        emit_progress(emit_fn, task_id, "error", "Something went wrong while processing the video.", 0)
        return None, None, None, None, None
    except Exception:
        emit_progress(emit_fn, task_id, "error", "An unexpected error occurred during processing.", 0)
        return None, None, None, None, None
