<div align="center">

<h1>SubGen</h1>

<p><strong>Private, local subtitle generation for self-hosted media libraries</strong></p>

<p>
  <a href="https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/ci.yaml"><img src="https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/ci.yaml/badge.svg" alt="CI status"></a>
  <a href="https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/container.yaml"><img src="https://github.com/Fahed-Alhammadi/SubGen/actions/workflows/container.yaml/badge.svg" alt="Container build status"></a>
  <a href="https://github.com/Fahed-Alhammadi/SubGen/pkgs/container/subgen"><img src="https://img.shields.io/badge/GHCR-subgen-blue?logo=docker" alt="GitHub Container Registry"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT License"></a>
</p>

<p><a href="https://fahed-alhammadi.github.io/SubGen/">Website</a> · <a href="#quick-start">Quick start</a> · <a href="docs/API.md">API reference</a></p>

</div>

SubGen watches your media libraries, transcribes speech with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), translates it with local models, and
writes readable subtitle sidecars (`Movie (2024).ar.srt`) beside each file. Everything runs on your own
server. One container image works on CPU and NVIDIA GPUs.

![SubGen overview dashboard](screenshots/web/dashboard.png)

> **Status:** active pre-1.0 development. Pin a versioned image, keep backups, and check results on a
> few representative files before processing an irreplaceable library unattended.

## Features

**Subtitle quality**
- Word-timestamp re-segmentation: every cue respects max characters per line, max lines, and minimum
  and maximum on-screen time. Breaks prefer sentence ends, clauses and pauses.
- Balanced two-line wrapping with CJK/Thai awareness, minimum gaps, no overlaps, and a short linger
  after speech.
- Hallucination filtering removes "Thanks for watching", subtitle credits and repetition loops
  invented by Whisper during silence or music.
- Translation runs on sentence-sized units and is redistributed across the original cue timings, so
  translated subtitles stay in sync.

**Fast and local**
- Transcribe once, translate many: transcripts are cached, so extra languages and formats skip
  Whisper entirely. Formats of the same language are written together.
- Batched GPU inference (3–6× faster on NVIDIA). CPU int8 inference on any x86-64 server.
- Local translation with NLLB-200, M2M100 or Marian/OPUS-MT models, or self-hosted Ollama /
  LibreTranslate. Nothing is sent to a cloud service.

**Libraries**
- Unlimited libraries, each with its own target languages and SRT / ASS / WebVTT formats.
- Skips languages that already have subtitles: external sidecars in any common naming
  (`Movie.eng.srt`, `Movie.English.sdh.srt`) and, optionally, embedded tracks.
- Picks the right audio track in multi-audio files by language preference and passes its language
  tag to Whisper.
- Ignore patterns (`sample, trailer`), protection for files still being copied, safe pruning of
  deleted media that never wipes an unmounted share.

**Tools**
- **Overview:** the job being subtitled right now shown as a media timeline (playhead, timecodes and
  pipeline steps), what runs next, recent results and the last 24 hours at a glance.
- **Studio:** drag and drop any video or audio file and download subtitles. No library needed.
- **Subtitle editor:** video preview with live captions, timecode editing, split/merge, timing shift,
  find & replace, and readability warnings (reading speed, line length, overlaps).
- **Models:** one table per model type with the model in use first, default models installed on first
  start, one-click installs, Hugging Face search, download progress, and an offer to delete the old
  model when you switch.
- **Queue:** status filters, search, bulk cancel/retry/priority/clear, one clear action per job with
  everything else in its details, and per-file generation with audio-track and language overrides.
- Settings editor generated from the backend's settings schema (pipeline, device, limits and sign-in
  protection, applied without a restart), provider connection tests, an audit log, and database backup
  and restore.
- Interface set in Atkinson Hyperlegible, a typeface designed for legibility, bundled so it works
  offline; light and dark themes, keyboard navigable, and usable on a phone.

**Security**
- Single administrator with Argon2 password hashing, optional two-factor sign-in (TOTP, turned on from
  **Settings → Security**), login throttling, and session revocation.
