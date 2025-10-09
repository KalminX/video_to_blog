# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
CHUNK_DIR = BASE_DIR / "chunks"
OUTPUT_DIR = BASE_DIR / "transcripts"
ARTICLES_DIR = BASE_DIR / "articles"
YTDLP_DIR = BASE_DIR / "yt_downloads"
WHISPER_CLI = str(BASE_DIR / "whisper.cpp/build/bin/whisper-cli")
MODEL_PATH = str(BASE_DIR / "whisper.cpp/models/ggml-base.en.bin")

# Ensure directories exist
for path in [CHUNK_DIR, OUTPUT_DIR, ARTICLES_DIR, YTDLP_DIR]:
    path.mkdir(parents=True, exist_ok=True)
