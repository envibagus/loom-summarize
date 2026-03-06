#!/usr/bin/env python3
"""Loom Video Summarizer — download, transcribe, and summarize Loom videos."""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


# Known Loom display name → first name mapping
KNOWN_NAMES = {
    "c v": "chad",
    "chad vogl": "chad",
    "zant": "zant",
}


def fetch_recorder_name(url: str) -> str:
    """Fetch the recorder's first name from the Loom page metadata."""
    try:
        import urllib.request
        import json
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # Extract display_name from Apollo state JSON embedded in page
        match = re.search(r'"display_name"\s*:\s*"([^"]+)"', html)
        if match:
            display_name = match.group(1).strip().lower()
            if display_name in KNOWN_NAMES:
                return KNOWN_NAMES[display_name]
            # Fall back to first word of display name
            return display_name.split()[0]
    except Exception:
        pass
    return "unknown"


def download_video(url: str, output_dir: str) -> str:
    """Download Loom video using yt-dlp."""
    print("\n[1/5] Downloading video...")
    output_template = os.path.join(output_dir, "video.%(ext)s")
    result = subprocess.run(
        ["yt-dlp", "--no-warnings", "-o", output_template, url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error downloading video:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Find the downloaded file by globbing
    files = glob.glob(os.path.join(output_dir, "video.*"))
    if not files:
        print("Error: no video file found after download", file=sys.stderr)
        sys.exit(1)

    video_path = files[0]
    print(f"  Downloaded: {os.path.basename(video_path)}")
    return video_path


def extract_audio(video_path: str, output_dir: str) -> str:
    """Extract 16kHz mono WAV audio using ffmpeg."""
    print("\n[2/5] Extracting audio...")
    audio_path = os.path.join(output_dir, "audio.wav")
    result = subprocess.run(
        ["ffmpeg", "-i", video_path, "-ar", "16000", "-ac", "1", "-y", audio_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error extracting audio:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    print("  Audio extracted: 16kHz mono WAV")
    return audio_path


def extract_screenshots(video_path: str, output_dir: str) -> list[dict]:
    """Extract key frames on scene changes using ffmpeg."""
    print("\n[3/5] Extracting key screenshots (scene detection)...")
    frames_dir = os.path.join(output_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    # Use scene detection to extract frames when visual content changes significantly
    # threshold 0.3 = moderate sensitivity (lower = more frames, higher = fewer)
    result = subprocess.run(
        [
            "ffmpeg", "-i", video_path,
            "-vf", "select='gt(scene,0.3)',showinfo",
            "-vsync", "vfr",
            "-frame_pts", "1",
            frames_dir + "/frame_%04d.jpg",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Warning: screenshot extraction failed, continuing without screenshots", file=sys.stderr)
        return []

    # Parse timestamps from showinfo output (appears in stderr)
    frames = []
    frame_files = sorted(glob.glob(os.path.join(frames_dir, "frame_*.jpg")))
    # Extract pts_time from ffmpeg showinfo log lines
    import re
    pts_times = re.findall(r"pts_time:(\d+\.?\d*)", result.stderr)

    for i, frame_file in enumerate(frame_files):
        timestamp = float(pts_times[i]) if i < len(pts_times) else 0.0
        frames.append({"path": frame_file, "timestamp": timestamp})

    print(f"  Extracted {len(frames)} key frames")
    return frames


def transcribe(audio_path: str) -> list[dict]:
    """Transcribe audio using mlx-whisper locally."""
    print("\n[4/5] Transcribing with mlx-whisper (local, may take a moment)...")
    import mlx_whisper

    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
    )

    segments = result.get("segments", [])
    print(f"  Transcribed {len(segments)} segments")
    return segments


def format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm for SRT."""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    ms = int((s - int(s)) * 1000)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def build_srt(segments: list[dict]) -> str:
    """Build SRT subtitle content from whisper segments."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = format_srt_timestamp(seg["start"])
        end = format_srt_timestamp(seg["end"])
        text = seg["text"].strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def build_transcript_text(segments: list[dict], frames: list[dict]) -> str:
    """Build timestamped transcript with inline screenshot references."""
    # Build a map of frame timestamps to filenames for inline insertion
    frame_insertions = {}
    for frame in frames:
        # Find the closest segment start time for this frame
        best_seg_idx = 0
        best_diff = float("inf")
        for i, seg in enumerate(segments):
            diff = abs(seg["start"] - frame["timestamp"])
            if diff < best_diff:
                best_diff = diff
                best_seg_idx = i
        frame_insertions.setdefault(best_seg_idx, []).append(frame)

    lines = []
    for i, seg in enumerate(segments):
        # Insert screenshots before the matching transcript line
        if i in frame_insertions:
            for frame in frame_insertions[i]:
                fname = os.path.basename(frame["path"])
                ts = format_timestamp(frame["timestamp"])
                lines.append(f"\n![Scene at {ts}](frames/{fname})\n")

        ts = format_timestamp(seg["start"])
        text = seg["text"].strip()
        lines.append(f"[{ts}] {text}")

    return "\n".join(lines)


def summarize(transcript: str) -> str:
    """Summarize transcript using Claude CLI."""
    print("\n[5/5] Summarizing with Claude...")

    prompt = f"""Summarize this Loom video transcript. Use this exact format:

## Title
(infer a clear title from the content)

## Summary
(2-3 sentence overview)

## Key Points
- (main topics as bullet points)

## Action Items
- (any tasks, requests, or next steps mentioned — write "None identified" if there are none)

---

Transcript:
{transcript}"""

    # Unset CLAUDECODE to allow nested invocation
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        print(f"Error running Claude:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    return result.stdout.strip()


def extract_title(summary: str) -> str | None:
    """Extract the title from the ## Title section of the summary."""
    match = re.search(r"## Title\s*\n+(.+)", summary)
    if match:
        return match.group(1).strip()
    return None


def slugify(text: str, max_len: int = 60) -> str:
    """Convert text to a filesystem-safe slug."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len].rstrip("-")


def save_output(url: str, transcript: str, summary: str | None, video_path: str, frames: list[dict], srt: str, recorder: str) -> str:
    """Save results, video, SRT subtitles, and screenshots to output/ directory."""
    output_dir = Path(__file__).parent / "output"
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Name folder as yyyy-mm-dd_firstname_simple-title
    folder_name = f"{date_str}_{recorder}_loom"
    if summary:
        title = extract_title(summary)
        if title:
            folder_name = f"{date_str}_{recorder}_{slugify(title)}"

    run_dir = output_dir / folder_name
    run_dir.mkdir(parents=True, exist_ok=True)

    # Save video
    video_ext = Path(video_path).suffix
    saved_video = run_dir / f"video{video_ext}"
    shutil.copy2(video_path, saved_video)
    print(f"  Video saved: {saved_video.name}")

    # Save SRT subtitles
    srt_path = run_dir / "video.srt"
    srt_path.write_text(srt)
    print(f"  Subtitles saved: video.srt")

    # Save screenshots
    if frames:
        frames_dir = run_dir / "frames"
        frames_dir.mkdir(exist_ok=True)
        for frame in frames:
            shutil.copy2(frame["path"], frames_dir / os.path.basename(frame["path"]))

    # Save markdown
    md_path = run_dir / "summary.md"
    parts = [
        f"# Loom Summary\n",
        f"**Recorded by:** {recorder.title()}\n",
        f"**Source:** {url}\n",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        f"**Video:** [video{video_ext}](video{video_ext})\n",
    ]

    if summary:
        parts.append(f"\n{summary}\n")

    parts.append(f"\n## Full Transcript\n\n{transcript}\n")

    md_path.write_text("\n".join(parts))
    return str(run_dir)


def main():
    parser = argparse.ArgumentParser(description="Download, transcribe, and summarize Loom videos.")
    parser.add_argument("url", help="Loom video URL")
    parser.add_argument("--transcript-only", action="store_true", help="Only transcribe, skip summarization")
    args = parser.parse_args()

    # Validate URL
    if "loom.com" not in args.url:
        print("Warning: URL doesn't look like a Loom link. Proceeding anyway...", file=sys.stderr)

    # Fetch recorder name from Loom page
    recorder = fetch_recorder_name(args.url)
    print(f"  Recorder: {recorder}")

    with tempfile.TemporaryDirectory(prefix="loom_") as tmp_dir:
        # Download
        video_path = download_video(args.url, tmp_dir)

        # Extract audio
        audio_path = extract_audio(video_path, tmp_dir)

        # Extract key screenshots
        frames = extract_screenshots(video_path, tmp_dir)

        # Transcribe
        segments = transcribe(audio_path)
        transcript = build_transcript_text(segments, frames)
        srt = build_srt(segments)

        # Summarize (unless --transcript-only)
        summary = None
        if not args.transcript_only:
            summary = summarize(transcript)

        # Save output (video, SRT, frames, markdown)
        saved_path = save_output(args.url, transcript, summary, video_path, frames, srt, recorder)

    # Print results
    print("\n" + "=" * 60)
    if summary:
        print(summary)
        print("\n" + "-" * 60)
    print("\n## Full Transcript\n")
    print(transcript)
    print("\n" + "=" * 60)
    print(f"\nSaved to: {saved_path}")


if __name__ == "__main__":
    main()
