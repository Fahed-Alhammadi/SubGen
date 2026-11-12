# SubGen

SubGen is an offline-first Linux web application that scans media libraries, transcribes each
missing language from the media audio, translates locally when necessary, and writes external
SRT, ASS, or WebVTT sidecars beside the media.

## Current MVP

- Single administrator configured through environment variables
- Multiple libraries, target languages, and output formats
- Persistent SQLite inventory and restart-safe job queue
- Local `faster-whisper` transcription; existing subtitles are never used as a source
- Local NLLB translation with network access disabled
- One container image for CPU and NVIDIA GPU execution
- Safe atomic subtitle writes and library-confined deletion API
- Non-root UID/GID operation and reverse-proxy-ready binding

## Prepare models

SubGen intentionally refuses to download models at runtime. Populate the host model directory
before deployment:

```text
data/models/whisper/       faster-whisper model cache or model directory
data/models/translation/facebook/nllb-200-distilled-600M/
```

The translation directory must contain a complete Transformers model and tokenizer. A future
model-management command can download these during installation, but production runtime remains
offline.

## Run

```bash
cp .env.example .env
mkdir -p data/config data/models media
chown -R 1000:1000 data media
docker compose up -d
```

The supplied `docker-compose.yaml` is ready for NVIDIA and runs one unified container image.
Install NVIDIA Container Toolkit on the host before starting it. `SUBGEN_WHISPER_DEVICE=auto`
selects CUDA at runtime. The unified image is intentionally larger than a CPU-only image.

Open `http://127.0.0.1:8080`. When HTTPS is terminated at a reverse proxy, keep
`SUBGEN_SECURE_COOKIES=true`. Bind publicly only when the proxy and firewall are configured.

## Storage mounts

SubGen supports any number of media libraries. Every mount must have a unique destination below
`/media`, and SubGen must have write permission because sidecars are always written next to media.
If all libraries share a host parent, mount that parent with `SUBGEN_MEDIA_PATH` and add paths such
as `/media/movies`, `/media/tv`, and `/media/anime` in the web interface.

For unrelated host paths, add more entries directly to `docker-compose.yaml`:

```yaml
services:
  subgen:
    volumes:
      - /srv/movies:/media/movies
      - /mnt/nas/tv:/media/tv
      - /mnt/archive/anime:/media/anime
```

Then create three libraries in SubGen using the corresponding container paths. There is no
application-level limit on the number of libraries.

Compose-managed NFS and SMB mounts can be mixed with local bind mounts:

```yaml
volumes:
  media_nfs:
    driver_opts:
      type: nfs
      o: addr=192.0.2.10,rw,nfsvers=4
      device: :/export/media
  media_smb:
    driver_opts:
      type: cifs
      o: username=${SMB_USER},password=${SMB_PASSWORD},uid=${PUID},gid=${PGID},vers=3.0
      device: //192.0.2.20/media
```

Do not commit SMB credentials. Prefer a host credentials file or Docker secrets in production.
Network filesystems are covered by scheduled reconciliation scans as well as manual scans. Newly
modified files are ignored until the configured stability interval has elapsed.

## Filename policy

For `Movie Name.mkv`, Arabic SRT output is `Movie Name.ar.srt`. Embedded subtitles do not satisfy
the requirement: SubGen always creates an external sidecar for every configured language/format.

## Publishing

The release workflow publishes the single image as `ghcr.io/fahed-alhammadi/subgen:x.y.z` for Git
tags such as `v0.1.0`. It also publishes major/minor and `latest` aliases. CUDA currently limits the
unified release image to `linux/amd64`; ARM64 can be added when an equivalent unified runtime is
validated.

## Security notes

- Generate `SUBGEN_SECRET_KEY` with `openssl rand -hex 32`.
- The application does not mount the Docker socket and drops all Linux capabilities.
- Subtitle deletion accepts only supported extensions inside a configured library and is audited.
- Back up `/config/subgen.db` and generated subtitle files.
- Treat all mounted media as sensitive; filenames appear in application logs and the dashboard.

## Roadmap before 1.0

- Settings editing, subtitle browser/preview, explicit delete confirmation, and audit-log UI
- Job cancellation, priority, progress based on media duration, and controlled regeneration
- CSRF tokens, login throttling, trusted-proxy enforcement, session revocation, and TOTP
- Alembic migrations, backup/restore, model installer, notifications, and broader language mapping
- Integration tests using real short media fixtures and NVIDIA CI on a self-hosted runner

This repository is an MVP foundation and should not be described as production-complete until the
1.0 security and reliability milestones above are finished.

