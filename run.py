import os
import re
import json
import sys
import subprocess
import requests
import shutil
from urllib.parse import urlparse, parse_qs
import warnings

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings("ignore")

# --- Load Environment Variables (.env) ---
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                try:
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v
                except ValueError:
                    pass

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# --- Load Project Config (config.json) ---
try:
    with open("config.json", "r", encoding="utf-8") as cb:
        CONFIG = json.load(cb)
except Exception as e:
    print(f"⚠️ Gagal membaca config.json, menggunakan nilai default ({e})")
    CONFIG = {}

OUTPUT_DIR = CONFIG.get("output_dir", "clips")
MAX_DURATION = CONFIG.get("max_duration", 60)
MIN_SCORE = CONFIG.get("min_score", 0.40)
MAX_CLIPS = CONFIG.get("max_clips", 10)
MAX_WORKERS = CONFIG.get("max_workers", 1)
PADDING = CONFIG.get("padding", 20)
TOP_HEIGHT = CONFIG.get("top_height", 960)
BOTTOM_HEIGHT = CONFIG.get("bottom_height", 320)
USE_SUBTITLE = CONFIG.get("use_subtitle", True)
WHISPER_MODEL = CONFIG.get("whisper_model", "small")

SUBTITLE_STYLE = CONFIG.get("subtitle_style", {
    "font_name": "Arial",
    "font_size": 14,
    "primary_color": "&HFFFFFF",
    "outline_color": "&H000000"
})

HOOK_STYLE = CONFIG.get("hook_style", {
    "font_color": "white",
    "box_color": "red@0.9",
    "font_size": 28
})

