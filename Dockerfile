FROM python:3.12-slim

# Non-interactive + clean logs
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Audio (PulseAudio works in Docker if host passes socket)
ENV ALSA_CARD=0
ENV PULSE_SERVER=unix:/run/pulse/native

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    bluez \
    bluetooth \
    dbus \
    ffmpeg \
    curl \
    ca-certificates \
    alsa-utils \
    pulseaudio \
    pulseaudio-utils \
    unzip \
    && rm -rf /var/lib/apt/lists/*


# -------------------------
# Install Deno (YouTube JS runtime)
# -------------------------
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:${PATH}"


# -------------------------
# Python deps
# -------------------------
# requirements.txt pins the tested versions; pyproject.toml declares what to install.
WORKDIR /app
COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY yt_player ./yt_player
RUN pip install --no-cache-dir -c requirements.txt ".[server]"

ENV DATA_DIR=/data
ENV SERVICE_HOST=0.0.0.0
ENV SERVICE_PORT=5454

EXPOSE 5454 5455

CMD ["yt-player", "serve"]
