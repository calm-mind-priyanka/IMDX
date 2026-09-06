FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN chmod +x /app/start.sh

RUN tesseract --version \
    && python -c "import pytesseract; print('pytesseract', pytesseract.get_tesseract_version())"

CMD ["bash", "/app/start.sh"]
