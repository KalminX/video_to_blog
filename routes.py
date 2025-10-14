# routes.py
import os
import json
import uuid
import hmac
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Set

import requests
from werkzeug.utils import secure_filename
from markdown2 import markdown
from flask import (
    render_template, request, jsonify, send_file, redirect, url_for, current_app
)
from flask_login import login_required, current_user

from models import db, Video, Payment, User  # your models file

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Paystack config from env
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_BASE_URL = "https://api.paystack.co"
PAYSTACK_WEBHOOK_SECRET = os.getenv("PAYSTACK_WEBHOOK_SECRET", PAYSTACK_SECRET_KEY)  # can be same as secret key

# Example price-per-credit in NGN (update to your desired rate)
PRICE_PER_CREDIT_NGN = int(os.getenv("PRICE_PER_CREDIT_NGN", "300"))


# ---------- Helpers ----------
def json_error(message, code=400, **extra):
    payload = {"error": message, **extra}
    return jsonify(payload), code


def json_success(message=None, **data):
    if message:
        data["message"] = message
    return jsonify(data), 200


def allowed_file(filename: str, allowed_exts: Set[str]) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_exts


def get_unique_filename(directory: Path, filename: str) -> Path:
    path = directory / filename
    if path.exists():
        stem, ext = path.stem, path.suffix
        path = directory / f"{stem}_{uuid.uuid4().hex}{ext}"
    return path


