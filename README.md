<div align="center">

# Subtitle Auto Translator

**Automated subtitle extraction and AI translation for MKV media libraries, powered by Google Gemini (with local LibreTranslate fallback).**

![Status](https://img.shields.io/badge/Status-Active-16A34A?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-2563EB?style=flat-square)
![Casco Digital](https://img.shields.io/badge/Casco-Digital-111827?style=flat-square)
![Python](https://img.shields.io/badge/Python-3-3776AB?style=flat-square&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-Primary-8E75B2?style=flat-square&logo=googlegemini&logoColor=white)
![LibreTranslate](https://img.shields.io/badge/LibreTranslate-Fallback-00D9FF?style=flat-square)

</div>

---

Automated subtitle translation pipeline for media collections (MKVs). The workflow checks if your target language subtitle is already present (embedded in the MKV or alongside the media as an external `.srt`). If missing, it extracts the source subtitle track via MKVToolNix, translates the cues in batches using Google's **Gemini Flash** API, and automatically falls back to a self-hosted **LibreTranslate** container if needed.

## Highlights

- **Gemini Flash Primary** — Uses `gemini-2.5-flash-lite` (or custom model) via Google AI Studio. Context-aware, preserves formatting tags (`<i>`, speaker labels, sound effects), and easily runs within Google's generous free tier (15 requests/min, 1M tokens/day).
- **Offline Fallback** — Containerized LibreTranslate handles translation locally if Gemini is unreachable or rate-limited.
- **Smart & Idempotent** — Skips already translated files, respects embedded target tracks, and processes only what is needed.
- **Configurable Language Pairs** — Set any source and target languages via `.env` (defaults to English &rarr; Brazilian Portuguese).
- **Batch Processing** — Recursively scans your movies and TV directories.
- **Automation / Cron Ready** — Lightweight execution, perfect for a nightly cron job alongside your `*arr` stack.

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/cascodigital/subtitle-auto-translator.git
cd subtitle-auto-translator
```

### 2. Configure Environment

Copy the template:

```bash
cp .env.example .env
```

Edit `.env` and provide your Google Gemini API Key (get one for free at [Google AI Studio](https://aistudio.google.com/)):

```dotenv
GEMINI_API_KEY=AIzaSyYourKeyHere
```

*(Optional: change source or target languages; defaults are `en` to `pt-BR`).*

### 3. Adjust Paths in `docker-compose.yml`

Map your media library volumes:

```yaml
volumes:
  - .:/app
  - /path/to/your/movies:/movies
  - /path/to/your/tv:/tv
```

### 4. Build and Run

```bash
# Start local LibreTranslate fallback service
docker compose up -d libretranslate

# Build the translator container
docker compose build legendas

# Run the batch translation job
docker compose run --rm legendas
```

---

## Scheduling (Cron)

To keep your library continuously translated without manual intervention, add a job to your crontab:

```bash
crontab -e
```

Example (runs daily at 03:00):

```bash
0 3 * * * cd /path/to/subtitle-auto-translator && /usr/bin/docker compose run --rm legendas >> /var/log/subtitle_translator.log 2>&1
```

---

## How It Works

```
                     +---------------------------+
                     | Scan /movies, /tv for MKV |
                     +-------------+-------------+
                                   |
                  +----------------v---------------+
                  | Target subtitle already exists |
                  | (embedded or .<target>.srt)?   |
                  +----------------+---------------+
                                   |
                       +-----------+-----------+
                      Yes                     No
                       |                       |
                +------v-------+      +--------v---------+
                |     Skip     |      | Extract source   |
                +--------------+      | subtitle (SRT)   |
                                      +--------+---------+
                                               |
                                      +--------v---------+
                                      | Translate via    |
                                      | Gemini API       |
                                      +--------+---------+
                                               |
                                     Success? -+-- No --> Fallback to LibreTranslate
                                               |
                                      +--------v---------+
                                      | Save external    |
                                      | .<target>.srt    |
                                      +------------------+
```

1. **Scan**: Recursively inspects `/movies`, `/tv`, and any paths listed in `EXTRA_MEDIA_DIRS`.
2. **Check**: Checks if the target language subtitle already exists (e.g. `.pt-BR.srt` or embedded in the MKV).
3. **Extraction**: If missing, inspects MKV tracks and extracts the source text track (e.g. `.en.srt`) using `mkvextract`.
   - *If only bitmap PGS/SUP tracks are found*: If `ENABLE_PGS_OCR=1`, it automatically extracts the stream, runs OCR via Tesseract/pgsrip, and verifies text through a dictionary quality gate. If disabled, it logs to `/app/temp/legendassup.txt` for manual review.
4. **Batch Translation**: Batches subtitle cues and translates via the Gemini API, preserving cue numbering, line breaks, formatting tags, and timestamps.
5. **Fallback**: If Gemini encounters an issue, it gracefully routes cues to the local LibreTranslate service.
6. **Save**: Writes the translated `.srt` alongside the media file.

---

## Bitmap Subtitles (PGS / SUP) OCR

Many Blu-ray rips, Remuxes, and anime releases only contain image-based bitmap subtitles (**PGS / SUP** format) rather than text tracks. 

This project includes an integrated OCR pipeline powered by `pgsrip` and `tesseract-ocr`:

> [!IMPORTANT]
> **Hardware & Memory Notice:**
> Subtitle OCR requires rendering and analyzing high-resolution bitmaps, which can consume **1.5 GB to 3.0 GB of RAM** during peak processing.
> * **Default behavior:** `ENABLE_PGS_OCR=0` (disabled). This ensures safe operation on low-RAM single-board computers (Raspberry Pi, Orange Pi, lightweight VPS) without risk of Out-Of-Memory (OOM) kills.
> * **To enable:** If your host machine has sufficient RAM (e.g., standard x86 servers, desktop PCs, or NAS with 4GB+ RAM), set `ENABLE_PGS_OCR=1` in your `.env`.
> * **Lexical Quality Gate:** To prevent OCR noise and corrupt subtitles from being sent to Gemini, recognized tokens are verified against a system dictionary (`/usr/share/dict/words`). Sample files, forced-only foreign dialogue tracks, and garbled output are safely rejected.

---

## Configuration Reference

All settings can be customized in your `.env` file:

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | *(Required)* | Google AI Studio API key |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Model name (`gemini-2.5-flash-lite`, `gemini-2.5-flash`, etc.) |
| `GEMINI_MAX_CHARS` | `2500` | Maximum character count per API batch |
| `GEMINI_TIMEOUT` | `180` | Request timeout in seconds |
| `GEMINI_RETRIES` | `3` | Number of attempts before triggering fallback |
| `SOURCE_LANG` | `en` | Source language file suffix code (e.g. `en`) |
| `TARGET_LANG` | `pt-BR` | Target language file suffix code (e.g. `pt-BR`, `es`, `fr`, `de`) |
| `SOURCE_LANG_NAME` | `English` | Human-readable language name for the Gemini prompt |
| `TARGET_LANG_NAME` | `Brazilian Portuguese` | Target language name for the Gemini prompt |
| `LIBRETRANSLATE_SOURCE` | `en` | ISO source language code for LibreTranslate |
| `LIBRETRANSLATE_TARGET` | `pt` | ISO target language code for LibreTranslate |
| `ENABLE_PGS_OCR` | `0` | Enable bitmap PGS/SUP OCR (`1` = enabled, `0` = disabled) |
| `PGS_MIN_CUES` | `80` | Minimum cue count required by the OCR quality gate |
| `PGS_MAX_BAD_PCT` | `15.0` | Maximum percentage of unrecognized dictionary words before rejecting OCR |
| `PROCESS_EXISTING_SOURCE_ONLY` | `0` | If `1`, only translates existing `.en.srt` files on disk (skips MKV scan) |
| `EXTRA_MEDIA_DIRS` | `""` | Additional colon-separated folders to scan (e.g. `/anime:/documentaries`) |

---

## Tech Stack

- **Python 3** + `requests` + `tqdm`
- **[Google Gemini API](https://ai.google.dev/)** (`gemini-2.5-flash-lite`)
- **[MKVToolNix](https://mkvtoolnix.download/)** (built into the Docker image)
- **[LibreTranslate](https://github.com/LibreTranslate/LibreTranslate)** (local offline fallback)
- **Docker & Docker Compose**

---

## License

Released under the [MIT License](LICENSE).

Developed with 🐢 (and coffee) by [Casco Digital](https://github.com/cascodigital).