- CSRF tokens on every mutation, a strict Content-Security-Policy (no inline scripts or styles), and
  path confinement for every file operation.
- Runs as your `PUID`/`PGID`, never root, with no Linux capabilities and `no-new-privileges`.

**Simple setup**
- A short compose file and an `.env` where only the admin password is required. SubGen prepares its
  folders, generates its own session key, and picks HTTPS-only cookies automatically behind HTTPS.

## Screenshots

| Studio | Subtitle editor |
| --- | --- |
| ![Studio upload and generation](screenshots/web/studio.png) | ![Subtitle editor with video preview](screenshots/web/editor.png) |
| **Library browser** | **Models** |
| ![Library media browser](screenshots/web/library.png) | ![Models page grouped by model type](screenshots/web/models.png) |

## How it works

```text
media file ─► ffprobe ─► choose audio track ─► ffmpeg (16 kHz mono)
           ─► faster-whisper (word timestamps, VAD) ──► transcript cache
           ─► clean hallucinations ─► re-segment into readable cues
           ─► translate sentence units (if target ≠ spoken language)
           ─► balance lines, fix timing ─► atomic write: Movie.ar.srt / .ass / .vtt
```

```text
Browser ──HTTPS──► reverse proxy ──► SubGen container
                                      ├─ Web UI + JSON API (FastAPI)
                                      ├─ Scheduler (library scans, upload expiry)
                                      ├─ Worker (one job at a time; models unloaded when idle)
                                      └─ SQLite (jobs, settings, transcripts, audit)
                                             │
                          /media (libraries)  /models  /config
```

## Requirements

- Linux with Docker Engine and Docker Compose v2 (`linux/amd64` image)
- Write permission for every media library for the user set in `PUID`/`PGID` (subtitles are stored
  beside the media)
- Disk space for models: about 0.5 GB (Whisper Small) to 3 GB (Large v3), plus 2.5 GB for NLLB-200
- For GPU processing: an NVIDIA GPU, a current driver, and the NVIDIA Container Toolkit

## Quick start

SubGen needs two files: `docker-compose.yaml` and `.env`.

```bash
mkdir subgen && cd subgen
curl -fsSLO https://raw.githubusercontent.com/Fahed-Alhammadi/SubGen/main/docker-compose.yaml
curl -fsSL https://raw.githubusercontent.com/Fahed-Alhammadi/SubGen/main/.env.example -o .env
chmod 600 .env
```

`docker-compose.yaml`:

```yaml
services:
  subgen:
    image: ghcr.io/fahed-alhammadi/subgen:latest
    container_name: subgen
    restart: unless-stopped
    gpus: all                       # remove this line if the host has no NVIDIA GPU
    ports:
      - "8080:8080"                 # behind a reverse proxy on this host: "127.0.0.1:8080:8080"
    env_file: .env
    volumes:
      - ./config:/config            # database, settings and Studio uploads
      - ./models:/models            # Whisper and translation models
      # Your media folders. Each one becomes a library you add in the web interface.
      - /path/to/movies:/media/movies
      - /path/to/tv:/media/tv

    # Recommended hardening. Safe to keep as is.
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    cap_add: [CHOWN, SETUID, SETGID]  # used only at startup to switch to PUID/PGID
```

`.env`:

```env
# User that owns your media folders. Find it with: id -u  and  id -g
PUID=1000
PGID=1000

# Admin sign-in. Password: at least 12 characters.
SUBGEN_ADMIN_USERNAME=admin
SUBGEN_ADMIN_PASSWORD=change-me-to-a-long-password
```

1. In `.env`, set a password of at least 12 characters. Set `PUID`/`PGID` to the user that owns your
   media (run `id -u` and `id -g` as that user).
2. In `docker-compose.yaml`, replace `/path/to/movies` and `/path/to/tv` with your media folders (add or
   remove lines as needed). Remove `gpus: all` on hosts without an NVIDIA GPU.
3. Start SubGen and open `http://<server>:8080`:

```bash
docker compose up -d
```

