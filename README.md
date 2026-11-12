# SubGen

[![CI](https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/ci.yaml/badge.svg)](https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/ci.yaml)
[![Container](https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/container.yaml/badge.svg)](https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/container.yaml)
[![GHCR](https://img.shields.io/badge/GHCR-subgen-blue?logo=docker)](https://github.com/Fahed-Alhammadi/SubGen/pkgs/container/subgen)
[![License: Elastic-2.0](https://img.shields.io/badge/License-Elastic--2.0-blue.svg)](LICENSE)

SubGen is an offline-first, self-hosted subtitle generation service for Linux media servers and
NAS environments. It scans media libraries, transcribes speech directly from media audio,
translates it locally when needed, and writes subtitle sidecars beside the source files.

One unified container image supports CPU and NVIDIA GPU execution.

> **Status:** Early MVP. Review the [current limitations](#current-limitations) before using SubGen
> with irreplaceable media libraries.

## Features

- Web dashboard with persistent media inventory and restart-safe jobs
- Unlimited media libraries and multiple target languages per library
- SRT, ASS, and WebVTT output
- Direct audio transcription using `faster-whisper`
- Fully local NLLB translation with runtime network downloads disabled
- One CPU/CUDA-capable image with automatic device selection
- Local disk, NFS, and SMB/CIFS support
- Sidecar names such as `Movie Name.ar.srt`
- Scheduled scans and protection from processing files still being copied
- Atomic writes, regeneration, and audited subtitle deletion
- Environment-configured administrator account
- Non-root UID/GID execution and reverse-proxy-friendly networking

## Architecture

```text
Browser
   |
HTTPS reverse proxy
   |
SubGen container
   |-- Web interface and API
   |-- Media scanner
   |-- SQLite job queue
   |-- faster-whisper transcription
   |-- Local NLLB translation
   `-- Subtitle writer
            |
       Media libraries
```

SubGen always transcribes the media audio. Existing and embedded subtitles are not used as a
transcription source. Every configured language and format produces an external sidecar.

## Requirements

- Linux with Docker Engine and Docker Compose v2
- Write permission for every configured media library
- Locally prepared Whisper and translation models
- For GPU processing: an NVIDIA GPU, current driver, and NVIDIA Container Toolkit

The published image currently targets `linux/amd64`.

## Quick start

```bash
git clone https://github.com/Fahed-Alhammadi/SubGen.git
cd SubGen
cp .env.example .env
mkdir -p data/config data/models media
chown -R 1000:1000 data media
```

Change the required credentials in `.env`:

```env
SUBGEN_ADMIN_USERNAME=admin
SUBGEN_ADMIN_PASSWORD=replace-with-a-long-unique-password
SUBGEN_SECRET_KEY=replace-with-at-least-32-random-characters
```

Generate a signing key with `openssl rand -hex 32`, prepare the offline models, then start SubGen:

```bash
docker compose up -d
```

Open `http://127.0.0.1:8080` or route the service through an HTTPS reverse proxy.

## Container image

Releases are published through GitHub Container Registry:

```bash
docker pull ghcr.io/fahed-alhammadi/subgen:0.1.2
```

The release workflow publishes these aliases:

```text
ghcr.io/fahed-alhammadi/subgen:0.1.2
ghcr.io/fahed-alhammadi/subgen:0.1
ghcr.io/fahed-alhammadi/subgen:0
ghcr.io/fahed-alhammadi/subgen:latest
```

## CPU and NVIDIA GPU

The provided `docker-compose.yaml` enables NVIDIA access:

```yaml
services:
  subgen:
    gpus: all
```

With `SUBGEN_WHISPER_DEVICE=auto`, SubGen selects CUDA when available. On CPU-only hosts, comment
out `gpus: all`; the same image will automatically use CPU.

## Multiple media libraries

Every library must be mounted at a unique path below `/media`. A shared parent can contain any
number of library folders, or unrelated host paths can be mounted separately:

```yaml
services:
  subgen:
    volumes:
      - /srv/movies:/media/movies
      - /mnt/nas/television:/media/tv
      - /mnt/archive/anime:/media/anime
```

Add `/media/movies`, `/media/tv`, and `/media/anime` as separate libraries in the web interface.
All mounts must be writable because subtitles are stored beside the media.

NFS and SMB shares may be mounted by the host or declared as Compose volumes. Never commit network
share credentials; use a protected credentials file or Docker secrets.

## Offline models

SubGen blocks runtime Hugging Face downloads. Prepare the model directory before processing:

```text
data/models/
├── whisper/
└── translation/
    └── facebook/
        └── nllb-200-distilled-600M/
```

Default settings:

```env
SUBGEN_WHISPER_MODEL=small
SUBGEN_WHISPER_DEVICE=auto
SUBGEN_WHISPER_COMPUTE_TYPE=auto
SUBGEN_TRANSLATION_MODEL=facebook/nllb-200-distilled-600M
```

If a required model is missing, the job fails visibly instead of sending content externally.

## Subtitle naming

```text
Movie Name (2026).mkv
Movie Name (2026).ar.srt
Movie Name (2026).en.vtt
Movie Name (2026).fr.ass
```

## Security

- Replace all credentials before first launch.
- Keep `.env`, media, models, databases, and private tests outside Git.
- Use HTTPS for remote access and keep the default localhost bind behind a local reverse proxy.
- The container runs with the configured `PUID`/`PGID`, drops Linux capabilities, enables
  `no-new-privileges`, and never mounts the Docker socket.
- Subtitle deletion is restricted to supported files inside configured libraries.
- Back up `/config/subgen.db` and generated subtitles.

## Configuration

See [.env.example](.env.example) for the full template.

| Variable | Purpose |
| --- | --- |
| `PUID`, `PGID` | Host filesystem identity |
| `SUBGEN_ADMIN_USERNAME` | Administrator login |
| `SUBGEN_ADMIN_PASSWORD` | Administrator password, minimum 12 characters |
| `SUBGEN_SECRET_KEY` | Session-signing key, minimum 32 characters |
| `SUBGEN_MEDIA_PATH` | Common host media directory |
| `SUBGEN_MODEL_PATH` | Offline model directory |
| `SUBGEN_CONFIG_PATH` | Persistent configuration/database directory |
| `SUBGEN_WHISPER_DEVICE` | `auto`, `cpu`, or `cuda` |

## Updating

Back up `/config`, then run:

```bash
docker compose pull
docker compose up -d
```

Use an explicit version instead of `latest` for reproducible deployments.

## Current limitations

Work remaining before a stable `1.0` release includes:

- CSRF protection, login throttling, trusted proxy enforcement, session revocation, and TOTP
- Formal database migrations and integrated backup/restore
- Model installation and validation tooling
- Richer settings, audit, preview, and library management interfaces
- Duration-based progress, priorities, and cancellation
- Wider language mappings and real-media integration validation
- NVIDIA testing on a dedicated self-hosted runner

## License

SubGen is licensed under the [Elastic License 2.0](LICENSE) (`Elastic-2.0`). You may use, copy,
modify, and redistribute the software subject to its terms. You may not offer SubGen to third
parties as a hosted or managed service that exposes a substantial set of its functionality, and
you must preserve its license and copyright notices.

---

© 2026 Fahed Alhammadi. All rights reserved.
