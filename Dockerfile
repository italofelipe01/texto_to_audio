# Texto para Áudio — imagem pronta com FFmpeg completo, eSpeak NG e voz offline Piper.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TTA_DATA_DIR=/data \
    TTA_PIPER_VOICES_DIR=/data/piper-voices:/opt/piper-voices

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg espeak-ng fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[web,pdf,piper]"

# Voz neural offline pré-instalada, usada automaticamente quando não há internet.
# Para pular: docker build --build-arg PIPER_VOICES="" .
ARG PIPER_VOICES="pt_BR-faber-medium"
RUN mkdir -p /opt/piper-voices \
    && for voice in $PIPER_VOICES; do \
         python -m piper.download_voices --data-dir /opt/piper-voices "$voice" \
         || echo "aviso: não foi possível baixar a voz $voice (será baixada no primeiro uso)"; \
       done

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1

ENTRYPOINT ["entrypoint.sh"]
CMD ["tta", "serve", "--host", "0.0.0.0", "--port", "8000"]