Nothing else needs preparing. On start, SubGen creates `config/` and `models/`, gives them to
`PUID`/`PGID`, generates its session-signing key and runs as that user, never as root. Sign-in works
over plain HTTP on your LAN and switches to HTTPS-only cookies automatically behind an HTTPS proxy.
Everything else is configured in the web interface under **Settings**.

### First run

1. **Models:** Whisper Small and NLLB-200 600M install automatically on first start (about 3 GB; jobs
   wait until they finish). On a GPU, switch to Large v3 Turbo; SubGen then offers to delete the old
   model or keep it.
2. **Studio:** drop a short clip to check quality before touching your library.
3. **Libraries:** add each mounted folder (the folder picker browses `/media`), choose target
   languages and formats, and select **Scan**.
4. **Queue / Overview:** follow progress, speed and ETA. Use **Edit** on any finished subtitle.

## Models

The **Models** page lists recommended models with one-click install:

| Model | Kind | Size | Notes |
| --- | --- | --- | --- |
| `Systran/faster-whisper-small` | Whisper | 0.5 GB | Balanced CPU default |
| `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | Whisper | 1.6 GB | Recommended on NVIDIA GPUs |
| `Systran/faster-whisper-large-v3` | Whisper | 3.1 GB | Best multilingual accuracy |
| `facebook/nllb-200-distilled-600M` | Translation | 2.5 GB | 200 languages, default |
| `facebook/m2m100_418M` | Translation | 1.9 GB | Lighter, 100 languages |
| `Helsinki-NLP/opus-mt-*` | Translation | 0.3 GB | Fast single-pair models |

Any CTranslate2 Whisper repository or Transformers seq2seq translation repository on Hugging Face can
be installed from the search box. Downloads skip unused framework weights (TensorFlow, Flax, ONNX) and
are stored in the `models` folder:

```text
models/
├── whisper/Systran/faster-whisper-small/
└── translation/facebook/nllb-200-distilled-600M/
```

The models in use are installed automatically when they are missing (on first start, Whisper Small and
NLLB-200 600M); the queue waits while they download. Any model that is not in use, including the
defaults, can be deleted. Set `SUBGEN_AUTO_INSTALL_MODELS=false` on servers without internet access and
copy models into the folders above instead.

Jobs run one at a time, in priority order, so a long queue never runs several models at once. Loaded
models stay in memory between jobs and are released after 15 idle minutes (**Settings → Transcription →
Unload models when idle**; 0 keeps them loaded), which frees GPU memory for other containers. The next job
reloads them in a few seconds.

## Settings

Settings are edited in the web UI, stored in the database and apply without a restart. Defaults:

| Group | Setting | Default |
| --- | --- | --- |
| Transcription | Processing device / compute type | Automatic (GPU when visible; float16 on GPU, int8 on CPU) |
| | Spoken language | Detect automatically |
| | Preferred audio tracks | Default track |
| | Beam size / batch size | 5 / auto (8 on GPU) |
| | Skip silence (VAD), filter hallucinations | On |
| | Vocabulary prompt | Empty |
| Segmentation | Characters per line / lines | 42 / 2 (≈19 for CJK) |
| | Subtitle duration | 1.0 – 6.0 s, 80 ms gap, 250 ms linger |
| Translation | Provider | Local Hugging Face model |
| Scanning | Interval | 30 min |
| | Wait for copies to finish | 60 s unchanged |
| | Skip existing / embedded subtitles | On / Off |
| | Ignore files containing | `sample, trailer, featurette` |
| Output | ASS font, UTF-8 BOM for SRT | Arial 56 / Off |
| Workspace | Keep Studio uploads / largest upload | 72 h / 20480 MB |
| System | Keep free disk space / log detail | 1024 MB / Info |
| Security | Two-factor sign-in | Off |
| | Stay signed in / failed sign-ins allowed / block period | 24 h / 5 / 300 s |

Individual jobs can override the spoken language, audio track and vocabulary prompt.

Before 0.8, the device, upload, disk-space, sign-in, stable-file and log settings were environment
variables. If your `.env` still sets them, those values are the starting point shown in Settings until
you change them there.

## Translation providers

- **Local Hugging Face:** NLLB-200, M2M100, and Marian/OPUS-MT models from `/models`. Uses the GPU
  when available.
- **Ollama:** any local LLM through `/api/chat` with validated JSON output and automatic batch
  splitting.
- **LibreTranslate:** a private LibreTranslate server, with batched requests.

When Ollama or LibreTranslate runs in another container, attach both to the same Docker network and
use the service name in the URL. When it runs directly on the host, use an address reachable from the
container, not `127.0.0.1`.

## CPU and NVIDIA GPU

The provided `docker-compose.yaml` requests every NVIDIA GPU (`gpus: all`). With **Settings →
Transcription → Processing device** on Automatic, SubGen uses CUDA when a GPU is visible and the CPU
otherwise. On hosts without an NVIDIA GPU, remove `gpus: all`: Docker refuses to start a container that
asks for a GPU it can't provide.

## Multiple media libraries

Mount each library below `/media`:

```yaml
services:
  subgen:
    volumes:
      - /srv/movies:/media/movies
      - /mnt/nas/television:/media/tv
      - /mnt/archive/anime:/media/anime
