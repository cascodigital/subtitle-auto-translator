FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        mkvtoolnix \
        tesseract-ocr \
        tesseract-ocr-eng \
        ca-certificates \
        libglib2.0-0 \
        wamerican \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y opencv-python 2>/dev/null || true \
    && pip install --no-cache-dir opencv-python-headless

COPY translate_subtitles.py gemini_api_translate_srt.py pgs_ocr.py ./

CMD ["python", "/app/translate_subtitles.py"]
