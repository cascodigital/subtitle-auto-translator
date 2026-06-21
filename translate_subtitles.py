#!/usr/bin/env python3
import os
import re
import subprocess
import json
import logging
import requests
import time
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("/app/subtitle_translator.log"),
        logging.StreamHandler()
    ]
)

MOVIES_DIR = "/movies"
TV_DIR = "/tv"
SUP_LOG_FILE = "/app/temp/legendassup.txt"
TEMP_DIR = "/app/temp"
TRANSLATE_API_URL = os.environ.get("TRANSLATE_API_URL", "http://libretranslate:5000/translate")
PROCESS_EXISTING_EN_ONLY = os.environ.get("PROCESS_EXISTING_EN_ONLY", "0") == "1"
EXTRA_MEDIA_DIRS = [
    path.strip()
    for path in os.environ.get("EXTRA_MEDIA_DIRS", "").split(":")
    if path.strip()
]
SEARCH_DIRS = [MOVIES_DIR, TV_DIR] + EXTRA_MEDIA_DIRS

os.makedirs(TEMP_DIR, exist_ok=True)

def get_all_mkv_files():
    mkv_files = []
    for search_dir in SEARCH_DIRS:
        logging.info("Buscando arquivos .mkv em %s", search_dir)
        if not os.path.isdir(search_dir):
            logging.warning("Diretorio nao existe: %s", search_dir)
            continue
        for root, _, files in os.walk(search_dir):
            for file in files:
                if file.endswith(".mkv"):
                    mkv_files.append(os.path.join(root, file))
    logging.info("Total de arquivos .mkv encontrados: %d", len(mkv_files))
    return mkv_files

def get_pending_en_subtitles():
    pending = []
    for search_dir in SEARCH_DIRS:
        logging.info("Buscando legendas .en.srt pendentes em %s", search_dir)
        if not os.path.isdir(search_dir):
            logging.warning("Diretorio nao existe: %s", search_dir)
            continue
        for root, _, files in os.walk(search_dir):
            for file in files:
                if not file.endswith(".en.srt"):
                    continue
                en_subtitle = os.path.join(root, file)
                pt_br_subtitle = en_subtitle[:-7] + ".pt-BR.srt"
                if not os.path.exists(pt_br_subtitle):
                    pending.append(en_subtitle)
    pending.sort()
    logging.info("Total de legendas .en.srt pendentes: %d", len(pending))
    for subtitle in pending:
        logging.info("Pendente: %s", subtitle)
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
                    "track_name": track.get("properties", {}).get("track_name")
                })
        return subtitle_tracks
    except subprocess.TimeoutExpired:
        logging.error("Timeout ao ler %s", mkv_file)
        return []
    except subprocess.CalledProcessError as e:
        logging.error("Erro ao obter info de %s: %s", mkv_file, e.stderr)
        return []
    except json.JSONDecodeError:
        logging.error("Erro JSON ao ler %s", mkv_file)
        return []

