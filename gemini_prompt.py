# gemini_prompt.py
import json

def build_gemini_prompt(transcript: str, metadata: dict) -> str:
    """
    Construct the prompt for Gemini API based on the transcript and optional metadata.
    Returns a formatted string.
    """
    return f"""You are a highly experienced technical writer with extensive expertise in transforming complex subjects into clear, relatable, and engaging content for diverse audiences. Your task is to analyze the transcript of a video provided below, which may include spoken dialogue, narration, interviews, or other audio content, and generate a detailed, structured, and human-centered article. The article should deeply explore the video’s core themes, emotions, and context, connecting with readers on a personal level while maintaining clarity and professionalism.

## Input Data
**Transcript**:  
{transcript}

**YouTube Metadata (if available)**:  
{json.dumps(metadata, indent=2) if metadata else "No metadata available"}

## Output Format
Generate the article in Markdown as described in instructions.
"""
