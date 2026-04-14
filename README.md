# 🎥 yt-heatmap-clipper (Ultimate Pro)

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-Required-green.svg)](https://ffmpeg.org/)
[![Whisper](https://img.shields.io/badge/AI-Faster--Whisper-orange.svg)](https://github.com/guillaumekln/faster-whisper)
[![Gemini](https://img.shields.io/badge/AI-Gemini%201.5%20Flash-magenta.svg)](https://ai.google.dev/)

Automatically extract the most engaging segments from YouTube videos using **Heatmap (Most Replayed)** data or **AI-powered Curation**, then convert them into viral-ready vertical clips with AI subtitles and clickbait metadata.

---

## ✨ Features

### 🧠 Smart Extraction
- **Heatmap Analysis**: Automatically identifies high-engagement moments using YouTube's "Most Replayed" data.
- **AI Curation (Fallback)**: If no heatmap is available, **Gemini AI** analyzes the video transcript to find the most viral/interesting segments.

### ✂️ Professional Editing
- **Vertical Export**: Outputs perfect 9:16 vertical videos (720x1280) ready for **Shorts, TikTok, and Reels**.
- **3 Pro Crop Modes**:
  - **Center Crop**: Standard focus on the middle.
  - **Split Left**: Gaming/Reaction style (Top: Center, Bottom: Facecam Left).
  - **Split Right**: Gaming/Reaction style (Top: Center, Bottom: Facecam Right).
- **Padding & Limits**: Configurable pre/post padding and maximum clip duration.

### 💬 AI Subtitles & Hooks
- **Word-Level Subtitles**: Dynamic "Hormozi Style" subtitles powered by **Faster-Whisper**.
- **Automated Hooks**: Gemini AI generates a "Static Hook" (text overlay) at the start of the video to grab attention.
- **Clickbait Metadata**: Gemini generates 3 viral titles, a description, and 5 trending hashtags for every clip.

### 🛠️ Developer Friendly
- **Rich CLI UI**: Beautiful, interactive terminal interface.
- **Configuration System**: Manage settings via `config.json` and API keys via `.env`.
- **Resume Capability**: Skips already processed clips to save time/bandwidth.

---

## 🚀 Quick Start

### 1. Requirements
- **Python 3.8+**
- **FFmpeg** installed and added to `PATH`.

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/0xACAB666/yt-heatmap-clipper.git
cd yt-heatmap-clipper

# Install dependencies
pip install -r requirements.txt
```

### 3. Setup API Keys (Optional but Recommended)
Create a `.env` file in the root directory:
```env
GEMINI_API_KEY=your_gemini_api_key_here
```
> Get your free API key at [Google AI Studio](https://aistudio.google.com/).

### 4. Run System Check
```bash
python check_setup.py
```

### 5. Launch
```bash
python run.py
```

---

## ⚙️ Configuration

You can customize almost everything in `config.json`. If it doesn't exist, copy `config.json.example`.

| Key | Default | Description |
| :--- | :--- | :--- |
| `max_duration` | `60` | Maximum length of each clip (seconds) |
| `min_score` | `0.40` | Heatmap score threshold (0.0 - 1.0) |
| `padding` | `20` | Seconds added before/after segments |
| `whisper_model` | `small` | AI model: `tiny`, `base`, `small`, `medium`, `large` |
| `output_dir` | `clips` | Folder to save generated clips |

---

## 📂 Output Structure

The tool organizes clips into subfolders based on the Video ID:
```text
clips/
└── {video_id}/
    ├── clip_1.mp4
    ├── clip_1_metadata.txt      <-- Viral titles & hashtags
    ├── clip_2.mp4
    └── ...
```

---

## 📐 Crop Modes Visualized

### Mode 1: Default (Center Crop)
```text
┌───────────────────────┐     ┌───────────┐
│           │           │     │           │
│        CONTENT        │ ──► │  CONTENT  │
│           │           │     │           │
└───────────────────────┘     └───────────┘
```

### Mode 2/3: Split Crop (Gaming/Reaction)
```text
┌───────────────────────┐     ┌───────────┐
│           │           │     │  CONTENT  │ (Top)
│        CONTENT        │ ──► │ (Center)  │
│           │           │     ├───────────┤
├───┐       │           │     │  FACECAM  │ (Bottom)
│CAM│       │           │     │           │
└───┴───────────────────┘     └───────────┘
```

---

## ❓ FAQ & Troubleshooting

- **"No high-engagement segments found"**: The video may be too new or have very low views. Try lowering `min_score` in `config.json`.
- **FFmpeg errors**: Ensure FFmpeg is correctly installed. Type `ffmpeg -version` in your terminal to check.
- **Slow transcription**: If you don't have a GPU, use `tiny` or `base` whisper models.

---

## 🙏 Credits

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - YouTube video downloader
- [Faster-Whisper](https://github.com/guillaumekln/faster-whisper) - High-speed AI transcription
- [Google Gemini](https://ai.google.dev/) - Content curation & metadata AI
- [Rich](https://github.com/Textualize/rich) - CLI aesthetic engine

---
MIT License • Created by [0xACAB666](https://github.com/0xACAB666)