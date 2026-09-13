#!/usr/bin/env python3
"""
pgs_ocr: Extract and OCR bitmap subtitles (PGS / SUP) from MKV files.

Designed as an optional pre-translation stage. If a media file only has
bitmap PGS/SUP subtitles, this module extracts the stream, runs OCR via pgsrip,
and verifies text quality through a lexical dictionary gate before outputting
a text .srt file for translation.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

MIN_CUES = int(os.environ.get("PGS_MIN_CUES", "80"))
MAX_BAD_PCT = float(os.environ.get("PGS_MAX_BAD_PCT", "15.0"))
OCR_TIMEOUT = int(os.environ.get("PGS_OCR_TIMEOUT", "3600"))

DICT_PATHS = [
    Path("/usr/share/dict/words"),
    Path("/usr/share/dict/american-english"),
    Path("/usr/share/dict/british-english"),
]


def is_ocr_available() -> bool:
    """Check if pgsrip and mkvextract are available in PATH."""
    return bool(shutil.which("pgsrip")) and bool(shutil.which("mkvextract"))


def load_dictionary() -> Optional[set]:
    """Load system word dictionary for lexical quality verification."""
    for path in DICT_PATHS:
        if path.exists():
            try:
                words = {
                    w.strip().lower()
                    for w in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if w.strip()
                }
                if words:
                    return words
            except Exception as e:
                logging.warning("Error reading dictionary from %s: %s", path, e)
    return None


def evaluate_ocr(srt_path: Path, words: Optional[set]) -> Tuple[bool, str, dict]:
    """
    Quality gate: validates generated SRT against cue counts and lexical dictionary.
    Rejects samples, forced-only foreign dialogue tracks, and corrupted/gibberish OCR.
    """
    try:
        raw = srt_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"Failed to read OCR output: {e}", {}

    blocks = [b for b in re.split(r"\n\s*\n", raw.strip()) if b.strip()]
    cues_text = []
    for b in blocks:
        lines = [x for x in b.strip().split("\n") if x.strip()]
        timing_lines = [x for x in lines if "-->" in x]
        if not timing_lines:
            continue
        idx = lines.index(timing_lines[0])
        cues_text.append(" ".join(lines[idx + 1:]).strip())

    n = len(cues_text)
    if n < MIN_CUES:
        return False, f"Only {n} cues (minimum {MIN_CUES}) - likely sample or forced track", {"cues": n}

    empty_count = sum(1 for t in cues_text if not t)
    if empty_count > n * 0.15:
        return False, f"Too many empty cues: {empty_count}/{n}", {"cues": n, "empty": empty_count}

    if not words:
        return True, "Dictionary not found - lexical gate skipped", {"cues": n, "bad_pct": None}

    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", " ".join(cues_text))
    tokens = [t.strip("'-") for t in tokens if len(t) > 1]
    if not tokens:
        return False, "No alphabetic tokens recognized", {"cues": n}

    bad_tokens = [
        t for t in tokens
        if t.lower() not in words and t.lower().rstrip("'s") not in words
    ]
    pct = (100.0 * len(bad_tokens)) / len(tokens)
    stats = {"cues": n, "tokens": len(tokens), "bad_pct": round(pct, 1)}

    if pct > MAX_BAD_PCT:
        return False, f"{pct:.1f}% tokens outside dictionary (limit {MAX_BAD_PCT}%)", stats

    return True, "Quality check passed", stats


def ocr_mkv_track(
    mkv_path: str,
    track_id: int,
    output_srt: str,
    lang_code: str = "en",
    work_dir: Optional[str] = None
) -> bool:
    """
    Extracts a PGS/SUP track from an MKV, runs OCR via pgsrip,
    evaluates quality, and atomically writes the final SRT file.
    """
    if not is_ocr_available():
        logging.warning("pgsrip or mkvextract not found. OCR cannot be performed.")
        return False

    temp_folder = Path(work_dir) if work_dir else Path(output_srt).parent / ".ocr_tmp"
    temp_folder.mkdir(parents=True, exist_ok=True)

    pid = os.getpid()
    sup_path = temp_folder / f"extract_{pid}.sup"
    temp_srt_path = temp_folder / f"extract_{pid}.srt"

    for f in (sup_path, temp_srt_path):
        if f.exists():
            f.unlink()

    try:
        logging.info("Extracting PGS stream from %s (track %s)...", mkv_path, track_id)
        t0 = time.time()
        extract_cmd = ["mkvextract", "tracks", mkv_path, f"{track_id}:{sup_path}"]
        res = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=1800)
        if res.returncode != 0 or not sup_path.exists() or sup_path.stat().st_size == 0:
            logging.error("mkvextract failed for %s: %s", mkv_path, res.stderr.strip()[:200])
            return False

        size_kb = sup_path.stat().st_size // 1024
        logging.info("Extracted %d KB SUP in %.1fs. Starting pgsrip OCR (lang=%s)...", size_kb, time.time() - t0, lang_code)

        t1 = time.time()
        ocr_cmd = ["pgsrip", "-l", lang_code, str(sup_path)]
        res_ocr = subprocess.run(ocr_cmd, capture_output=True, text=True, timeout=OCR_TIMEOUT)
        
        # pgsrip produces output alongside input file named <base>.srt
        generated_srt = sup_path.with_suffix(".srt")
        if not generated_srt.exists():
            logging.error(
                "OCR failed or produced no SRT (exit=%d): stdout=%s stderr=%s",
                res_ocr.returncode,
                res_ocr.stdout.strip()[-200:],
                res_ocr.stderr.strip()[-200:]
            )
            return False

        logging.info("pgsrip finished in %.1fs. Running lexical quality gate...", time.time() - t1)
        words = load_dictionary()
        ok, reason, stats = evaluate_ocr(generated_srt, words)
        logging.info("OCR quality check: ok=%s, reason=%s, stats=%s", ok, reason, stats)

        if not ok:
            logging.warning("OCR output REJECTED for %s: %s", mkv_path, reason)
            return False

        # Atomically write to final destination
        partial_dest = Path(output_srt + ".partial")
        shutil.copyfile(generated_srt, partial_dest)
        os.replace(partial_dest, output_srt)
        logging.info("OCR successfully produced: %s", output_srt)
        return True

    except subprocess.TimeoutExpired:
        logging.error("OCR process timed out for %s", mkv_path)
        return False
    except Exception as exc:
        logging.error("Unexpected error during OCR of %s: %s", mkv_path, exc)
        return False
    finally:
        for f in (sup_path, temp_srt_path, sup_path.with_suffix(".srt")):
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass
