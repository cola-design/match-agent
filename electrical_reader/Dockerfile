# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS dwg-build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    python -c "import urllib.request;urllib.request.urlretrieve('https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.xz','source.tar.xz')"
RUN echo '62ebb73b984f865960f20ed26619ea5f8789d5e3fd088fa40a2598384da81275  source.tar.xz' | sha256sum -c - && \
    tar -xf source.tar.xz && cd libredwg-0.14 && \
    ./configure --disable-shared --disable-bindings --disable-docs && make -j2

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk libpcre2-8-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir -r requirements.txt
COPY --from=dwg-build /build/libredwg-0.14/programs/dwgread /usr/local/bin/dwgread
COPY --from=dwg-build /build/libredwg-0.14/COPYING /usr/share/licenses/libredwg/COPYING
COPY --from=dwg-build /build/source.tar.xz /usr/share/licenses/libredwg/source.tar.xz
COPY --chmod=644 app.py reader.py domain.py ./
RUN useradd -m -u 10001 reader && mkdir /data && chown reader:reader /data /app
USER reader
ENV DRAWING_DATA_DIR=/data DWGREAD_BIN=/usr/local/bin/dwgread CJK_FONT_FILE=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
EXPOSE 9910
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","9910","--workers","1"]
