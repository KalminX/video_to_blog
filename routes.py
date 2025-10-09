# routes.py
import json
import os
import uuid
import subprocess
from pathlib import Path
from flask import render_template, jsonify, request, send_file, url_for, redirect
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from markdown2 import markdown
from models import db, Video

def get_unique_filename(directory: Path, filename: str) -> Path:
    """
    Ensure filename is unique in the directory by appending a UUID if needed.
    Returns a Path object inside the given directory.
    """
    path = directory / filename
    if path.exists():
        stem, ext = path.stem, path.suffix
        path = directory / f"{stem}_{uuid.uuid4().hex}{ext}"  # <- Path, not str
    return path


def extract_metadata_ffmpeg(file_path: str) -> dict:
    """
    Extract metadata using ffprobe (part of ffmpeg).
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

def init_routes(app, socketio, UPLOAD_DIR: Path, ALLOWED_EXTENSIONS: set, BASE_DIR: Path, _background_transcribe):
    # ensure upload dir exists
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    YTDLP_DIR = BASE_DIR / "yt_downloads"
    YTDLP_DIR.mkdir(parents=True, exist_ok=True)

    def allowed_file(filename: str) -> bool:
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

    @app.route("/upload", methods=["GET"])
    @login_required
    def upload():
        return render_template("upload.html", user=current_user)

    @app.route("/upload", methods=["POST"])
    @login_required
    def upload_post():
        try:
            file = request.files.get("file")
            if not file or not allowed_file(file.filename):
                return jsonify({"error": "Invalid or missing file"}), 400

            # Temporarily save file
            temp_name = f"temp_{uuid.uuid4().hex}_{secure_filename(file.filename)}"
            temp_path = UPLOAD_DIR / temp_name
            file.save(temp_path)

            # Extract metadata
            metadata = extract_metadata_ffmpeg(str(temp_path))

            # Use filename from metadata if available
            metadata_filename = metadata.get("filename")
            final_filename = Path(metadata_filename).name if metadata_filename else secure_filename(file.filename)
            save_path = Path(get_unique_filename(UPLOAD_DIR, final_filename))  # Ensure Path object

            # Rename temp file
            temp_path.rename(save_path)

            # Create DB record
            video = Video(
                filename=save_path.name,
                filepath=str(save_path),
                user_id=current_user.id,
                video_metadata=json.dumps(metadata)
            )
            db.session.add(video)
            db.session.commit()

            # Start background task
            task_id = str(uuid.uuid4())
            sid = getattr(request, 'sid', None)
            socketio.start_background_task(_background_transcribe, task_id, str(save_path), video.id, sid, metadata=metadata)

            return jsonify({"task_id": task_id, "video_id": video.id, "message": "Upload & transcription started"}), 202

        except Exception as e:
            return jsonify({"error": str(e)}), 500



    @app.route("/upload-youtube", methods=["POST"])
    @login_required
    def upload_youtube():
        try:
            url = request.form.get("youtube_url")
            if not url:
                return jsonify({"error": "Missing YouTube URL"}), 400

            video_uid = uuid.uuid4().hex
            video_dir = YTDLP_DIR / video_uid
            video_dir.mkdir(parents=True, exist_ok=True)

            # Download video + info JSON
            ytdlp_cmd = [
                "yt-dlp",
                "-f", "bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "--write-info-json",
                "-o", str(video_dir / "%(title)s.%(ext)s"),
                url
            ]
            res = subprocess.run(ytdlp_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                return jsonify({"error": f"yt-dlp failed: {res.stderr}"}), 500

            # Get downloaded files
            video_files = sorted(video_dir.glob("*.mp4"))
            json_files = sorted(video_dir.glob("*.info.json")) + sorted(video_dir.glob("*.json"))
            if not video_files or not json_files:
                return jsonify({"error": "Video or metadata JSON not found"}), 500

            # Load metadata JSON
            with open(json_files[0], "r", encoding="utf-8") as f:
                data = json.load(f)

            # Extract relevant info
            relevant = {
                "title": data.get("title"),
                "fulltitle": data.get("fulltitle"),
                "description": data.get("description"),
                "channel": data.get("channel"),
                "channel_url": data.get("channel_url"),
                "channel_is_verified": data.get("channel_is_verified"),
                "upload_date": data.get("upload_date"),
                "duration": data.get("duration"),
                "view_count": data.get("view_count"),
                "like_count": data.get("like_count"),
                "thumbnail": data.get("thumbnail"),
                "categories": data.get("categories", []),
                "tags": data.get("tags", []),
                "webpage_url": data.get("webpage_url")
            }

            # Use sanitized title as filename
            title_safe = secure_filename(relevant.get("title") or f"yt_{video_uid}")
            final_video_path = get_unique_filename(video_dir, f"{title_safe}.mp4")
            final_json_path = final_video_path.with_suffix(".json")

            # Rename files
            video_files[0].rename(final_video_path)
            json_files[0].rename(final_json_path)

            # Create DB record
            video = Video(
                filename=final_video_path.name,
                filepath=str(final_video_path),
                user_id=current_user.id,
                video_metadata=json.dumps(relevant)
            )
            db.session.add(video)
            db.session.commit()

            # Start background task
            task_id = str(uuid.uuid4())
            sid = getattr(request, "sid", None)
            socketio.start_background_task(
                _background_transcribe,
                task_id,
                str(final_video_path),
                video.id,
                sid,
                metadata=relevant
            )

            return jsonify({
                "task_id": task_id,
                "video_id": video.id,
                "message": "YouTube download & transcription started"
            }), 202

        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/dashboard")
    @login_required
    def dashboard():
        videos = Video.query.filter_by(user_id=current_user.id).order_by(Video.created_at.desc()).all()
        
        # Add just the thumbnail filename for the template
        for v in videos:
            if v.thumbnail_path:
                v.thumb_filename = os.path.basename(v.thumbnail_path)
            else:
                v.thumb_filename = None

        return render_template("dashboard.html", videos=videos)


    @app.route("/download/<int:video_id>")
    @login_required
    def download(video_id):
        video = db.session.get(Video, video_id)
        if not video or not video.transcript_path or not Path(video.transcript_path).exists():
            return jsonify({"error": "Transcript not found"}), 404
        return send_file(video.transcript_path, as_attachment=True, download_name=Path(video.transcript_path).name)

    @app.route("/download-article/<int:video_id>")
    @login_required
    def download_article(video_id):
        video = db.session.get(Video, video_id)
        if not video or not video.article_path or not Path(video.article_path).exists():
            return jsonify({"error": "Article not found"}), 404
        return send_file(video.article_path, as_attachment=True, download_name=Path(video.article_path).name)

    @app.route("/article/<int:video_id>")
    @login_required
    def article(video_id):
        video = db.session.get(Video, video_id)
        if not video or not video.article_path or not Path(video.article_path).exists():
            return jsonify({"error": "Article not found"}), 404

        article_md = Path(video.article_path).read_text(encoding="utf-8")
        article_html = markdown(article_md)
        return render_template("article.html", article_html=article_html, video=video)

    @app.route("/thumbnails/<path:filename>")
    def thumbnails(filename):
        thumb_path = BASE_DIR / "uploads" / "thumbnails" / filename
        print("Serving thumbnail:", thumb_path, "Exists?", thumb_path.exists())
        if thumb_path.exists():
            return send_file(thumb_path)
        return "", 404


    @app.route("/favicon.ico")
    def favicon():
        favicon_path = BASE_DIR / "static" / "favicon.ico"
        if favicon_path.exists():
            return send_file(favicon_path)
        return "", 204
