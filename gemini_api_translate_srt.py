#!/usr/bin/env python3
import argparse
import contextlib
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


TIMESTAMP_RE = re.compile(r"^\d\d:\d\d:\d\d,\d{3}\s+-->\s+\d\d:\d\d:\d\d,\d{3}")


class RecitationError(RuntimeError):
    """Gemini refused the batch with finishReason=RECITATION (copyright filter). Deterministic - do not retry."""


class QuotaError(RuntimeError):
    """Gemini HTTP 429 (rate limit or monthly spending cap). Global - abort the file, never write bogus output."""


@dataclass
class Cue:
    raw_index: str
    timestamp: str
    text: str


def parse_srt(path: Path) -> tuple[list[Cue], str]:
    data = path.read_bytes()
    bom = "\ufeff" if data.startswith(b"\xef\xbb\xbf") else ""
    text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[Cue] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        if TIMESTAMP_RE.match(lines[0]):
            raw_index = str(len(cues) + 1)
            timestamp = lines[0]
            text_lines = lines[1:]
        else:
            raw_index = lines[0].strip()
            timestamp = lines[1].strip() if len(lines) > 1 else ""
            text_lines = lines[2:]
        if not TIMESTAMP_RE.match(timestamp):
            raise ValueError(f"Invalid SRT timestamp near cue {raw_index!r}: {timestamp!r}")
        cues.append(Cue(raw_index=raw_index, timestamp=timestamp, text="\n".join(text_lines)))
    return cues, bom


def make_batches(cues: list[Cue], max_chars: int) -> list[list[tuple[int, str]]]:
    batches: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    size = 0
    for idx, cue in enumerate(cues):
        item_size = len(cue.text) + 40
        if current and size + item_size > max_chars:
            batches.append(current)
            current = []
            size = 0
        current.append((idx, cue.text))
        size += item_size
    if current:
        batches.append(current)
    return batches


def extract_json(raw: str) -> list[dict]:
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("[")
    if start == -1:
        raise ValueError(f"No JSON array found in model output: {raw[:500]}")
    candidate = raw[start:]
    try:
        parsed, _ = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        # Cheap models occasionally emit invalid JSON escapes such as "\?".
        sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", candidate)
        parsed, _ = json.JSONDecoder().raw_decode(sanitized)
    if not isinstance(parsed, list):
        raise ValueError(f"Decoded JSON is not an array: {type(parsed).__name__}")
    return parsed


def usage_counts(data: dict) -> dict[str, int]:
    usage = data.get("usageMetadata") or {}
    return {
        "prompt_tokens": int(usage.get("promptTokenCount", 0) or 0),
        "completion_tokens": int(usage.get("candidatesTokenCount", 0) or 0),
        "total_tokens": int(usage.get("totalTokenCount", 0) or 0),
    }


@contextlib.contextmanager
def hard_timeout(seconds: int):
    def _raise_timeout(_signum, _frame):
        raise TimeoutError(f"Gemini request exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def read_api_key(api_key_file: Optional[Path]) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        return api_key.strip()
    if api_key_file and api_key_file.exists():
        for line in api_key_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("GEMINI_API_KEY is required")


def call_gemini(batch: list[tuple[int, str]], api_key: str, model: str, timeout: int) -> tuple[dict[int, str], dict[str, int]]:
    payload = [{"i": idx, "text": text} for idx, text in batch]
    prompt = (
        "Translate the JSON array subtitle texts from English to Brazilian Portuguese. "
        "Return only a valid JSON array, no markdown. Preserve every i exactly. Preserve line breaks, "
        "SRT tags such as <i>, speaker labels, punctuation, and sound-effect parentheses. "
        "Schema: [{\"i\": number, \"text\": string}]\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
    }
    query = urllib.parse.urlencode({"key": api_key})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?{query}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with hard_timeout(timeout):
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:160]
            except Exception:
                pass
            raise QuotaError(f"Gemini 429 quota/spending cap: {detail}")
        raise
    candidate = data["candidates"][0]
    if "content" not in candidate:
        finish_reason = candidate.get("finishReason", "UNKNOWN")
        safety = candidate.get("safetyRatings", [])
        msg = f"Gemini returned no content: finishReason={finish_reason} safetyRatings={safety}"
        if finish_reason == "RECITATION":
            raise RecitationError(msg)
        raise RuntimeError(msg)
    parts = candidate["content"]["parts"]
    content = "".join(part.get("text", "") for part in parts)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = extract_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("items") or parsed.get("translations") or parsed.get("result")
    if not isinstance(parsed, list):
        raise ValueError(f"Unexpected JSON response shape: {type(parsed).__name__}")
    translated = {int(item["i"]): str(item["text"]) for item in parsed}
    expected = {idx for idx, _ in batch}
    if not expected.issubset(set(translated)):
        missing = sorted(expected - set(translated))
        extra = sorted(set(translated) - expected)
        raise ValueError(f"Index mismatch: missing={missing[:10]} extra={extra[:10]}")
    extra = sorted(set(translated) - expected)
    if extra:
        print(f"ignoring extra indexes: {extra[:10]}", flush=True)
        translated = {idx: translated[idx] for idx in expected}
    return translated, usage_counts(data)