```

All mounts must be writable by `PUID`/`PGID`; SubGen never changes the owner of anything under
`/media`. When the media belongs to a shared group, add it with `group_add: ["<gid>"]`. NFS and SMB
shares can be mounted by the host or declared as Compose volumes. Keep share credentials out of Git.

## Subtitle naming

```text
Movie Name (2026).mkv
Movie Name (2026).ar.srt
Movie Name (2026).en.vtt
Movie Name (2026).fr.ass
```

## Configuration

Only the admin password is required. See [.env.example](.env.example) for the template.

| Variable | Purpose |
| --- | --- |
| `PUID`, `PGID` | User and group SubGen runs as (default `1000`). They own `config/`, `models/` and new subtitles |
| `SUBGEN_ADMIN_USERNAME`, `SUBGEN_ADMIN_PASSWORD` | Administrator sign-in (password ≥ 12 characters; username defaults to `admin`) |
| `SUBGEN_TRUSTED_PROXIES` | Reverse proxies allowed to supply the client address: addresses or ranges (default `127.0.0.1`) |
| `SUBGEN_SECRET_KEY` | Session-signing key (≥ 32 characters). Generated into `config/secret_key` when not set |
| `SUBGEN_SECURE_COOKIES` | `auto` (default): HTTPS-only cookies whenever the browser connects over HTTPS. `true`/`false` force it |
| `SUBGEN_TOTP_SECRET` | Enforce two-factor sign-in with this Base32 secret instead of enabling it in Settings |
| `SUBGEN_AUTO_INSTALL_MODELS` | Install the selected models when missing (default `true`; set `false` offline) |
| `SUBGEN_WORKSPACE_DIR` | Studio upload storage (default `/config/workspace`) |

### Reverse proxy

- **Proxy on the same host:** publish the port on localhost only: `"127.0.0.1:8080:8080"`.
- **Proxy in another container** (Nginx Proxy Manager, Traefik, Caddy): remove `ports:`, attach SubGen
  to the proxy's Docker network, and set `SUBGEN_TRUSTED_PROXIES` to that network's range (shown by
  `docker network inspect <network>`), for example `172.18.0.0/16`. Without it, sign-in throttling sees
  every visitor as the proxy.
- Cookies switch to HTTPS-only from the `X-Forwarded-Proto` header, which these proxies send by default
  (nginx: `proxy_set_header X-Forwarded-Proto $scheme;`).
- Allow large request bodies for Studio uploads (for example `client_max_body_size 20g;` in nginx).

## Security

- Set a long, unique admin password before the first launch and use HTTPS for remote access.
- Mutating API requests require a session-bound CSRF token. Logout revokes all sessions.
- Every file operation is confined to its library, the media root, or the Studio workspace.
- The container starts as root only to give `/config` and `/models` to `PUID`/`PGID`, then runs SubGen
  as that user with no Linux capabilities at all. The provided compose file also drops every capability
  except the three that startup step needs, enables `no-new-privileges`, and never mounts the Docker
  socket. Starting the container with `user: "1000:1000"` instead skips the step entirely.
- Keep `.env`, media, models and databases out of Git.

## Backup and restore

**System → Backup** downloads a consistent SQLite snapshot (settings, libraries, jobs, transcripts,
audit log). A restore is validated, staged in `/config`, and applied on restart. Backups do not
include media, subtitles or models.

## Updating

```bash
docker compose pull
docker compose up -d
```

Database migrations run automatically on start. Pin a version (`ghcr.io/fahed-alhammadi/subgen:0.8`)
for reproducible deployments.

### Upgrading from 0.7 or earlier

Your existing `docker-compose.yaml` and `.env` keep working: a `user:` line is still honoured, and the
variables that moved to Settings become their starting values. To switch to the shorter files:

1. Download the new `docker-compose.yaml` and put your folders in it: the old `SUBGEN_CONFIG_PATH`,
   `SUBGEN_MODEL_PATH` and `SUBGEN_MEDIA_PATH` values become the left side of the `/config`, `/models`
   and `/media/...` lines.
2. Keep `PUID`, `PGID`, `SUBGEN_ADMIN_USERNAME` and `SUBGEN_ADMIN_PASSWORD` in `.env`; the rest can
   go, including `SUBGEN_SECRET_KEY` (SubGen then generates one in `config/secret_key`). A restart signs
   everyone out either way, so you only sign in again.
3. If Compose reports that the name `subgen` is already in use, remove the old container first with
   `docker rm -f subgen` (your data lives in the mounted folders).

If SubGen ran as root before (no `user:` line), the first start gives `config/` and `models/` back to
`PUID`/`PGID`. Subtitles written as root stay owned by root; to fix them, run
`sudo find /path/to/media -user 0 \( -name '*.srt' -o -name '*.vtt' -o -name '*.ass' \) -exec chown 1000:1000 {} +`.

## Project structure

```text
subgen/
├── main.py            App assembly, security headers, background loops
├── config.py          Environment variables, generated session key
├── api/               HTTP routers (auth, jobs, libraries, media, models, settings, subtitles, system)
├── pipeline.py        Probe → audio → Whisper (cached) → segment → translate
├── segmentation.py    Subtitle-quality engine (re-segmentation, wrapping, hallucination filter)
├── worker.py          Job queue worker
├── media.py           Library scanning, Studio workspace
├── providers.py       Translation providers (Hugging Face, Ollama, LibreTranslate)
├── models.py          Model vault
├── settings_store.py  Settings schema and validation
├── languages.py       Language codes, names, NLLB/Whisper mappings
├── audio.py           ffprobe / ffmpeg
├── subtitles.py       Sidecar reading, writing and detection
├── db.py              SQLAlchemy models and migrations
└── static/            Web UI (no build step, native ES modules)
docker/entrypoint.sh   Container start: hand /config and /models to PUID/PGID, drop root
docs/API.md            HTTP API reference
Website/               Project website (GitHub Pages)
```

## Development

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
export SUBGEN_ADMIN_PASSWORD=dev-password-123 SUBGEN_CONFIG_DIR=./data/config \
       SUBGEN_MODEL_DIR=./data/models SUBGEN_MEDIA_ROOT=./media
.venv/bin/uvicorn subgen.main:app --reload --port 8080
.venv/bin/ruff check .
```

`ffmpeg` and `ffprobe` must be on `PATH`. The frontend has no build step: edit `subgen/static/` and
reload.

## Contributing

Issues and pull requests are welcome. Keep changes focused, run `ruff check .`, describe how you
tested, and never include credentials, media or model files.

## License

SubGen is released under the [MIT License](LICENSE). You may use, copy, modify, merge, publish,
distribute, sublicense and sell copies of the software, provided the copyright and permission notice is
included in all copies or substantial portions of it. The software is provided "as is", without warranty
of any kind.

The web interface bundles the Atkinson Hyperlegible Next and Atkinson Hyperlegible Mono typefaces,
which are licensed under the SIL Open Font License 1.1 (see `subgen/static/fonts/`).

---

© 2026 Fahed Alhammadi. All rights reserved.
