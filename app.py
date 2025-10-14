# app.py
import json
import os
from pathlib import Path
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from flask_migrate import Migrate
from dotenv import load_dotenv
from models import db, Video
from transcribe import process_audio_task
from auth import init_auth
from routes import init_routes
import eventlet

# ---- Monkey patch early for Eventlet ----
eventlet.monkey_patch()

# ---- Load environment variables ----
load_dotenv()

# ---- Directory setup ----
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
ARTICLES_DIR = BASE_DIR / "articles"
for d in (UPLOAD_DIR, TRANSCRIPTS_DIR, ARTICLES_DIR):
    d.mkdir(exist_ok=True)

# ---- Extensions ----
socketio = SocketIO(cors_allowed_origins="*", async_mode="eventlet")
migrate = Migrate()


def create_app(test_config=None):
    """Application factory compatible with Gunicorn and Flask CLI"""
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ---- Base configuration ----
    app.secret_key = os.getenv("SECRET_KEY", "dev_secret")

    # ✅ Use absolute SQLite path to avoid issues when Gunicorn changes cwd
    db_path = BASE_DIR / "db.sqlite3"
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", f"sqlite:///{db_path}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

    # ---- Apply test configuration if provided ----
    if test_config:
        app.config.update(test_config)

    # ---- Initialize extensions ----
    db.init_app(app)
    migrate.init_app(app, db)
    socketio.init_app(app)

    # ---- Create tables automatically if not present ----
    with app.app_context():
        db.create_all()

    # ---- Initialize authentication ----
    login_manager, oauth, google = init_auth(app)

    # ---- Background transcription task ----
    def _background_transcribe(task_id, file_path, video_db_id, sid=None, metadata=None):
        with app.app_context():

            def emit_fn(task_id, stage, message, percent=0, text_chunk=None, download_url=None, article_url=None):
                payload = {
                    "task_id": task_id,
                    "stage": stage,
                    "message": message,
                    "percent": percent,
                    "text_chunk": text_chunk,
                    "download_url": download_url,
                    "article_url": article_url,
                }
                socketio.emit("progress", payload, to=sid) if sid else socketio.emit("progress", payload)
                print(f"[EMIT] Sending progress: {payload}, SID: {sid or 'broadcast'}")

            try:
                out_path, full_text, article_path, article_content, thumbnail_path = process_audio_task(
                    task_id, file_path, emit_fn, video_id=video_db_id
                )

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
                    emit_fn(task_id, "db", "Video updated in database", 98)

                emit_fn(
                    task_id,
                    "finished",
                    "Transcription and article generation finished",
                    100,
                    download_url=f"/download/{video_db_id}",
                    article_url=f"/article/{video_db_id}",
                )

            except Exception as e:
                emit_fn(task_id, "error", f"Transcription failed: {e}", 100)
                print(f"[ERROR] Background transcription failed: {e}")

    # ---- Initialize routes ----
    init_routes(app, socketio, UPLOAD_DIR, {"mp3", "wav", "mp4", "m4a"}, BASE_DIR, _background_transcribe)

    return app


# ---- Run in development only ----
if __name__ == "__main__":
    app = create_app()
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=True)