def extract_video_id(url):
    """
    Extract the YouTube video ID from a given URL.
    Supports standard YouTube URLs, shortened URLs, and Shorts URLs.
    """
    parsed = urlparse(url)

    if parsed.hostname in ("youtu.be", "www.youtu.be"):
        return parsed.path[1:]

    if parsed.hostname in ("youtube.com", "www.youtube.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]

    return None


def get_model_size(model):
    """
    Get the approximate size of a Whisper model.
    """
    sizes = {
        "tiny": "75 MB",
        "base": "142 MB",
        "small": "466 MB",
        "medium": "1.5 GB",
        "large-v1": "2.9 GB",
        "large-v2": "2.9 GB",
        "large-v3": "2.9 GB"
    }
    return sizes.get(model, "unknown size")


def cek_dependensi(install_whisper=False):
    """
    Ensure required dependencies are available.
    Automatically updates yt-dlp and checks FFmpeg availability.
    """
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    if install_whisper:
        # Check if faster-whisper package is installed
        try:
            import faster_whisper
            print(f"✅ Faster-Whisper package installed.")
            
            # Check if selected model is cached
            cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
            model_name = f"faster-whisper-{WHISPER_MODEL}"
            
            model_cached = False
            if os.path.exists(cache_dir):
                try:
                    cached_items = os.listdir(cache_dir)
                    model_cached = any(model_name in item.lower() for item in cached_items)
                except Exception:
                    pass
            
            if model_cached:
                print(f"✅ Model '{WHISPER_MODEL}' already cached and ready.\n")
            else:
                print(f"⚠️  Model '{WHISPER_MODEL}' not found in cache.")
                print(f"   📥 Will auto-download ~{get_model_size(WHISPER_MODEL)} on first transcribe.")
                print(f"   ⏱️  Download happens only once, then cached for future use.\n")
                
        except ImportError:
            print("📦 Installing Faster-Whisper package...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "faster-whisper"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print(f"✅ Faster-Whisper package installed successfully.")
            print(f"⚠️  Model '{WHISPER_MODEL}' (~{get_model_size(WHISPER_MODEL)}) will be downloaded on first use.\n")

    if not shutil.which("ffmpeg"):
        print("FFmpeg not found. Please install FFmpeg and ensure it is in PATH.")
        sys.exit(1)


def ambil_most_replayed(video_id):
    """
    Fetch and parse YouTube 'Most Replayed' heatmap data.
    Returns a list of high-engagement segments.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {"User-Agent": "Mozilla/5.0"}

    print("Reading YouTube heatmap data...")

    try:
        html = requests.get(url, headers=headers, timeout=20).text
    except Exception:
        return []

    match = re.search(
        r'"markers":\s*(\[.*?\])\s*,\s*"?markersMetadata"?',
        html,
        re.DOTALL
    )

    if not match:
        return []

    try:
        markers = json.loads(match.group(1).replace('\\"', '"'))
    except Exception:
        return []

    results = []

    for marker in markers:
        if "heatMarkerRenderer" in marker:
            marker = marker["heatMarkerRenderer"]

        try:
            score = float(marker.get("intensityScoreNormalized", 0))
            if score >= MIN_SCORE:
                results.append({
                    "start": float(marker["startMillis"]) / 1000,
                    "duration": min(
                        float(marker["durationMillis"]) / 1000,
                        MAX_DURATION
                    ),
                    "score": score
                })
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def get_duration(video_id):
    """
    Retrieve the total duration of a YouTube video in seconds.
    """
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--get-duration",
        f"https://youtu.be/{video_id}"
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        time_parts = res.stdout.strip().split(":")

        if len(time_parts) == 2:
            return int(time_parts[0]) * 60 + int(time_parts[1])
        if len(time_parts) == 3:
            return (
                int(time_parts[0]) * 3600 +
                int(time_parts[1]) * 60 +
                int(time_parts[2])
            )
    except Exception:
        pass

    return 3600


def generate_subtitle(video_file, subtitle_file):
    """
    Generate subtitle file using Faster-Whisper for the given video.
    Returns True if successful, False otherwise.
    """
    try:
        from faster_whisper import WhisperModel
        
        print(f"  Loading Faster-Whisper model '{WHISPER_MODEL}'...")
        print(f"  (If this is first time, downloading ~{get_model_size(WHISPER_MODEL)}...)")
        # Use int8 for CPU efficiency, or "float16" for GPU
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        
        print("  ✅ Model loaded. Transcribing audio (4-5x faster than standard Whisper)...")
        segments, info = model.transcribe(video_file, language="id")
        
        # Generate SRT format
        print("  Generating subtitle file...")
        with open(subtitle_file, "w", encoding="utf-8") as f:
            for i, segment in enumerate(segments, start=1):
                start_time = format_timestamp(segment.start)
                end_time = format_timestamp(segment.end)
                text = segment.text.strip()
                
                f.write(f"{i}\n")
                f.write(f"{start_time} --> {end_time}\n")
                f.write(f"{text}\n\n")
        
        return True
    except Exception as e:
        print(f"  Failed to generate subtitle: {str(e)}")
        return False


def format_timestamp(seconds):
    """
    Convert seconds to SRT timestamp format (HH:MM:SS,mmm)
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def proses_satu_clip(video_id, item, index, total_duration, crop_mode="default", use_subtitle=False):
    """
    Download, crop, and export a single vertical clip
    based on a heatmap segment.
    
    Args:
        crop_mode: "default", "split_left", or "split_right"
        use_subtitle: whether to generate and burn subtitle
    """
    start_original = item["start"]
    end_original = item["start"] + item["duration"]

    start = max(0, start_original - PADDING)
    end = min(end_original + PADDING, total_duration)

    if end - start < 3:
        return False

    temp_file = f"temp_{index}.mp4"
    cropped_file = f"temp_cropped_{index}.mp4"
    subtitle_file = f"temp_{index}.srt"
    output_file = os.path.join(OUTPUT_DIR, f"clip_{index}.mp4")

    print(
        f"[Clip {index}] Processing segment "
        f"({int(start)}s - {int(end)}s, padding {PADDING}s)"
    )

    cmd_download = [
        sys.executable, "-m", "yt_dlp",
        "--force-ipv4",
        "--quiet", "--no-warnings",
        "--downloader", "ffmpeg",
        "--downloader-args",
        f"ffmpeg_i:-ss {start} -to {end} -hide_banner -loglevel error",
        "-f",
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", temp_file,
        f"https://youtu.be/{video_id}"
    ]

    try:
        result = subprocess.run(
            cmd_download,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if not os.path.exists(temp_file):
            print("Failed to download video segment.")
            return False

        # Build video filter based on crop_mode
        # First, crop the video to cropped_file
        if crop_mode == "default":
            # Standard center crop - ambil dari tengah video
            cmd_crop = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", temp_file,
                "-vf", "scale=-2:1280,crop=720:1280:(iw-720)/2:(ih-1280)/2",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                "-c:a", "aac", "-b:a", "128k",
                cropped_file
            ]
        elif crop_mode == "split_left":
            # Split crop: 
            # - Top: konten game dari tengah-tengah video (960px)
            # - Bottom: facecam dari kiri bawah video asli (320px)
            vf = (
                f"scale=-2:1280[scaled];"
                f"[scaled]split=2[s1][s2];"
                f"[s1]crop=720:{TOP_HEIGHT}:(iw-720)/2:(ih-1280)/2[top];"
                f"[s2]crop=720:{BOTTOM_HEIGHT}:0:ih-{BOTTOM_HEIGHT}[bottom];"
                f"[top][bottom]vstack=inputs=2[out]"
            )
            cmd_crop = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", temp_file,
                "-filter_complex", vf,
                "-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                "-c:a", "aac", "-b:a", "128k",
                cropped_file
            ]
        elif crop_mode == "split_right":
            # Split crop: 
            # - Top: konten game dari tengah-tengah video (960px)
            # - Bottom: facecam dari kanan bawah video asli (320px)
            vf = (
                f"scale=-2:1280[scaled];"
                f"[scaled]split=2[s1][s2];"
                f"[s1]crop=720:{TOP_HEIGHT}:(iw-720)/2:(ih-1280)/2[top];"
                f"[s2]crop=720:{BOTTOM_HEIGHT}:iw-720:ih-{BOTTOM_HEIGHT}[bottom];"
                f"[top][bottom]vstack=inputs=2[out]"
            )
            cmd_crop = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", temp_file,
                "-filter_complex", vf,
                "-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                "-c:a", "aac", "-b:a", "128k",
                cropped_file
            ]

        print("  Cropping video...")
        result = subprocess.run(
            cmd_crop,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        os.remove(temp_file)

        # Generate and burn subtitle if enabled
        if use_subtitle:
            print("  Generating subtitle...")
            if generate_subtitle(cropped_file, subtitle_file):
                print("  Burning subtitle to video...")
                # Get absolute path for subtitle file
                abs_subtitle_path = os.path.abspath(subtitle_file)
                # Escape for FFmpeg: replace \ with / and escape special chars
                subtitle_path = abs_subtitle_path.replace("\\", "/").replace(":", "\\:")

                # Auto-Titling Simple (Menyimpan Metadata dalam TXT) 
                # (Sengaja digeser ke Sini agar bisa mengekstraksi Hook Title ke dalam FFmpeg)
                meta_file = os.path.join(video_out_dir, f"clip_{index}_metadata.txt")
                with open(meta_file, "w", encoding="utf-8") as mf:
                    mf.write(f"--- AUTO-TITLE & DESC CLIP {index} ---\n\n")
                    mf.write("📝 TRANSKRIP ASLI:\n")
                    mf.write(f"{full_text}\n\n")
                    
                    if genai and GEMINI_API_KEY:
                        print("  🤖 Asking Gemini AI for viral titles...")
                        gemini_result = generate_metadata_with_gemini(full_text)
                        if gemini_result and not gemini_result.startswith("[Error"):
                            mf.write("✨ IDE KONTEN VIRAL & HASHTAG DARI GEMINI AI:\n")
                            mf.write(f"{gemini_result}\n\n")
                            
                            # Ekstrak judul baris pertama dari JSON/teks AI untuk dijadikan Static Hook Tiitle
                            for line in gemini_result.strip().split('\n'):
                                if line.strip().startswith("1."):
                                    hook_title = line.replace("1.", "").replace("*", "").replace("\"", "").strip()
                                    break
                        else:
                            mf.write(f"⚠️ GAGAL MENGGUNAKAN GEMINI AI: {gemini_result}\n\n")
                    else:
                        mf.write("🔖 IDE JUDUL (Berdasarkan Kata Kunci Utama):\n")
                        # Mengambil 5 kata pertama sebagai ide judul
                        snippet = " ".join(full_text.split()[:5])
                        hook_title = f"FAKTA {snippet.upper()}!"
                        mf.write(f"🔥 INI DIA... {snippet.capitalize()}!\n")
                        mf.write(f"😱 Fakta Mencengangkan Tentang {snippet.capitalize()} \n\n")
                        mf.write("HASHTAGS:\n#Shorts #Viral #Trending #Fyp\n")

                print(f"  ✅ Metadata (Judul/Hashtag) disimpan di: {meta_file}")
                
                # Bersihkan label Hook Title agar aman dimasak oleh FFmpeg (hanya huruf, angka, spasi)
                clean_hook = "".join(c for c in hook_title if c.isalnum() or c in " ?!.,-…").replace("'", "").replace(":", "").strip()
                if not clean_hook:
                    clean_hook = "Tonton Sampai Habis!"
                
                # Batasi ulang setelah dibersihkan
                if len(clean_hook) > 35:
                    pass
                
                # Bungkus teks menjadi list baris (maks 25 huruf per baris)
                clean_hook_lines = textwrap.wrap(clean_hook, width=25)
                
                # Buat filter drawtext terpisah untuk setiap baris
                drawtext_filters = []
                start_y = 180
                line_height = 45 # Jarak antar baris
                for idx, line_text in enumerate(clean_hook_lines):
                    y_pos = start_y + (idx * line_height)
                    
                    font_color = HOOK_STYLE.get("font_color", "white")
                    box_color = HOOK_STYLE.get("box_color", "red@0.9")
                    font_size = HOOK_STYLE.get("font_size", 28)
                    
                    dt_filter = (
                        f"drawtext=text='{line_text}':fontcolor={font_color}:box=1:boxcolor={box_color}:boxborderw=15:"
                        f"fontsize={font_size}:x=(w-text_w)/2:y={y_pos}:enable='between(t,0,4)'"
                    )
                    drawtext_filters.append(dt_filter)
                
                drawtext_combined = ",".join(drawtext_filters)
                
                # Filter kompleks: Subtitle kata per kata di bawah, lalu hook dibagian atas
                s_font = SUBTITLE_STYLE.get("font_name", "Arial")
                s_size = SUBTITLE_STYLE.get("font_size", 14)
                s_pri = SUBTITLE_STYLE.get("primary_color", "&HFFFFFF")
                s_out = SUBTITLE_STYLE.get("outline_color", "&H000000")
                
                vf_filters = (
                    f"subtitles='{subtitle_path}':force_style='FontName={s_font},FontSize={s_size},PrimaryColour={s_pri},"
                    f"OutlineColour={s_out},BorderStyle=1,Outline=3,Shadow=2,Alignment=2,MarginV=60',"
                    f"{drawtext_combined}"
                )
                
                cmd_subtitle = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", cropped_file,
                    "-vf", f"subtitles='{subtitle_path}':force_style='FontName=Arial,FontSize=12,Bold=1,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=100'",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                    "-c:a", "copy",
                    output_file
                ]
                
                result = subprocess.run(
                    cmd_subtitle,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                os.remove(cropped_file)
                os.remove(subtitle_file)
            else:
                # If subtitle generation failed, use cropped file as output
                print("  Subtitle generation failed, continuing without subtitle...")
                os.rename(cropped_file, output_file)
        else:
            # No subtitle, rename cropped file to output
            os.rename(cropped_file, output_file)

        print("Clip successfully generated.")
        return True

    except subprocess.CalledProcessError as e:
        # Cleanup temp files
        for f in [temp_file, cropped_file, subtitle_file]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

        print(f"Failed to generate this clip.")
        print(f"Error details: {e.stderr if e.stderr else e.stdout}")
        return False
    except Exception as e:
        # Cleanup temp files
        for f in [temp_file, cropped_file, subtitle_file]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

        print(f"Failed to generate this clip.")
        print(f"Error: {str(e)}")
        return False


def main():
    """
    Main entry point of the application.
    """
    # Select crop mode
    print("\n=== Crop Mode ===")
    print("1. Default (center crop)")
    print("2. Split 1 (top: center, bottom: bottom-left (facecam))")
    print("3. Split 2 (top: center, bottom: bottom-right ((facecam))")
    
    while True:
        choice = input("\nSelect crop mode (1-3): ").strip()
        if choice == "1":
            crop_mode = "default"
            crop_desc = "Default center crop"
            break
        elif choice == "2":
            crop_mode = "split_left"
            crop_desc = "Split crop (bottom-left facecam)"
            break
        elif choice == "3":
            crop_mode = "split_right"
            crop_desc = "Split crop (bottom-right facecam)"
            break
        else:
            print("Invalid choice. Please enter 1, 2, or 3.")
    
    print(f"Selected: {crop_desc}")
    
    # Ask for subtitle
    print("\n=== Auto Subtitle ===")
    print(f"Available model: {WHISPER_MODEL} (~{get_model_size(WHISPER_MODEL)})")
    subtitle_choice = input("Add auto subtitle using Faster-Whisper? (y/n): ").strip().lower()
    use_subtitle = subtitle_choice in ["y", "yes"]
    
    if use_subtitle:
        print(f"✅ Subtitle enabled (Model: {WHISPER_MODEL}, Bahasa Indonesia)")
    else:
        print("❌ Subtitle disabled")
    
    print()
    
    # Check dependencies
    cek_dependensi(install_whisper=use_subtitle)

    link = input("Link YT: ").strip()
    video_id = extract_video_id(link)

    if not video_id:
        print("Invalid YouTube link.")
        return

    heatmap_data = ambil_most_replayed(video_id)

    if not heatmap_data:
        print("No high-engagement segments found.")
        return

    print(f"Found {len(heatmap_data)} high-engagement segments.")

    total_duration = get_duration(video_id)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(
        f"Processing clips with {PADDING}s pre-padding "
        f"and {PADDING}s post-padding."
    )
    print(f"Using crop mode: {crop_desc}")

    success_count = 0

    for item in heatmap_data:
        if success_count >= MAX_CLIPS:
            break

        if proses_satu_clip(
            video_id,
            item,
            success_count + 1,
            total_duration,
            crop_mode,
            use_subtitle
        ):
            success_count += 1

    print(
        f"Finished processing. "
        f"{success_count} clip(s) successfully saved to '{OUTPUT_DIR}'."
    )


if __name__ == "__main__":
    main()