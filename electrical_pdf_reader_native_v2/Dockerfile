FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir -r requirements.txt
COPY app.py recognize.py ocr.py schedules.py vision.py ./
RUN useradd -m -u 10001 reader && mkdir /data && chown reader:reader /data /app
USER reader
ENV DRAWING_DATA_DIR=/data
EXPOSE 9912
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","9912","--workers","1"]
