FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY music_bot ./music_bot
COPY web ./web
RUN pip install --no-cache-dir . && useradd --system --uid 10001 music && mkdir -p /app/data && chown music:music /app/data
USER music
CMD ["python", "-m", "music_bot.main"]
