<div align="center">

<h1>SubGen</h1>

<p><strong>Offline subtitle generation for self-hosted media libraries</strong></p>

<p>
  <a href="https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/ci.yaml"><img src="https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/ci.yaml/badge.svg" alt="CI status"></a>
  <a href="https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/container.yaml"><img src="https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/container.yaml/badge.svg" alt="Container build status"></a>
  <a href="https://github.com/Fahed-Alhammadi/SubGen/pkgs/container/subgen"><img src="https://img.shields.io/badge/GHCR-subgen-blue?logo=docker" alt="GitHub Container Registry"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Elastic--2.0-blue.svg" alt="Elastic License 2.0"></a>
</p>

</div>

SubGen is an offline-first, self-hosted subtitle generation service for Linux media servers and
NAS environments. It scans media libraries, transcribes speech directly from media audio,
translates it locally when needed, and writes subtitle sidecars beside the source files.

One unified container image supports CPU and NVIDIA GPU execution.

> **Status:** Active pre-1.0 development. Pin a versioned image, keep backups, and review the
> [current limitations](#current-limitations) before using SubGen with irreplaceable libraries.

## Features

- Web dashboard with persistent media inventory and restart-safe jobs
- Unlimited media libraries and multiple target languages per library
- SRT, ASS, and WebVTT output
- Direct audio transcription using `faster-whisper`
- Fully local Hugging Face translation with no implicit runtime downloads
- One CPU/CUDA-capable image with automatic device selection
- Local disk, NFS, and SMB/CIFS support
- Sidecar names such as `Movie Name.ar.srt`
- Scheduled scans and protection from processing files still being copied
- Atomic writes, regeneration, and audited subtitle deletion
- Built-in Hugging Face model downloads, selection, inventory, and deletion
- Pluggable local translation through Hugging Face, Ollama, or LibreTranslate
- Provider connection tests and persistent pipeline settings
- Duration-based job progress, queue priorities, cancellation, retry, and regeneration
- Built-in subtitle preview/editor with atomic saves
- Searchable administrative audit history and integrated database backup/restore
- CSRF protection, login throttling, session revocation, security headers, and optional TOTP
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
       |-- Migrated SQLite job queue and audit log
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
- Enough persistent storage for the selected transcription and translation models
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

Generate a signing key with `openssl rand -hex 32`, then start SubGen:

```bash
docker compose up -d
```

Open `http://127.0.0.1:8080`, sign in with the credentials from `.env`, and use **Model vault**
to install the models you want. For remote access, route SubGen through an HTTPS reverse proxy.

### First-run checklist

1. Open **Models** and download a Whisper transcription model.
2. Download a translation model if you intend to use the local Hugging Face provider.
3. Open **Providers**, choose the transcription and translation models, configure the translation
   provider, test it, and save the pipeline settings.
4. Open **Libraries**, add each mounted container path with its target languages and output
   formats, then select **Scan now**.
5. Follow queued, active, completed, and failed jobs from **Overview**.

Model downloads can be large. Keep the Models page open to monitor transfer status; models are
stored persistently below the configured `SUBGEN_MODEL_PATH`.

## Container image

Releases are published through GitHub Container Registry:

```bash
docker pull ghcr.io/fahed-alhammadi/subgen:0.3.1
```

The release workflow publishes these aliases:

```text
ghcr.io/fahed-alhammadi/subgen:0.3.1
ghcr.io/fahed-alhammadi/subgen:0.3
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

## Model management

The **Model vault** in the web interface can download, list, select, and delete Hugging Face models.
Downloads occur only when an administrator explicitly starts one. Transcription and translation
jobs always load models from persistent local storage and never download them implicitly.

Models use this directory layout:

```text
data/models/
├── whisper/
└── translation/
    └── facebook/
        └── nllb-200-distilled-600M/
```

Default settings:

```env
SUBGEN_WHISPER_MODEL=Systran/faster-whisper-small
SUBGEN_WHISPER_DEVICE=auto
SUBGEN_WHISPER_COMPUTE_TYPE=auto
SUBGEN_TRANSLATION_MODEL=facebook/nllb-200-distilled-600M
```

If a required model is missing, the job fails visibly instead of sending content externally.

## Translation providers

Configure and test providers from **Provider studio**:

- **Hugging Face:** dedicated translation models stored locally under `/models`
- **Ollama:** open-source LLMs through the Ollama `/api/chat` API with validated structured output
- **LibreTranslate:** a local or private LibreTranslate HTTP API

SubGen remains offline-first. Ollama and LibreTranslate default to Compose-network hostnames; no
public translation service is enabled automatically.

When Ollama or LibreTranslate runs in a separate container, attach both services to the same
Docker network and use its service name in the provider URL. When it runs directly on the host,
use a host address reachable from the SubGen container rather than `127.0.0.1`.

## Subtitle naming

```text
Movie Name (2026).mkv
Movie Name (2026).ar.srt
Movie Name (2026).en.vtt
Movie Name (2026).fr.ass
```

## Queue and subtitle editor

The Overview page reports transcription progress against media duration. Queued jobs are processed
by priority and then creation time. Administrators can reprioritize, cancel, retry, or regenerate a
job without entering the container.

Completed jobs include an **Edit** action. The editor validates cue timing and text limits, then
atomically replaces the sidecar so a failed write cannot leave a partial subtitle. Edits,
regeneration, deletion, queue controls, settings changes, and authentication events appear under
**Activity**.

## Backup and restore

Use **System → Backup and restore** to download a consistent SQLite snapshot. A validated restore
is staged inside `/config` and restarts SubGen; the restored database is applied before workers
start. Backups do not contain media or downloaded models, so protect those separately.

## Security

- Replace all credentials before first launch.
- Optionally set `SUBGEN_TOTP_SECRET` to a Base32 authenticator secret. When configured, login
  requires a current six-digit TOTP code.
- Keep `.env`, media, models, databases, and private tests outside Git.
- Use HTTPS for remote access and keep the default localhost bind behind a local reverse proxy.
- The container runs with the configured `PUID`/`PGID`, drops Linux capabilities, enables
  `no-new-privileges`, and never mounts the Docker socket.
- Subtitle deletion is restricted to supported files inside configured libraries.
- Mutating API requests require a session-bound CSRF token. Login attempts are rate limited and
  logout revokes all outstanding sessions for the single administrator.
- Forwarded client addresses are honored only from `SUBGEN_TRUSTED_PROXIES`.
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
| `SUBGEN_WHISPER_COMPUTE_TYPE` | Whisper compute type; `auto` is recommended |
| `SUBGEN_BIND_ADDRESS`, `SUBGEN_PORT` | Published web interface address and port |
| `SUBGEN_SECURE_COOKIES` | Require HTTPS session cookies; keep enabled behind HTTPS |
| `SUBGEN_SESSION_HOURS` | Maximum authenticated session lifetime |
| `SUBGEN_LOGIN_ATTEMPTS` | Failed attempts allowed within the login window |
| `SUBGEN_LOGIN_WINDOW_SECONDS` | Login throttling window |
| `SUBGEN_TRUSTED_PROXIES` | Comma-separated proxy addresses allowed to supply client IPs |
| `SUBGEN_TOTP_SECRET` | Optional Base32 secret for authenticator-based two-factor login |

## Updating

Back up `/config`, then run:

```bash
docker compose pull
docker compose up -d
```

Use an explicit version instead of `latest` for reproducible deployments.

To stop SubGen without deleting its database, settings, or downloaded models:

```bash
docker compose down
```

Runtime directories are intentionally ignored by Git. Remove them only when you deliberately want
to reset the local installation, and back up anything you need first.

## Validation and operational boundaries

Every release runs linting, Compose validation, Docker build checks, a published-image health smoke
test, SBOM generation, and signed build-provenance attestation. A manual NVIDIA workflow is included
for a self-hosted runner labeled `nvidia`; GPU owners can use it to validate CUDA visibility and the
media runtime against any image tag.

SubGen now maps more than 60 common language codes for local NLLB translation. Ollama and
LibreTranslate may support additional pairs depending on the selected model or server. As with any
speech-recognition system, validate timing and translation quality on representative real media
before enabling unattended processing across an irreplaceable library.

## License

SubGen is licensed under the [Elastic License 2.0](LICENSE) (`Elastic-2.0`). You may use, copy,
modify, and redistribute the software subject to its terms. You may not offer SubGen to third
parties as a hosted or managed service that exposes a substantial set of its functionality, and
you must preserve its license and copyright notices.

---

© 2026 Fahed Alhammadi. All rights reserved.
