FROM python:3.12-slim
ARG MUSIC_WITH_YOUTUBE=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY music_bot ./music_bot
COPY web ./web
RUN pip install --no-cache-dir . \
    && if [ "$MUSIC_WITH_YOUTUBE" = "true" ]; then \
         apt-get update \
         && apt-get install -y --no-install-recommends ffmpeg \
         && rm -rf /var/lib/apt/lists/* \
         && pip install --no-cache-dir ".[youtube]"; \
       fi \
    && useradd --system --uid 10001 music \
    && mkdir -p /app/data \
    && chown music:music /app/data
USER music
CMD ["python", "-m", "music_bot.main"]