def translate_batch(
    batch_no: int,
    total_batches: int,
    batch: list[tuple[int, str]],
    api_key: str,
    model: str,
    retries: int,
    timeout: int,
) -> tuple[dict[int, str], dict[str, int]]:
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            print(f"batch {batch_no}/{total_batches} attempt {attempt}/{retries}", flush=True)
            translated, usage = call_gemini(batch, api_key, model, timeout)
            for key in usage_total:
                usage_total[key] += usage.get(key, 0)
            return translated, usage_total
        except QuotaError:
            print(f"batch {batch_no}/{total_batches} QUOTA/CAP 429 - Gemini budget exhausted, aborting", flush=True)
            raise
        except RecitationError:
            print(f"batch {batch_no}/{total_batches} RECITATION - copyright filter, no retry; falling back", flush=True)
            raise
        except TimeoutError as exc:
            last_error = exc
            print(f"batch {batch_no}/{total_batches} timeout: {exc}", flush=True)
            if attempt < retries:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(f"Gemini batch {batch_no}/{total_batches} failed after {retries} attempts: {last_error}")
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, RuntimeError, json.JSONDecodeError, KeyError) as exc:
            last_error = exc
            print(f"batch {batch_no}/{total_batches} failed: {type(exc).__name__}: {exc}", flush=True)
            if attempt < retries:
                time.sleep(3 * attempt)
                continue
            raise RuntimeError(f"Gemini batch {batch_no}/{total_batches} failed after {retries} attempts: {last_error}")
    raise RuntimeError(f"Gemini batch {batch_no}/{total_batches} failed after {retries} attempts: {last_error}")


def libretranslate_batch(batch: list[tuple[int, str]], url: str) -> dict[int, str]:
    """Fallback translator: local LibreTranslate (free). Per-cue; keeps original on failure."""
    out: dict[int, str] = {}
    for idx, text in batch:
        if not text.strip():
            out[idx] = text
            continue
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps({"q": text, "source": "en", "target": "pt", "format": "text"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            out[idx] = data.get("translatedText", text) or text
        except Exception as exc:
            print(f"  libretranslate failed for cue {idx + 1}: {type(exc).__name__}: {exc}; keeping original", flush=True)
            out[idx] = text
    return out


def recover_batch(
    batch_no: int,
    total_batches: int,
    batch: list[tuple[int, str]],
    api_key: str,
    model: str,
    timeout: int,
    libre_url: str,
) -> dict[int, str]:
    """Per-cue recovery for a batch the model refused (usually RECITATION).
    A single cue rarely trips the copyright filter, so retry each alone on Gemini;
    whatever is still refused falls to LibreTranslate, then to the original text."""
    out: dict[int, str] = {}
    still_blocked: list[tuple[int, str]] = []
    for idx, text in batch:
        if not text.strip():
            out[idx] = text
            continue
        try:
            single, _ = call_gemini([(idx, text)], api_key, model, timeout)
            out[idx] = single.get(idx, text)
        except QuotaError:
            raise
        except RecitationError:
            still_blocked.append((idx, text))
        except Exception as exc:
            print(f"  cue {idx + 1} single retry failed: {type(exc).__name__}: {exc}", flush=True)
            still_blocked.append((idx, text))
    if still_blocked:
        print(f"  {len(still_blocked)} cue(s) still blocked - LibreTranslate/original", flush=True)
        out.update(libretranslate_batch(still_blocked, libre_url))
    return out


def write_srt(cues: list[Cue], bom: str, output: Path) -> None:
    parts = []
    for cue in cues:
        parts.append(f"{cue.raw_index}\n{cue.timestamp}\n{cue.text}".rstrip())
    output.write_text(bom + "\n\n".join(parts) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate SRT text via Gemini API.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="gemini-2.5-flash-lite")
    parser.add_argument("--max-chars", type=int, default=9000)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--api-key-file", type=Path, default=Path("/dados/dockers/claude/ai/config/mimi_api.txt"))
    args = parser.parse_args()

    api_key = read_api_key(args.api_key_file)
    libre_url = os.environ.get("TRANSLATE_API_URL", "http://127.0.0.1:15000/translate")

    cues, bom = parse_srt(args.input)
    batches = make_batches(cues, args.max_chars)
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    fallback_batches = 0
    failed_batches = 0
    progress_output = args.output.with_name(args.output.name + ".partial")
    print(f"input={args.input}", flush=True)
    print(f"output={args.output}", flush=True)
    print(f"cues={len(cues)} batches={len(batches)} model={args.model}", flush=True)

    for batch_no, batch in enumerate(batches, start=1):
        first = batch[0][0] + 1
        last = batch[-1][0] + 1
        print(f"batch {batch_no}/{len(batches)} cues {first}-{last}", flush=True)
        try:
            translated, usage = translate_batch(batch_no, len(batches), batch, api_key, args.model, args.retries, args.timeout)
            for key in usage_total:
                usage_total[key] += usage.get(key, 0)
        except QuotaError:
            print(f"batch {batch_no}/{len(batches)} ABORT: Gemini 429 quota/spending cap - stopping file, nothing written as final (retry after cap resets)", flush=True)
            raise
        except Exception as exc:
            print(f"batch {batch_no}/{len(batches)} Gemini batch refused ({type(exc).__name__}) - per-cue recovery", flush=True)
            fallback_batches += 1
            try:
                translated = recover_batch(batch_no, len(batches), batch, api_key, args.model, args.timeout, libre_url)
            except Exception as fexc:
                print(f"batch {batch_no}/{len(batches)} fallback also failed: {type(fexc).__name__}: {fexc}; keeping original", flush=True)
                failed_batches += 1
                translated = {idx: text for idx, text in batch}
        for idx, text in translated.items():
            cues[idx].text = text
        write_srt(cues, bom, progress_output)

    print(f"usage={json.dumps(usage_total, sort_keys=True)}", flush=True)
    print(f"fallback_batches={fallback_batches} failed_batches={failed_batches}", flush=True)
    write_srt(cues, bom, args.output)
    if progress_output.exists():
        progress_output.unlink()
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
