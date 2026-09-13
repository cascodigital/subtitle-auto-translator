#!/usr/bin/env python3
import os
import re
import subprocess
import json
import logging
import requests
import time
from tqdm import tqdm

LOG_DIR = "/app" if os.path.exists("/app") else os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, "subtitle_translator.log")
TEMP_DIR = os.path.join(LOG_DIR, "temp")
SUP_LOG_FILE = os.path.join(TEMP_DIR, "legendassup.txt")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

MOVIES_DIR = "/movies"
TV_DIR = "/tv"
TRANSLATE_API_URL = os.environ.get("TRANSLATE_API_URL", "http://libretranslate:5000/translate")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_MAX_CHARS = os.environ.get("GEMINI_MAX_CHARS", "2500")
GEMINI_TIMEOUT = os.environ.get("GEMINI_TIMEOUT", "180")
GEMINI_RETRIES = os.environ.get("GEMINI_RETRIES", "3")
GEMINI_SCRIPT = os.environ.get("GEMINI_SCRIPT", "/app/gemini_api_translate_srt.py")

# Configurable language settings (defaults preserve previous EN -> PT-BR behavior)
SOURCE_LANG = os.environ.get("SOURCE_LANG", "en")
TARGET_LANG = os.environ.get("TARGET_LANG", "pt-BR")
SOURCE_LANG_NAME = os.environ.get("SOURCE_LANG_NAME", "English")
TARGET_LANG_NAME = os.environ.get("TARGET_LANG_NAME", "Brazilian Portuguese")
LIBRETRANSLATE_SOURCE = os.environ.get("LIBRETRANSLATE_SOURCE", "en")
LIBRETRANSLATE_TARGET = os.environ.get("LIBRETRANSLATE_TARGET", "pt")

SOURCE_SUBTITLE_SUFFIX = os.environ.get("SOURCE_SUBTITLE_SUFFIX", f".{SOURCE_LANG}.srt")
TARGET_SUBTITLE_SUFFIX = os.environ.get("TARGET_SUBTITLE_SUFFIX", f".{TARGET_LANG}.srt")

# Optional Bitmap (PGS/SUP) Subtitle OCR stage
ENABLE_PGS_OCR = os.environ.get("ENABLE_PGS_OCR", "0") == "1"

PROCESS_EXISTING_SOURCE_ONLY = (
    os.environ.get("PROCESS_EXISTING_SOURCE_ONLY", "0") == "1"
    or os.environ.get("PROCESS_EXISTING_EN_ONLY", "0") == "1"
)

EXTRA_MEDIA_DIRS = [
    path.strip()
    for path in os.environ.get("EXTRA_MEDIA_DIRS", "").split(":")
    if path.strip()
]
SEARCH_DIRS = [MOVIES_DIR, TV_DIR] + EXTRA_MEDIA_DIRS

# Parse target language tags for checking embedded tracks in MKV
target_codes_env = os.environ.get("TARGET_LANG_CODES", "")
if target_codes_env.strip():
    TARGET_LANG_CODES = [c.strip().lower() for c in target_codes_env.split(",") if c.strip()]
else:
    t_lower = TARGET_LANG.lower()
    if t_lower.startswith("pt"):
        TARGET_LANG_CODES = ["por", "pt", "pt-br", "portuguese", "portugues"]
    elif t_lower.startswith("es"):
        TARGET_LANG_CODES = ["spa", "es", "spanish", "espanol", "español"]
    elif t_lower.startswith("fr"):
        TARGET_LANG_CODES = ["fra", "fre", "fr", "french", "francais", "français"]
    elif t_lower.startswith("de"):
        TARGET_LANG_CODES = ["deu", "ger", "de", "german", "deutsch"]
    elif t_lower.startswith("it"):
        TARGET_LANG_CODES = ["ita", "it", "italian", "italiano"]
    elif t_lower.startswith("ja"):
        TARGET_LANG_CODES = ["jpn", "ja", "japanese"]
    elif t_lower.startswith("en"):
        TARGET_LANG_CODES = ["eng", "en", "english"]
    else:
        TARGET_LANG_CODES = [t_lower, t_lower.split("-")[0]]

# Parse source language tags for checking text tracks to extract
source_codes_env = os.environ.get("SOURCE_LANG_CODES", "")
if source_codes_env.strip():
    SOURCE_LANG_CODES = [c.strip().lower() for c in source_codes_env.split(",") if c.strip()]
