FROM python:3.12-slim
ARG MUSIC_WITH_YOUTUBE=false
ARG MUSIC_WITH_FINGERPRINT=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# System packages depend only on the build profile, not application sources.
RUN set -eu; \
    set --; \
    if [ "$MUSIC_WITH_YOUTUBE" = "true" ]; then set -- "$@" ffmpeg; fi; \
    if [ "$MUSIC_WITH_FINGERPRINT" = "true" ]; then set -- "$@" libchromaprint-tools; fi; \
    if [ "$#" -gt 0 ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends "$@"; \
        rm -rf /var/lib/apt/lists/*; \
    fi; \
    useradd --system --uid 10001 music; \
    mkdir -p /app/data; \
    chown music:music /app/data

# pyproject.toml remains the sole dependency manifest. Install build tools,
# runtime dependencies and enabled extras before copying application code.
COPY pyproject.toml ./
RUN python -c 'import os, subprocess, sys, tomllib; p = tomllib.load(open("pyproject.toml", "rb")); requirements = p["build-system"]["requires"] + p["project"]["dependencies"]; requirements += p["project"]["optional-dependencies"]["youtube"] if os.environ.get("MUSIC_WITH_YOUTUBE") == "true" else []; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *requirements])'

# A source-only change rebuilds just the small local package. A web-only
# change does not even reinstall that package.
COPY README.md ./
COPY music_bot ./music_bot
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
COPY web ./web
USER music
CMD ["python", "-m", "music_bot.main"]
