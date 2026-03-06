# Loom Summarize

CLI tool that downloads Loom videos, transcribes them locally, and generates AI summaries.

- **yt-dlp** — downloads the video
- **ffmpeg** — extracts 16kHz mono audio
- **mlx-whisper** — transcribes locally on Apple Silicon (no API key needed)
- **LLM summarization** — supports Claude CLI, Ollama (local), and OpenAI API

## Output

Each run creates a folder in `output/` named `yyyy-mm-dd_recorder_title/` containing:

| File | Description |
|------|-------------|
| `video.mp4` | Downloaded video |
| `video.srt` | Subtitles (auto-detected by VLC/IINA) |
| `summary.md` | AI summary + timestamped transcript with inline screenshots |
| `frames/` | Key scene-change screenshots |

## Setup

Requires Python 3.11+, ffmpeg, and yt-dlp installed on your system. The first run will create a venv and install Python dependencies automatically.

```bash
git clone https://github.com/envibagus/loom-summarize.git
cd loom-summarize
chmod +x run.sh
```

## Usage

```bash
# Full pipeline (uses Claude CLI by default)
./run.sh https://www.loom.com/share/xxxxx

# Transcript only (skip summarization)
./run.sh https://www.loom.com/share/xxxxx --transcript-only
```

### Choosing an LLM

Use the `--llm` flag to pick your summarization provider:

```bash
# Claude CLI (default) — requires Claude CLI installed
./run.sh https://www.loom.com/share/xxxxx --llm claude

# Ollama (fully local, no API key) — requires Ollama installed
./run.sh https://www.loom.com/share/xxxxx --llm ollama:llama3.1
./run.sh https://www.loom.com/share/xxxxx --llm ollama:mistral

# OpenAI API — requires OPENAI_API_KEY env var
export OPENAI_API_KEY=sk-...
./run.sh https://www.loom.com/share/xxxxx --llm openai:gpt-4o
./run.sh https://www.loom.com/share/xxxxx --llm openai:gpt-4o-mini

# Gemini API — requires GEMINI_API_KEY env var
export GEMINI_API_KEY=...
./run.sh https://www.loom.com/share/xxxxx --llm gemini:gemini-2.0-flash
./run.sh https://www.loom.com/share/xxxxx --llm gemini:gemini-2.5-pro-preview-06-05
```

### Shell alias

Set up an alias for quick access:

```bash
echo 'alias loom-v="~/path/to/loom-summarize/run.sh"' >> ~/.zshrc
source ~/.zshrc

loom-v https://www.loom.com/share/xxxxx
```

## How it works

1. Fetches the recorder's name from the Loom page
2. Downloads the video with yt-dlp
3. Extracts audio as 16kHz mono WAV
4. Captures key screenshots via ffmpeg scene detection
5. Transcribes locally with mlx-whisper (whisper-large-v3-turbo)
6. Summarizes with your chosen LLM → title, key points, action items
7. Saves everything to a named output folder