else:
    s_lower = SOURCE_LANG.lower()
    if s_lower.startswith("en"):
        SOURCE_LANG_CODES = ["eng", "en", "english"]
    elif s_lower.startswith("ja"):
        SOURCE_LANG_CODES = ["jpn", "ja", "japanese"]
    elif s_lower.startswith("es"):
        SOURCE_LANG_CODES = ["spa", "es", "spanish"]
    else:
        SOURCE_LANG_CODES = [s_lower, s_lower.split("-")[0]]

os.makedirs(TEMP_DIR, exist_ok=True)


def get_all_mkv_files():
    mkv_files = []
    for search_dir in SEARCH_DIRS:
        logging.info("Scanning for .mkv files in %s", search_dir)
        if not os.path.isdir(search_dir):
            logging.warning("Directory does not exist: %s", search_dir)
            continue
        for root, _, files in os.walk(search_dir):
            for file in files:
                if file.endswith(".mkv"):
                    mkv_files.append(os.path.join(root, file))
    logging.info("Total .mkv files found: %d", len(mkv_files))
    return mkv_files


def get_pending_source_subtitles():
    pending = []
    for search_dir in SEARCH_DIRS:
        logging.info("Scanning for pending %s subtitles in %s", SOURCE_SUBTITLE_SUFFIX, search_dir)
        if not os.path.isdir(search_dir):
            logging.warning("Directory does not exist: %s", search_dir)
            continue
        for root, _, files in os.walk(search_dir):
            for file in files:
                if not file.endswith(SOURCE_SUBTITLE_SUFFIX):
                    continue
                src_subtitle = os.path.join(root, file)
                target_subtitle = src_subtitle[:-len(SOURCE_SUBTITLE_SUFFIX)] + TARGET_SUBTITLE_SUFFIX
                if not os.path.exists(target_subtitle):
                    pending.append(src_subtitle)
    pending.sort()
    logging.info("Total pending %s subtitles: %d", SOURCE_SUBTITLE_SUFFIX, len(pending))
    for subtitle in pending:
        logging.info("Pending: %s", subtitle)
    return pending


def get_subtitle_tracks(mkv_file):
    try:
        result = subprocess.run(
            ["mkvmerge", "-J", mkv_file],
            capture_output=True, text=True, check=True, timeout=30
        )
        info = json.loads(result.stdout)
        subtitle_tracks = []
        for track in info.get("tracks", []):
            if track.get("type") == "subtitles":
                subtitle_tracks.append({
                    "id": track.get("id"),
                    "codec": track.get("codec"),
                    "language": track.get("properties", {}).get("language"),
                    "track_name": track.get("properties", {}).get("track_name"),
                    "forced": bool(track.get("properties", {}).get("forced_track")),
                })
        return subtitle_tracks
    except subprocess.TimeoutExpired:
        logging.error("Timeout reading %s", mkv_file)
        return []
    except subprocess.CalledProcessError as e:
        logging.error("Error reading track info from %s: %s", mkv_file, e.stderr)
        return []
    except json.JSONDecodeError:
        logging.error("JSON decode error reading %s", mkv_file)
        return []


def extract_subtitle(mkv_file, track_id, output_file):
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        cmd = ["mkvextract", "tracks", mkv_file, f"{track_id}:{output_file}"]
        logging.info("Extracting: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logging.error("mkvextract failed: %s", result.stderr)
            return False
        if os.path.exists(output_file):
            logging.info("Subtitle extracted: %s", output_file)
            return True
        logging.warning("Output file not found after extraction: %s", output_file)
        return False
    except Exception as e:
        logging.error("Error extracting subtitle: %s", e)
        return False


def preprocess_text(text):
    tags = {}
    tag_counter = 0
    def replace_tag(match):
        nonlocal tag_counter
        tag = match.group(0)
        placeholder = f"[[[TAG{tag_counter}]]]"
        tags[placeholder] = tag
        tag_counter += 1
        return placeholder
    processed_text = re.sub(r'<[^>]+>', replace_tag, text)
    return processed_text, tags


def postprocess_text(text, tags):
    processed_text = text
    for placeholder, tag in tags.items():
        processed_text = processed_text.replace(placeholder, tag)
    processed_text = re.sub(r'[\[\]_]*TAG\d+[\[\]_]*', '', processed_text)
    return processed_text


def translate_text(text, retries=3):
    for attempt in range(retries):
        try:
            processed_text, tags = preprocess_text(text)
            payload = {"q": processed_text, "source": LIBRETRANSLATE_SOURCE, "target": LIBRETRANSLATE_TARGET}
            headers = {"Content-Type": "application/json"}
            response = requests.post(TRANSLATE_API_URL, data=json.dumps(payload), headers=headers, timeout=60)
            if response.status_code == 200:
                result = response.json()
                translated_text = result.get("translatedText", processed_text)
                return postprocess_text(translated_text, tags)
            else:
                logging.warning("Translation failure, status %d: %s", response.status_code, response.text)
        except Exception as e:
            logging.warning("Attempt %d/%d failed: %s", attempt + 1, retries, e)
            if attempt < retries - 1:
                time.sleep(5)
    logging.error("Translation failed after %d attempts", retries)
    return text


def translate_subtitle(input_file, output_file):
    try:
        if not os.path.exists(input_file):
            logging.error("Subtitle file does not exist: %s", input_file)
            return False
        if GEMINI_API_KEY:
            cmd = [
                "python3",
                GEMINI_SCRIPT,
                input_file,
                output_file,
                "--model",
                GEMINI_MODEL,
                "--max-chars",
                GEMINI_MAX_CHARS,
                "--timeout",
                GEMINI_TIMEOUT,
                "--retries",
                GEMINI_RETRIES,
                "--source-lang",
                SOURCE_LANG_NAME,
                "--target-lang",
                TARGET_LANG_NAME,
                "--libre-source",
                LIBRETRANSLATE_SOURCE,
                "--libre-target",
                LIBRETRANSLATE_TARGET,
            ]
            logging.info(
                "Translating via Gemini API (%s -> %s): model=%s max_chars=%s timeout=%s retries=%s",
                SOURCE_LANG_NAME,
                TARGET_LANG_NAME,
                GEMINI_MODEL,
                GEMINI_MAX_CHARS,
                GEMINI_TIMEOUT,
                GEMINI_RETRIES,
            )
            result = subprocess.run(cmd, timeout=7200)
            if result.returncode == 0 and os.path.exists(output_file):
                return True
            logging.error("Gemini failed with exit code %s; falling back to local LibreTranslate", result.returncode)

        with open(input_file, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        blocks = re.split(r'\n\s*\n', content)

        def translate_block(indexed_block):
            idx, block = indexed_block
            if not block.strip():
                return idx, block
            lines = block.strip().split('\n')
            if len(lines) < 3:
                return idx, block
            number = lines[0]
            timestamp = lines[1]
            text_to_translate = '\n'.join(lines[2:])
            translated_text = translate_text(text_to_translate)
            time.sleep(0.1)
            return idx, f"{number}\n{timestamp}\n{translated_text}"

        from concurrent.futures import ThreadPoolExecutor, as_completed
        indexed_blocks = list(enumerate(blocks))
        results = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(translate_block, ib): ib[0] for ib in indexed_blocks}
            for future in as_completed(futures):
                idx, translated = future.result()
                results[idx] = translated
        translated_blocks = [results[i] for i in sorted(results) if results[i].strip()]

        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n\n'.join(translated_blocks))
        logging.info("Translated subtitle saved to: %s", output_file)
        return True
    except Exception as e:
        logging.error("Error translating subtitle %s: %s", input_file, e)
        return False


def process_file(mkv_file):
    logging.info("Processing file: %s", mkv_file)
    base_path = os.path.splitext(mkv_file)[0]
    source_subtitle = f"{base_path}{SOURCE_SUBTITLE_SUFFIX}"
    target_subtitle = f"{base_path}{TARGET_SUBTITLE_SUFFIX}"

    if os.path.exists(target_subtitle):
        logging.info("Target subtitle already exists: %s", target_subtitle)
        return

    if not os.path.exists(source_subtitle):
        subtitle_tracks = get_subtitle_tracks(mkv_file)
        if not subtitle_tracks:
            logging.warning("No subtitle tracks found in %s", mkv_file)
            return
        logging.info("Subtitle tracks found: %s", subtitle_tracks)

        has_target = any(
            track.get("language") and track.get("language").lower() in TARGET_LANG_CODES
            for track in subtitle_tracks
        )
        if has_target:
            logging.info("MKV already has embedded target subtitle (%s): %s", TARGET_LANG, mkv_file)
            return

        source_text_track = None
        sup_tracks = []
        for track in subtitle_tracks:
            language = (track.get("language") or "").lower()
            codec = (track.get("codec") or "").lower()
            if language in SOURCE_LANG_CODES:
                if "sup" in codec or "hdmv" in codec or "pgs" in codec:
                    sup_tracks.append(track)
                else:
                    source_text_track = track
                    break

        if not source_text_track and sup_tracks:
            if ENABLE_PGS_OCR:
                try:
                    from pgs_ocr import ocr_mkv_track, is_ocr_available
                    if is_ocr_available():
                        best_sup = next((t for t in sup_tracks if not t.get("forced")), sup_tracks[0])
                        logging.info("ENABLE_PGS_OCR enabled. Attempting OCR on track ID %s (%s)...", best_sup["id"], best_sup.get("track_name", ""))
                        if ocr_mkv_track(mkv_file, best_sup["id"], source_subtitle, lang_code=SOURCE_LANG, work_dir=TEMP_DIR):
                            logging.info("OCR generated %s successfully.", source_subtitle)
                        else:
                            with open(SUP_LOG_FILE, "a") as log:
                                log.write(f"{mkv_file}\n")
                            return
                    else:
                        logging.warning("ENABLE_PGS_OCR is enabled, but pgsrip/mkvextract is missing. Logging to SUP file.")
                        with open(SUP_LOG_FILE, "a") as log:
                            log.write(f"{mkv_file}\n")
                        return
                except ImportError:
                    logging.warning("pgs_ocr module not found. Logging to SUP file.")
                    with open(SUP_LOG_FILE, "a") as log:
                        log.write(f"{mkv_file}\n")
                    return
            else:
                with open(SUP_LOG_FILE, "a") as log:
                    log.write(f"{mkv_file}\n")
                logging.info("File with bitmap/SUP subtitles logged for review: %s", mkv_file)
                return

        if not source_text_track:
            logging.info("No source text subtitle (%s) found in %s", SOURCE_LANG, mkv_file)
            return

        logging.info("Extracting %s subtitle: %s (ID: %s)", SOURCE_LANG, mkv_file, source_text_track["id"])
        if not extract_subtitle(mkv_file, source_text_track["id"], source_subtitle):
            logging.error("Failed to extract subtitle from: %s", mkv_file)
            return
    else:
        logging.info("Source subtitle already extracted: %s", source_subtitle)

    logging.info("Translating subtitle: %s -> %s", source_subtitle, target_subtitle)
    if translate_subtitle(source_subtitle, target_subtitle):
        logging.info("Subtitle translated successfully: %s", target_subtitle)
    else:
        logging.error("Failed to translate subtitle: %s", source_subtitle)


def process_existing_source_subtitle(src_subtitle):
    logging.info("Processing existing subtitle: %s", src_subtitle)
    target_subtitle = src_subtitle[:-len(SOURCE_SUBTITLE_SUFFIX)] + TARGET_SUBTITLE_SUFFIX
    if os.path.exists(target_subtitle):
        logging.info("Target subtitle already exists: %s", target_subtitle)
        return
    if translate_subtitle(src_subtitle, target_subtitle):
        logging.info("Subtitle translated successfully: %s", target_subtitle)
    else:
        logging.error("Failed to translate subtitle: %s", src_subtitle)


def main():
    logging.info("Starting subtitle translation process")
    logging.info(
        "Languages: %s (%s) -> %s (%s) | Model: %s | Batch Max Chars: %s",
        SOURCE_LANG_NAME, SOURCE_LANG,
        TARGET_LANG_NAME, TARGET_LANG,
        GEMINI_MODEL, GEMINI_MAX_CHARS
    )
    os.makedirs(os.path.dirname(SUP_LOG_FILE), exist_ok=True)

    if GEMINI_API_KEY:
        logging.info("Primary translation backend: Gemini API (%s)", GEMINI_MODEL)
    else:
        try:
            test_response = requests.post(
                TRANSLATE_API_URL,
                data=json.dumps({"q": "Hello", "source": LIBRETRANSLATE_SOURCE, "target": LIBRETRANSLATE_TARGET}),
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            logging.info("LibreTranslate API health check: %d - %s", test_response.status_code, test_response.text)
        except Exception as e:
            logging.error("Translation API unreachable: %s", e)
            return

    if PROCESS_EXISTING_SOURCE_ONLY:
        pending_subtitles = get_pending_source_subtitles()
        for subtitle in tqdm(pending_subtitles, total=len(pending_subtitles), desc=f"Translating existing {SOURCE_LANG} subtitles"):
            process_existing_source_subtitle(subtitle)
        logging.info("Subtitle translation process finished")
        return

    mkv_files = get_all_mkv_files()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as executor:
        list(tqdm(executor.map(process_file, mkv_files), total=len(mkv_files), desc="Processing media files"))

    logging.info("Subtitle translation process finished")


if __name__ == "__main__":
    main()
