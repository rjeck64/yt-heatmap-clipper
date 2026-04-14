import textwrap
import os
import re
import json
import sys
import subprocess
import requests
import shutil
import http.cookiejar
from urllib.parse import urlparse, parse_qs
import warnings

try:
    from google import genai
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich import print as rprint
    from youtube_transcript_api import YouTubeTranscriptApi
    console = Console()
except ImportError as e:
    print(f"\n❌ Modul Python hilang: {e.name}")
    print("Harap install semua modul di requirements.txt terlebih dahulu:")
    print("Jalankan: pip install -r requirements.txt\n")
    sys.exit(1)

sys.stdout.reconfigure(encoding='utf-8')
# Tangkap FutureWarning agar tidak menutupi UI kita dengan teks merah jelek dari Google
warnings.simplefilter('ignore', category=FutureWarning)
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

def generate_metadata_with_gemini(transcript):
    """
    Meminta Gemini membuat judul dan deskripsi clickbait berdasarkan transkrip.
    """
    if not genai or not GEMINI_API_KEY:
        return None
        
    try:
        # Gunakan SDK baru (google-genai) sesuai rekomendasi Google API terbaru
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # Deteksi otomatis model AI
        best_model = "gemini-1.5-flash" # default fallback untuk SDK baru
        try:
            for m in client.models.list():
                if 'generateContent' in m.supported_actions:
                    if 'flash' in m.name:
                        best_model = m.name
                        break
        except Exception:
            pass

        prompt = (
            "Kamu adalah seorang kreator konten TikTok dan YouTube Shorts yang sangat ahli dalam membuat hook dan judul clickbait (viral). "
            "Saya punya transkrip video pendek. Tolong buatkan: "
            "1. Tiga (3) ide judul video yang super memancing rasa penasaran (huruf besar di awal kata, gunakan emoji yang pas). "
            "2. Deskripsi singkat 1 paragraf untuk di-post, dan "
            "3. 5 hashtag yang relevan dan berpotensi FYP/Trending.\n\n"
            f"Transkrip:\n\"{transcript}\""
        )
        response = client.models.generate_content(
            model=best_model,
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"[Error Gemini API]: {str(e)}"

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
    Hanya mengecek instalasi, tidak menginstall secara otomatis.
    """
    if not shutil.which("ffmpeg"):
        rprint("[bold red]❌ FFmpeg tidak ditemukan.[/bold red] Harap install FFmpeg (sudo apt install ffmpeg) dan pastikan masuk ke dalam PATH.")
        sys.exit(1)

    if install_whisper:
        try:
            import faster_whisper
        except ImportError:
            rprint("[bold red]❌ Library 'faster-whisper' belum terinstall.[/bold red]")
            rprint("   Jalankan: [bold]pip install -r requirements.txt[/bold]\n")
            sys.exit(1)
            
        rprint("[green]✅ Faster-Whisper package terdeteksi.[/green]")
        
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
            rprint(f"[green]✅ Model '{WHISPER_MODEL}' already cached and ready.[/green]\n")
        else:
            rprint(f"[bold yellow]⚠️  Model '{WHISPER_MODEL}' not found in cache.[/bold yellow]")
            rprint(f"   📥 Will auto-download ~{get_model_size(WHISPER_MODEL)} on first transcribe.")
            rprint("   ⏱️  Download happens only once, then cached for future use.\n")


def ambil_most_replayed(video_id):
    """
    Fetch and parse YouTube 'Most Replayed' heatmap data.
    Returns a list of high-engagement segments.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    print("Reading YouTube heatmap data...")

    session = requests.Session()
    session.headers.update(headers)
    
    if os.path.exists("cookies.txt"):
        print("  [Info] Menggunakan file cookies.txt untuk mem-bypass batasan YouTube...")
        try:
            cj = http.cookiejar.MozillaCookieJar("cookies.txt")
            cj.load(ignore_discard=True, ignore_expires=True)
            session.cookies.update(cj)
        except Exception as e:
            print(f"  [Warning] Gagal meload cookies.txt: {e}")

    try:
        response = session.get(url, timeout=20)
        if response.status_code == 429:
            print("⚠️ ERROR: YouTube membatasi permintaan Anda (HTTP 429: Too Many Requests).")
            print("   Mungkin karena sering mendownload atau IP terkena rate-limit.")
            return []
        html = response.text
    except Exception as e:
        print(f"⚠️ ERROR saat mengakses YouTube: {str(e)}")
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


def ambil_ai_curation(video_id):
    """
    Jika heatmap tidak ada, ambil transkrip utuh (dari youtube-transcript-api) 
    lalu serahkan ke Gemini untuk mencari segmen momen viral.
    """
    if not genai or not GEMINI_API_KEY:
        rprint("[yellow]Gemini belum dikonfigurasi, melewati fitur AI Curation.[/yellow]")
        return []
    
    rprint("[cyan]Mengambil transkrip dari YouTube untuk dianalisis oleh AI...[/cyan]")
    try:
        ts = YouTubeTranscriptApi.get_transcript(video_id, languages=['id', 'en'])
        
        # Gabungkan Teks ke Blok-Blok kecil beserta Timestamp
        text_with_times = ""
        for t in ts:
            text = t['text'].replace('\n', ' ').strip()
            text_with_times += f"[{int(t['start'])}s]: {text}\n"
        
        # Batasi ke 30.000 karakter agar tidak memakan token terlalu banyak
        text_with_times = text_with_times[:30000]
        
        client = genai.Client(api_key=GEMINI_API_KEY)
        best_model = "gemini-1.5-flash"
        
        # Cari Flash Model
        try:
            for m in client.models.list():
                if 'generateContent' in m.supported_actions and 'flash' in m.name:
                    best_model = m.name
                    break
        except Exception:
            pass

        prompt = (
            "Kamu adalah Asisten Kurator Konten TikTok dan Reels. \n"
            "Berikut adalah transkrip dari sebuah video YouTube dengan timestamp-nya. "
            "Tugasmu adalah mencari 3 segmen/momen paling menarik, emosional, informasi klimaks, atau lucu yang berdurasi 30 sampai 60 detik. "
            "Pilih secara acak dari awal, tengah, atau akhir yang paling bagus. "
            "Balas HANYA dengan format JSON Array persis seperti contoh di bawah, dan tidak ada teks awalan/penjelasan sama sekali.\n\n"
            "Contoh balasan:\n"
            "[\n"
            "  {\"start\": 120, \"duration\": 50, \"score\": 0.9},\n"
            "  {\"start\": 350, \"duration\": 40, \"score\": 0.8}\n"
            "]\n\n"
            f"Transkrip:\n{text_with_times}"
        )
        
        rprint("[cyan]Menganalisis isi video menggunakan Gemini AI...[/cyan]")
        response = client.models.generate_content(model=best_model, contents=prompt)
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        segments = json.loads(clean_text)
        
        results = []
        for s in segments:
            results.append({
                "start": float(s["start"]),
                "duration": float(s["duration"]),
                "score": float(s.get("score", 0.8))
            })
            
        rprint(f"[green]✅ AI berhasil menemukan {len(results)} segmen menarik![/green]")
        return results
    except Exception as e:
        rprint(f"[bold red]❌ AI Curation gagal: {str(e)}[/bold red]")
        return []


def get_duration(video_id):
    """
    Retrieve the total duration of a YouTube video in seconds.
    """
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--js-runtimes", "node",
        "--get-duration",
        f"https://youtu.be/{video_id}"
    ]
    
    if os.path.exists("cookies.txt"):
        cmd.extend(["--cookies", "cookies.txt"])

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
        # Aktifkan word_timestamps=True untuk Hormozi-style
        segments, info = model.transcribe(video_file, language="id", word_timestamps=True)
        
        # Generate SRT format (Hormozi Style / Word-Level)
        print("  Generating subtitle file (Word-Level Hormozi Style)...")
        full_text = []

        with open(subtitle_file, "w", encoding="utf-8") as f:
            sub_idx = 1
            for segment in segments:
                words = list(segment.words)
                # Kumpulkan kata dalam kelompok pendek (maksimal 3-4 kata per baris)
                chunk_size = 4
                for i in range(0, len(words), chunk_size):
                    chunk = words[i:i+chunk_size]
                    
                    for j, current_word in enumerate(chunk):
                        start_time = format_timestamp(current_word.start)
                        # Agar subtitle menyala terus (menyambung tanpa hilang), kita atur end_time ke awal kata berikutnya
                        if j < len(chunk) - 1:
                            end_time = format_timestamp(chunk[j+1].start)
                        else:
                            end_time = format_timestamp(current_word.end)
                        
                        word_clean = current_word.word.strip().upper()
                        # Jangan simpan duplikat ke full_text
                        if j == 0 or len(chunk) == 1:
                            pass # We will collect text separately below
                        
                        # Bangun teks baris (menampilkan seluruh chunk tapi hanya 1 yang kuning)
                        chunk_text_parts = []
                        for k, w in enumerate(chunk):
                            w_text = w.word.strip().upper()
                            if k == j:
                                # Highlight kata yang sedang diucapkan (Kuning + Bold)
                                chunk_text_parts.append(f'<font color="#FFFF00"><b>{w_text}</b></font>')
                            else:
                                # Kata lainnya (Putih biasa)
                                chunk_text_parts.append(w_text)
                                
                        line_text = " ".join(chunk_text_parts)
                        
                        f.write(f"{sub_idx}\n")
                        f.write(f"{start_time} --> {end_time}\n")
                        f.write(f"{line_text}\n\n")
                        sub_idx += 1
                
                # Simpan teks asli untuk keperluan metadata AI
                for w in words:
                    full_text.append(w.word.strip())
        
        return True, " ".join(full_text)
    except Exception as e:
        print(f"  Failed to generate subtitle: {str(e)}")
        return False, ""


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

    temp_file = f"temp_{video_id}_{index}.mp4"
    cropped_file = f"temp_cropped_{video_id}_{index}.mp4"
    subtitle_file = f"temp_{video_id}_{index}.srt"
    
    video_out_dir = os.path.join(OUTPUT_DIR, video_id)
    os.makedirs(video_out_dir, exist_ok=True)
    
    output_file = os.path.join(video_out_dir, f"clip_{index}.mp4")

    # Resume feature: Jika file mp4 sudah ada, skip proses download dan render
    if os.path.exists(output_file):
        print(f"[Clip {index}] ✅ File {output_file} sudah ada! Melanjutkan ke klip berikutnya (Resume mode).")
        return True

    print(
        f"[Clip {index}] Processing segment "
        f"({int(start)}s - {int(end)}s, padding {PADDING}s)"
    )

    cmd_download = [
        sys.executable, "-m", "yt_dlp",
        "--js-runtimes", "node",
        "--force-ipv4",
        "--quiet", "--no-warnings",
        "--downloader", "ffmpeg",
        "--downloader-args",
        f"ffmpeg_i:-ss {start} -to {end} -hide_banner -loglevel error",
        "-f",
        "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "-o", temp_file,
        f"https://youtu.be/{video_id}"
    ]

    if os.path.exists("cookies.txt"):
        cmd_download.extend(["--cookies", "cookies.txt"])

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
            print("  Generating subtitle and Auto-Title...")
            success, full_text = generate_subtitle(cropped_file, subtitle_file)
            if success:
                # Inisialisasi hook_title untuk fitur Pancingan Statis
                hook_title = "FAKTA MENGEJUTKAN!"
                
                print("  Burning word-level subtitle to video...")
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
                    "-vf", vf_filters,
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
    if console:
        console.print(Panel.fit("[bold cyan]🔥 YT Heatmap Clipper (Ultimate Pro Edition) 🔥[/bold cyan]\n[white]Auto-Clip, Auto-Subtitle & Auto-Title[/white]", border_style="cyan"))
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("No", style="dim", width=4)
        table.add_column("Crop Mode", min_width=20)
        table.add_column("Description")
        
        table.add_row("1", "[green]Default[/green]", "Center crop (9:16)")
        table.add_row("2", "[blue]Split 1[/blue]", "Top: Center, Bottom: Bottom-Left (Facecam)")
        table.add_row("3", "[red]Split 2[/red]", "Top: Center, Bottom: Bottom-Right (Facecam)")
        console.print(table)
        
        choice = Prompt.ask("\n[bold yellow]Select crop mode[/bold yellow]", choices=["1", "2", "3"], default="1")
    else:
        # Fallback to standard UI
        print("\n=== Crop Mode ===")
        print("1. Default (center crop)")
        print("2. Split 1 (top: center, bottom: bottom-left (facecam))")
        print("3. Split 2 (top: center, bottom: bottom-right ((facecam))")
        
        while True:
            choice = input("\nSelect crop mode (1-3): ").strip()
            if choice in ["1", "2", "3"]:
                break
            print("Invalid choice. Please enter 1, 2, or 3.")
            
    if choice == "1":
        crop_mode = "default"
        crop_desc = "Default center crop"
    elif choice == "2":
        crop_mode = "split_left"
        crop_desc = "Split crop (bottom-left facecam)"
    elif choice == "3":
        crop_mode = "split_right"
        crop_desc = "Split crop (bottom-right facecam)"
    
    rprint(f"[bold green]Selected:[/bold green] {crop_desc}")
    
    # Ask for subtitle
    if console:
        use_subtitle = Confirm.ask(f"\n[bold yellow]Add auto subtitle using Faster-Whisper? ({WHISPER_MODEL})[/bold yellow]", default=True)
    else:
        print("\n=== Auto Subtitle ===")
        print(f"Available model: {WHISPER_MODEL} (~{get_model_size(WHISPER_MODEL)})")
        subtitle_choice = input("Add auto subtitle using Faster-Whisper? (y/n): ").strip().lower()
        use_subtitle = subtitle_choice in ["y", "yes"]
    
    if use_subtitle:
        rprint(f"[bold green]✅ Subtitle enabled (Model: {WHISPER_MODEL}, Bahasa Indonesia)[/bold green]")
    else:
        rprint("[bold red]❌ Subtitle disabled[/bold red]")
    
    print()
    
    # Check dependencies
    cek_dependensi(install_whisper=use_subtitle)
    
    # Check Gemini Configuration
    rprint("\n[bold cyan]=== Auto-Titling AI ===[/bold cyan]")
    if GEMINI_API_KEY and GEMINI_API_KEY != "MASUKKAN_API_KEY_DI_SINI":
        rprint("[bold green]✅ Gemini AI siap digunakan[/bold green]")
    else:
        rprint("[bold red]❌ Gemini AI belum dikonfigurasi[/bold red] [dim](Edit run.py dan masukkan GEMINI_API_KEY)[/dim]\n[yellow]Akan menggunakan Generator Judul bawaan sementara.[/yellow]")
        
    print()
    if console:
        link = Prompt.ask("[bold magenta]Link YT/Video ID[/bold magenta]")
    else:
        link = input("Link YT: ").strip()
        
    video_id = extract_video_id(link)

    if not video_id:
        rprint("[bold red]Invalid YouTube link.[/bold red]")
        return

    if console:
        with console.status("[bold green]Membaca YouTube heatmap data...", spinner="dots"):
            heatmap_data = ambil_most_replayed(video_id)
            if not heatmap_data:
                # Coba Fallback AI
                heatmap_data = ambil_ai_curation(video_id)
            if heatmap_data:
                total_duration = get_duration(video_id)
    else:
        heatmap_data = ambil_most_replayed(video_id)
        if not heatmap_data:
            heatmap_data = ambil_ai_curation(video_id)
        if heatmap_data:
            total_duration = get_duration(video_id)

    if not heatmap_data:
        rprint("[bold red]No high-engagement segments found, dan AI Fallback gagal.[/bold red]")
        return
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