def extract_subtitle(mkv_file, track_id, output_file):
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        cmd = ["mkvextract", "tracks", mkv_file, f"{track_id}:{output_file}"]
        logging.info("Extraindo: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logging.error("mkvextract falhou: %s", result.stderr)
            return False
        if os.path.exists(output_file):
            logging.info("Legenda extraida: %s", output_file)
            return True
        logging.warning("Arquivo nao encontrado apos extracao: %s", output_file)
        return False
    except Exception as e:
        logging.error("Erro ao extrair legenda: %s", e)
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
            payload = {"q": processed_text, "source": "en", "target": "pt"}
            headers = {"Content-Type": "application/json"}
            response = requests.post(TRANSLATE_API_URL, data=json.dumps(payload), headers=headers, timeout=60)
            if response.status_code == 200:
                result = response.json()
                translated_text = result.get("translatedText", processed_text)
                return postprocess_text(translated_text, tags)
            else:
                logging.warning("Falha na traducao, status %d: %s", response.status_code, response.text)
        except Exception as e:
            logging.warning("Tentativa %d/%d falhou: %s", attempt + 1, retries, e)
            if attempt < retries - 1:
                time.sleep(5)
    logging.error("Traducao falhou apos %d tentativas", retries)
    return text

def translate_subtitle(input_file, output_file):
    try:
        if not os.path.exists(input_file):
            logging.error("Arquivo de legenda nao existe: %s", input_file)
            return False
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
        logging.info("Legenda traduzida salva em: %s", output_file)
        return True
    except Exception as e:
        logging.error("Erro ao traduzir legenda %s: %s", input_file, e)
        return False

def process_file(mkv_file):
    logging.info("Processando arquivo: %s", mkv_file)
    base_path = os.path.splitext(mkv_file)[0]
    en_subtitle = f"{base_path}.en.srt"
    pt_br_subtitle = f"{base_path}.pt-BR.srt"

    if os.path.exists(pt_br_subtitle):
        logging.info("Legenda em PT-BR ja existe: %s", pt_br_subtitle)
        return

    if not os.path.exists(en_subtitle):
        subtitle_tracks = get_subtitle_tracks(mkv_file)
        if not subtitle_tracks:
            logging.warning("Nenhuma faixa de legenda em %s", mkv_file)
            return
        logging.info("Faixas encontradas: %s", subtitle_tracks)

        portuguese_text_track = None
        for track in subtitle_tracks:
            language = track.get("language")
            track_name = (track.get("track_name") or "").lower()
            codec = (track.get("codec") or "").lower()
            is_portuguese = language in ["por", "pt", "pt-BR", "pt-br", "portuguese", "portugues"]
            is_brazilian = "brazil" in track_name or "brasil" in track_name or "br" == track_name.strip()
            if is_portuguese and not any(marker in codec for marker in ["sup", "hdmv", "pgs"]):
                if portuguese_text_track is None or is_brazilian:
                    portuguese_text_track = track
                if is_brazilian:
                    break
        if portuguese_text_track:
            logging.info("Extraindo legenda PT embutida: %s (ID: %s)", mkv_file, portuguese_text_track["id"])
            if extract_subtitle(mkv_file, portuguese_text_track["id"], pt_br_subtitle):
                logging.info("Legenda PT-BR extraida com sucesso: %s", pt_br_subtitle)
            else:
                logging.error("Falha ao extrair legenda PT embutida: %s", mkv_file)
            return

        english_text_track = None
        sup_tracks = []
        for track in subtitle_tracks:
            language = track.get("language")
            codec = track.get("codec", "").lower()
            if language in ["eng", "en", "english"]:
                if "sup" in codec or "hdmv" in codec or "pgs" in codec:
                    sup_tracks.append(track)
                else:
                    english_text_track = track
                    break

        if not english_text_track and sup_tracks:
            with open(SUP_LOG_FILE, "a") as log:
                log.write(f"{mkv_file}\n")
            logging.info("Arquivo com legendas SUP: %s", mkv_file)
            return

        if not english_text_track:
            logging.info("Nenhuma legenda EN encontrada: %s", mkv_file)
            return

        logging.info("Extraindo legenda EN: %s (ID: %s)", mkv_file, english_text_track["id"])
        if not extract_subtitle(mkv_file, english_text_track["id"], en_subtitle):
            logging.error("Falha ao extrair legenda: %s", mkv_file)
            return
    else:
        logging.info("Legenda EN ja extraida: %s", en_subtitle)

    logging.info("Traduzindo legenda: %s", en_subtitle)
    if translate_subtitle(en_subtitle, pt_br_subtitle):
        logging.info("Legenda traduzida com sucesso: %s", pt_br_subtitle)
    else:
        logging.error("Falha ao traduzir: %s", en_subtitle)

def process_existing_en_subtitle(en_subtitle):
    logging.info("Processando legenda existente: %s", en_subtitle)
    pt_br_subtitle = en_subtitle[:-7] + ".pt-BR.srt"
    if os.path.exists(pt_br_subtitle):
        logging.info("Legenda em PT-BR ja existe: %s", pt_br_subtitle)
        return
    if translate_subtitle(en_subtitle, pt_br_subtitle):
        logging.info("Legenda traduzida com sucesso: %s", pt_br_subtitle)
    else:
        logging.error("Falha ao traduzir: %s", en_subtitle)

def main():
    logging.info("Iniciando o processo de traducao de legendas")
    os.makedirs(os.path.dirname(SUP_LOG_FILE), exist_ok=True)

    try:
        test_response = requests.post(
            TRANSLATE_API_URL,
            data=json.dumps({"q": "Hello", "source": "en", "target": "pt"}),
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        logging.info("Backend local LibreTranslate: %d - %s", test_response.status_code, test_response.text)
        if test_response.status_code != 200:
            return
    except Exception as e:
        logging.error("LibreTranslate local inacessivel: %s", e)
        return

    if PROCESS_EXISTING_EN_ONLY:
        pending_subtitles = get_pending_en_subtitles()
        for subtitle in tqdm(pending_subtitles, total=len(pending_subtitles), desc="Traduzindo legendas EN existentes"):
            process_existing_en_subtitle(subtitle)
        logging.info("Processo de traducao de legendas concluido")
        return

    mkv_files = get_all_mkv_files()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as executor:
        list(tqdm(executor.map(process_file, mkv_files), total=len(mkv_files), desc="Processando arquivos"))

    logging.info("Processo de traducao de legendas concluido")

if __name__ == "__main__":
    main()
