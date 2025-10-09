# app.py
import json
import eventlet
eventlet.monkey_patch()  # Must be first

import os
from pathlib import Path
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO  # noqa: E402
from dotenv import load_dotenv
from models import db, Video
from transcribe import process_audio_task
from auth import init_auth
from routes import init_routes

# -----------------------
# Load environment variables
# -----------------------
load_dotenv()

# -----------------------
# Directories
# -----------------------
BASE_DIR = Path(__file__).parent.resolve()
UPLOAD_DIR = BASE_DIR / "uploads"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
ARTICLES_DIR = BASE_DIR / "articles"  # New directory
UPLOAD_DIR.mkdir(exist_ok=True)
TRANSCRIPTS_DIR.mkdir(exist_ok=True)
ARTICLES_DIR.mkdir(exist_ok=True)

# -----------------------
# Flask app configuration
# -----------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev_secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB limit
ALLOWED_EXTENSIONS = {"mp3", "wav", "mp4", "m4a"}

# -----------------------
# Extensions
# -----------------------
db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# -----------------------
# Auth
# -----------------------
login_manager, oauth, google = init_auth(app)

# -----------------------
# Background transcription function
# -----------------------
# -----------------------
# Background transcription function
# -----------------------
def _background_transcribe(task_id, file_path, video_db_id, sid=None, metadata=None):
    """
    Runs transcription, article generation, and thumbnail extraction in background.
    Optional `metadata` is used for YouTube videos.
    """
    with app.app_context():

        def emit_fn(task_id, stage, message, percent=0, text_chunk=None, download_url=None, article_url=None):
            payload = {
                "task_id": task_id,
                "stage": stage,
                "message": message,
                "percent": percent,
                "text_chunk": text_chunk,
                "download_url": download_url,
                "article_url": article_url
            }
            if sid:
                socketio.emit('progress', payload, to=sid)
            else:
                socketio.emit('progress', payload)
            print(f"[EMIT] Sending progress event: {payload}, SID: {sid or 'broadcast'}")

        try:
            # Run the main processing pipeline
            out_path, full_text, article_path, article_content, thumbnail_path = process_audio_task(
                task_id,
                file_path,
                emit_fn,
                video_id=video_db_id
            )

            # Update Video DB record
            video = db.session.get(Video, video_db_id)
            if video:
                video.transcript = full_text or ""
                video.transcript_path = out_path or ""
                video.article = article_content or ""
                video.article_path = article_path or ""
                video.thumbnail_path = thumbnail_path or ""
                if metadata:
                    video.video_metadata = json.dumps(metadata)
                db.session.commit()
                emit_fn(task_id, "db", "Video updated in database with transcript, article, thumbnail, and metadata", 98)

            emit_fn(
                task_id,
                "finished",
                "Transcription, article generation, and thumbnail extraction finished",
                100,
                download_url=f"/download/{video_db_id}",
                article_url=f"/article/{video_db_id}"
            )

        except Exception as e:
            emit_fn(task_id, "error", f"Transcription failed: {e}", 100)
            print(f"[ERROR] Background transcription failed: {e}")


# -----------------------
# Routes
# -----------------------
init_routes(app, socketio, UPLOAD_DIR, ALLOWED_EXTENSIONS, BASE_DIR, _background_transcribe)

# -----------------------
# Initialize DB
# -----------------------
with app.app_context():
    db.create_all()

# -----------------------
# Run app with SocketIO + Eventlet
# -----------------------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=False)