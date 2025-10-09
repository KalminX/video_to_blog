import uuid
from transcribe import process_audio_task

# Dummy emit function to print progress updates to terminal
def emit_fn(task_id, stage, message, percent, text_chunk=None, download_url=None, article_url=None):
    prefix = f"[{stage.upper()} {percent}%]"
    extra = ""
    if text_chunk:
        extra = f" | Text: {text_chunk[:50]}..."
    if download_url:
        extra += f" | DL: {download_url}"
    if article_url:
        extra += f" | Article: {article_url}"
    print(f"{prefix} {message}{extra}")

def main():
    # Provide path to your test audio/video file
    input_file = input("Enter path to your video/audio file: ").strip()
    task_id = str(uuid.uuid4())

    print(f"\n🌀 Starting test for: {input_file}")
    print(f"Task ID: {task_id}\n")

    transcript_path, full_text, article_path, article_text, thumbnail_path = process_audio_task(
        task_id=task_id,
        input_file=input_file,
        emit_fn=emit_fn,
        user_id=None,
        video_id=None,
    )

    print("\n=== ✅ TEST COMPLETE ===")
    print(f"Transcript saved to: {transcript_path}")
    print(f"Thumbnail saved to: {thumbnail_path}")
    if article_path:
        print(f"Article generated at: {article_path}")
    else:
        print("Article generation skipped (Gemini API not configured).")
    print("\n--- Transcript Preview ---")
    print(full_text[:500] if full_text else "[No transcript]")
    print("\n--- Article Preview ---")
    print(article_text[:500] if article_text else "[No article]")

if __name__ == "__main__":
    main()