def extract_metadata_ffmpeg(file_path: str) -> dict:
    """Extract metadata using ffprobe (part of ffmpeg)."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", file_path
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
        "size": int(fmt.get("size", 0) or 0),
        "bit_rate": int(fmt.get("bit_rate", 0)) if fmt.get("bit_rate") else None,
        "video_codec": video_stream.get("codec_name"),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps": eval(video_stream.get("r_frame_rate", "0")) if video_stream.get("r_frame_rate") else None,
        "audio_codec": audio_stream.get("codec_name"),
        "channels": audio_stream.get("channels"),
        "sample_rate": audio_stream.get("sample_rate")
    }


def verify_paystack_signature(raw_body: bytes, header_signature: str, secret: str) -> bool:
    """Verify Paystack webhook signature using HMAC SHA512."""
    if not header_signature:
        return False
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, header_signature)


# ---------- Main route initializer ----------
def init_routes(app, socketio, UPLOAD_DIR: Path, ALLOWED_EXTENSIONS: Set[str], BASE_DIR: Path, _background_transcribe):
    # Ensure directories exist
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    YTDLP_DIR = BASE_DIR / "yt_downloads"
    YTDLP_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- Landing ----------
    @app.route("/")
    def landing_page():
        return render_template("landing.html")

    # ---------- Upload / Transcription ----------
    @app.route("/upload", methods=["GET"])
    @login_required
    def upload_page():
        return render_template("upload.html", user=current_user)

    @app.route("/upload", methods=["POST"])
    @login_required
    def upload():
        try:
            file = request.files.get("file")
            if not file or not allowed_file(file.filename, ALLOWED_EXTENSIONS):
                return json_error("Invalid or missing file", 400)

            temp_name = f"temp_{uuid.uuid4().hex}_{secure_filename(file.filename)}"
            temp_path = UPLOAD_DIR / temp_name
            file.save(temp_path)

            try:
                metadata = extract_metadata_ffmpeg(str(temp_path))
            except Exception as e:
                temp_path.unlink(missing_ok=True)
                error_id = uuid.uuid4().hex
                logger.error(f"[{error_id}] ffprobe error: {e}", exc_info=True)
                return json_error("Failed to process video metadata", 500, error_id=error_id)

            duration_minutes = metadata.get("duration", 0) / 60
            if not current_user.has_enough_credits(duration_minutes):
                temp_path.unlink(missing_ok=True)
                return jsonify({
                    "error": "Not enough credits",
                    "available_credits": current_user.credits,
                    "required_credits": round(duration_minutes, 2)
                }), 403

            # Deduct credits and persist (assumes current_user.deduct_credits exists)
            try:
                current_user.deduct_credits(duration_minutes)
                db.session.commit()
            except Exception:
                # fallback: adjust numeric field
                current_user.credits -= duration_minutes
                db.session.commit()

            final_filename = Path(metadata.get("filename") or file.filename).name
            save_path = get_unique_filename(UPLOAD_DIR, secure_filename(final_filename))
            temp_path.rename(save_path)

            video = Video(
                filename=save_path.name,
                filepath=str(save_path),
                duration=duration_minutes,
                user_id=current_user.id,
                video_metadata=json.dumps(metadata)
            )
            db.session.add(video)
            db.session.commit()

            task_id = str(uuid.uuid4())
            sid = getattr(request, "sid", None)
            socketio.start_background_task(
                _background_transcribe, task_id, str(save_path), video.id, sid, metadata=metadata
            )

            return jsonify({
                "task_id": task_id,
                "video_id": video.id,
                "message": "Upload successful, transcription started",
                "remaining_credits": current_user.credits
            }), 202

        except Exception as e:
            error_id = uuid.uuid4().hex
            logger.exception(f"Unexpected error [{error_id}]: {e}")
            return json_error("An unexpected error occurred", 500, error_id=error_id)

    # ---------- Upload YouTube by URL (metadata then download) ----------
    @app.route("/upload-youtube", methods=["POST"])
    @login_required
    def upload_youtube():
        try:
            url = request.form.get("youtube_url")
            if not url:
                return json_error("Missing YouTube URL", 400)

            video_uid = uuid.uuid4().hex
            video_dir = YTDLP_DIR / video_uid
            video_dir.mkdir(parents=True, exist_ok=True)

            # Fetch metadata only
            ytdlp_info_cmd = [
                "yt-dlp", "--skip-download", "--write-info-json",
                "-o", str(video_dir / "%(title)s.%(ext)s"), url
            ]
            res = subprocess.run(ytdlp_info_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                # cleanup
                for f in video_dir.glob("*"):
                    f.unlink(missing_ok=True)
                video_dir.rmdir()
                if "Failed to resolve" in res.stderr or "HTTP Error" in res.stderr:
                    user_error = "Network error: unable to reach YouTube. Please check your connection and try again."
                else:
                    user_error = "YouTube metadata fetch failed. Please check the URL or try again later."
                return json_error(user_error, 500)

            json_files = sorted(video_dir.glob("*.info.json")) + sorted(video_dir.glob("*.json"))
            if not json_files:
                for f in video_dir.glob("*"):
                    f.unlink(missing_ok=True)
                video_dir.rmdir()
                return json_error("Failed to retrieve video metadata", 500)

            with open(json_files[0], "r", encoding="utf-8") as f:
                data = json.load(f)

            relevant = {
                "title": data.get("title"),
                "description": data.get("description"),
                "channel": data.get("channel"),
                "duration": data.get("duration"),
                "view_count": data.get("view_count"),
                "thumbnail": data.get("thumbnail"),
                "webpage_url": data.get("webpage_url")
            }

            duration_minutes = (relevant.get("duration") or 0) / 60

            if not current_user.has_enough_credits(duration_minutes):
                # cleanup
                for f in video_dir.glob("*"):
                    f.unlink(missing_ok=True)
                video_dir.rmdir()
                return jsonify({
                    "error": "Not enough credits",
                    "available_credits": current_user.credits,
                    "required_credits": round(duration_minutes, 2)
                }), 403

            # Deduct credits upfront
            try:
                current_user.deduct_credits(duration_minutes)
                db.session.commit()
            except Exception:
                current_user.credits -= duration_minutes
                db.session.commit()

            # Now download
            ytdlp_download_cmd = [
                "yt-dlp", "-f", "bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "-o", str(video_dir / "%(title)s.%(ext)s"), url
            ]
            res = subprocess.run(ytdlp_download_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                # refund credits
                try:
                    current_user.add_credits(duration_minutes)
                except Exception:
                    current_user.credits += duration_minutes
                db.session.commit()
                for f in video_dir.glob("*"):
                    f.unlink(missing_ok=True)
                video_dir.rmdir()
                if "Failed to resolve" in res.stderr or "HTTP Error" in res.stderr:
                    user_error = "Network error: unable to reach YouTube. Please check your connection and try again."
                else:
                    user_error = "YouTube download failed. Please check the URL or try again later."
                return json_error(user_error, 500)

            video_files = sorted(video_dir.glob("*.mp4"))
            if not video_files:
                try:
                    current_user.add_credits(duration_minutes)
                except Exception:
                    current_user.credits += duration_minutes
                db.session.commit()
                for f in video_dir.glob("*"):
                    f.unlink(missing_ok=True)
                video_dir.rmdir()
                return json_error("Downloaded video not found", 500)

            title_safe = secure_filename(relevant.get("title") or f"yt_{video_uid}")
            final_video_path = get_unique_filename(video_dir, f"{title_safe}.mp4")
            video_files[0].rename(final_video_path)

            video = Video(
                filename=final_video_path.name,
                filepath=str(final_video_path),
                duration=duration_minutes,
                user_id=current_user.id,
                video_metadata=json.dumps(relevant)
            )
            db.session.add(video)
            db.session.commit()

            task_id = str(uuid.uuid4())
            sid = getattr(request, "sid", None)
            socketio.start_background_task(
                _background_transcribe, task_id, str(final_video_path), video.id, sid, metadata=relevant
            )

            return jsonify({
                "task_id": task_id,
                "video_id": video.id,
                "message": "YouTube download & transcription started",
                "remaining_credits": current_user.credits
            }), 202

        except Exception as e:
            error_id = uuid.uuid4().hex
            logger.exception(f"Unexpected error [{error_id}]: {e}")
            return json_error("An unexpected error occurred", 500, error_id=error_id)

    # ---------- Dashboard / Article / Download ----------
    @app.route("/dashboard")
    @login_required
    def dashboard():
        page = request.args.get("page", 1, type=int)
        per_page = 5
        videos = (
            Video.query
            .filter(Video.user_id == current_user.id, Video.article_path.isnot(None))
            .order_by(Video.created_at.desc())
            .paginate(page=page, per_page=per_page, error_out=False)
        )
        for v in videos.items:
            v.thumb_filename = os.path.basename(v.thumbnail_path) if v.thumbnail_path else None
        return render_template("dashboard.html", videos=videos, page=page)

    @app.route("/download/<int:video_id>")
    @login_required
    def download(video_id):
        video = db.session.get(Video, video_id)
        if not video or not video.transcript_path or not Path(video.transcript_path).exists():
            return json_error("Transcript not found", 404)
        return send_file(video.transcript_path, as_attachment=True, download_name=Path(video.transcript_path).name)

    @app.route("/download-article/<int:video_id>")
    @login_required
    def download_article(video_id):
        video = db.session.get(Video, video_id)
        if not video or not video.article_path or not Path(video.article_path).exists():
            return json_error("Article not found", 404)
        return send_file(video.article_path, as_attachment=True, download_name=Path(video.article_path).name)

    @app.route("/article/<int:video_id>")
    @login_required
    def article(video_id):
        video = db.session.get(Video, video_id)
        if not video or not video.article_path or not Path(video.article_path).exists():
            return json_error("Article not found", 404)
        article_md = Path(video.article_path).read_text(encoding="utf-8")
        article_html = markdown(article_md)
        return render_template("article.html", article_html=article_html, video=video)

    # ---------- Static / Thumbnails / Favicon ----------
    @app.route("/thumbnails/<path:filename>")
    def thumbnails(filename):
        thumb_path = BASE_DIR / "uploads" / "thumbnails" / filename
        if thumb_path.exists():
            return send_file(thumb_path)
        return "", 404

    @app.route("/favicon.ico")
    def favicon():
        favicon_path = BASE_DIR / "static" / "favicon.ico"
        if favicon_path.exists():
            return send_file(favicon_path)
        return "", 204

    # ---------- Payments: Paystack ----------
    @app.route("/buy-credits-page")
    @login_required
    def buy_credits_page():
        # Render the payment UI; your payment.html expects an endpoint name of buy_credits
        # Pass any client keys or config needed by frontend (if using inline JS)
        return render_template("payment.html", paystack_public_key=os.getenv("PAYSTACK_PUBLIC_KEY"))

    @app.route("/buy-credits", methods=["POST"])
    @login_required
    def buy_credits():
        """Initialize a Paystack transaction and return authorization URL + reference."""
        try:
            data = request.get_json() or {}
            credits = int(data.get("credits", 0))
            if credits <= 0:
                return json_error("Invalid credit amount", 400)

            # Convert credits to NGN and kobo (Paystack expects kobo)
            amount_naira = credits * PRICE_PER_CREDIT_NGN
            amount_kobo = int(amount_naira * 100)

            payload = {
                "email": current_user.email,
                "amount": amount_kobo,
                "metadata": {"credits": credits, "user_id": current_user.id},
                "callback_url": (request.host_url.rstrip("/") + url_for("verify_payment"))
            }

            headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}
            resp = requests.post(f"{PAYSTACK_BASE_URL}/transaction/initialize", json=payload, headers=headers, timeout=15)
            res = resp.json()

            if not res.get("status"):
                logger.error("Paystack init failed: %s", res)
                return json_error(res.get("message", "Failed to initialize payment"), 500)

            reference = res["data"]["reference"]

            # Save pending payment record
            payment = Payment(
                user_id=current_user.id,
                amount_cents=amount_kobo,
                credits_added=credits,
                payment_provider_id=reference,
                status="pending"
            )
            db.session.add(payment)
            db.session.commit()

            return json_success(
                "Payment initialized",
                authorization_url=res["data"]["authorization_url"],
                reference=reference
            )

        except Exception as e:
            logger.exception("Failed to initialize Paystack payment: %s", e)
            return json_error("Payment initialization failed", 500)

    @app.route("/verify-payment")
    @login_required
    def verify_payment():
        """Paystack will redirect here after payment (callback). We verify the transaction and credit the user."""
        try:
            reference = request.args.get("reference")
            if not reference:
                return json_error("Missing reference", 400)

            headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}
            resp = requests.get(f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}", headers=headers, timeout=10)
            res = resp.json()

            if not res.get("status"):
                logger.warning("Paystack verify returned not ok: %s", res)
                return render_template("payment_failed.html", message=res.get("message"))

            tx = res["data"]
            payment = Payment.query.filter_by(payment_provider_id=reference).first()

            if not payment:
                # create if missing (defensive), using metadata if present
                meta = tx.get("metadata") or {}
                credits = meta.get("credits") or getattr(payment, "credits_added", 0)
                payment = Payment(
                    user_id=meta.get("user_id", current_user.id),
                    amount_cents=int(tx.get("amount", 0)),
                    credits_added=int(credits),
                    payment_provider_id=reference,
                    status="pending"
                )
                db.session.add(payment)
                db.session.commit()

            # If transaction was successful, credit the user
            if tx.get("status") == "success":
                payment.status = "succeeded"
                # Prefer model method; fallback to numeric add
                user = User.query.get(payment.user_id)
                try:
                    user.add_credits(payment.credits_added)
                except Exception:
                    user.credits = (getattr(user, "credits", 0) or 0) + payment.credits_added
                db.session.commit()

                return render_template("payment_success.html", credits_added=payment.credits_added)

            # else failed
            payment.status = "failed"
            db.session.commit()
            return render_template("payment_failed.html", message="Payment could not be verified as successful.")

        except Exception as e:
            logger.exception("Error verifying payment: %s", e)
            return render_template("payment_failed.html", message="An error occurred while verifying payment.")

    @app.route("/webhook/paystack", methods=["POST"])
    def paystack_webhook():
        """Handle Paystack webhook events.

        Verifies signature (HMAC SHA512). Expects header 'x-paystack-signature'.
        """
        try:
            raw_body = request.get_data()  # bytes
            sig = request.headers.get("x-paystack-signature", "")
            if not verify_paystack_signature(raw_body, sig, PAYSTACK_WEBHOOK_SECRET):
                logger.warning("Invalid Paystack webhook signature")
                return "", 400

            payload = request.get_json()
            event = payload.get("event")
            data = payload.get("data", {})

            # Common event: charge.success
            if event == "charge.success" or event == "transaction.success":
                reference = data.get("reference")
                payment = Payment.query.filter_by(payment_provider_id=reference).first()
                if payment and payment.status != "succeeded":
                    payment.status = "succeeded"
                    user = User.query.get(payment.user_id)
                    try:
                        user.add_credits(payment.credits_added)
                    except Exception:
                        user.credits = (getattr(user, "credits", 0) or 0) + payment.credits_added
                    db.session.commit()
                return "", 200

            # Add handling for other events if desired
            if event == "charge.failed":
                reference = data.get("reference")
                payment = Payment.query.filter_by(payment_provider_id=reference).first()
                if payment:
                    payment.status = "failed"
                    db.session.commit()
                return "", 200

            # Default: accept
            return "", 200

        except Exception as e:
            logger.exception("Paystack webhook handling failed: %s", e)
            return "", 500

    # End init_routes
