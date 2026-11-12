# SubGen HTTP API (v0.4)

All endpoints are same-origin JSON unless noted. The web UI is the only intended client.

## Conventions

- **Auth:** `POST /api/login` sets two cookies: `subgen_session` (HttpOnly) and `subgen_csrf` (readable).
  Every `POST`/`PUT`/`PATCH`/`DELETE` (except login) must send header `x-csrf-token: <subgen_csrf cookie>`.
- **401** → session missing/expired; the UI redirects to `/login`. Unauthenticated `/api/*` requests are
  rejected before their body is read.
- **413** → request body too large: 1 MB for JSON endpoints, 64 MB for subtitle saves, `SUBGEN_MAX_UPLOAD_MB`
  for uploads, 2 GB for restores.
- **Errors** always return `{"detail": "<human readable string>"}` (validation errors are flattened to a string).
- **Timestamps** are ISO-8601 UTC strings (`2026-11-12T10:20:30.123456+00:00`) or `null`.
- **Durations** are integer milliseconds.
- **Paging:** list endpoints accept `limit` (1–500, default 50) and `offset` (default 0) and return
  `{"items": [...], "total": <int>}`.

## Shared objects

### Job
```json
{
  "id": 12, "media_id": 4, "media_name": "Movie (2024).mkv", "media_path": "/media/movies/Movie (2024).mkv",
  "library_id": 1, "library_name": "Movies", "source": "library",
  "language": "ar", "language_name": "Arabic", "format": "srt",
  "status": "processing", "stage": "transcribing", "progress": 42, "priority": 0,
  "duration_ms": 5400000, "processed_ms": 2100000, "detected_language": "en",
  "speed": 12.4, "eta_ms": 266000,
  "error": null, "output_path": null, "options": {},
  "created_at": "…", "started_at": "…", "finished_at": null
}
```
- `source`: `"library"` or `"upload"` (Studio workspace).
- `status`: `queued | processing | completed | failed | cancelled`.
- `stage` (only meaningful while processing): `queued | loading | probing | extracting | transcribing |
  cached | translating | writing | done`. `cached` means a stored transcript was reused.
- `speed`: realtime factor while transcribing (audio ms processed per wall-clock ms), or `null`.
- `eta_ms`: rough remaining time, or `null`.
- `options`: per-job overrides (see `POST /api/media/{id}/generate`).

### Language
`{"code": "ar", "name": "Arabic", "translation": true, "whisper": true}` — `translation` means the local
Hugging Face (NLLB) provider supports it; `whisper` means Whisper can transcribe it.

## Session

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/login` | – | login HTML |
| POST | `/api/login` | form: `username`, `password`, `totp_code` | `{ok}` |
| POST | `/api/logout` | – | `{ok}` (revokes all sessions) |
| GET | `/api/session` | – | `{user, version, totp_enabled}` |
| GET | `/health` | – | `{status: "ok"}` (no auth) |

## Overview

`GET /api/dashboard` →
```json
{
  "libraries": 2, "media": 340,
  "jobs": {"queued": 10, "processing": 1, "completed": 200, "failed": 3, "cancelled": 0},
  "worker": {"state": "processing", "device": "cuda", "compute_type": "float16",
             "model": "Systran/faster-whisper-small", "job_id": 12, "message": "", "updated_at": "…"},
  "active": [Job], "recent": [Job],
  "last_24h": {"completed": 14, "failed": 1, "audio_ms": 72000000}
}
```
- `worker.state`: `idle | loading | processing | error`. `message` holds the last error when `error`.
- `active`: processing jobs first, then up to 10 queued jobs in execution order.
- `recent`: last 10 finished jobs (completed/failed/cancelled).

## Jobs

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/jobs?status=&library_id=&q=&limit=&offset=&sort=` | – | `{items: [Job], total}` |
| GET | `/api/jobs/{id}` | – | `Job` |
| POST | `/api/jobs/{id}/cancel` | – | `{ok}` (queued → cancelled; processing → cancel requested) |
| POST | `/api/jobs/{id}/retry` | – | `{ok}` (only failed/cancelled, else 409) |
| POST | `/api/jobs/{id}/regenerate` | – | `{id}` (new queued job, overwrites output when done) |
| PUT | `/api/jobs/{id}/priority` | `{priority: -100..100}` | `{ok}` |
| DELETE | `/api/jobs/{id}` | – | `{ok}` (history only; 409 while processing; the subtitle file is kept) |
| DELETE | `/api/jobs/{id}/subtitle` | – | `{ok}` (deletes the generated sidecar file) |
| POST | `/api/jobs/bulk` | `{action, ids?: [int], status?: str}` | `{affected}` |

- `status` filter accepts a single status or `active` (= queued + processing).
- `q` matches the media path (case-insensitive substring; `%` and `_` are literal).
- `sort`: `newest` (default), `oldest`, `queue` (priority desc then created asc).
- Bulk `action`: `cancel | retry | delete | prioritize`. Provide `ids`, or `status` to target every job in
  that status (e.g. `{action:"delete", status:"completed"}` clears history). `prioritize` also takes
  `priority`.

## Libraries

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/libraries` | – | `[Library]` |
| POST | `/api/libraries` | `{name, path, languages: [str], formats: [str], enabled?}` | `{id}` |
| PUT | `/api/libraries/{id}` | same as POST | `{ok}` |
| DELETE | `/api/libraries/{id}` | – | `{ok}` (media files untouched) |
| POST | `/api/libraries/{id}/scan` | – | `{queued, found, skipped, pruned}` |
| POST | `/api/libraries/scan` | – | `{queued, found, skipped, pruned, errors: [str]}` (all enabled libraries) |
| GET | `/api/browse?path=` | – | `{path, parent, root, dirs: [{name, path}]}` |

`Library` =
```json
{"id": 1, "name": "Movies", "path": "/media/movies", "languages": ["ar", "en"], "formats": ["srt"],
 "enabled": true, "media_count": 120, "last_scan_at": "…",
 "jobs": {"queued": 1, "processing": 0, "completed": 80, "failed": 2, "cancelled": 0}}
```
- `formats` ⊆ `srt, ass, vtt`.
- `/api/browse` lists sub-directories under the media root (`SUBGEN_MEDIA_ROOT`, default `/media`) for a
  folder picker. `path` defaults to the root; `parent` is `null` at the root.

## Media

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/media?library_id=&q=&status=&limit=&offset=` | – | `{items: [MediaItem], total}` |
| GET | `/api/media/{id}` | – | `MediaItem` + `probe` |
| POST | `/api/media/{id}/generate` | see below | `{jobs: [int]}` |
| GET | `/api/media/{id}/stream` | – | raw media bytes with HTTP Range support (for `<video>`) |

`MediaItem` =
```json
{"id": 4, "library_id": 1, "library_name": "Movies", "name": "Movie.mkv", "path": "/media/movies/Movie.mkv",
 "size": 123456789, "duration_ms": 5400000, "source": "library", "created_at": "…",
 "subtitles": [{"language": "ar", "format": "srt", "exists": true, "path": "…",
                "job_id": 12, "job_status": "completed"}],
 "external": ["Movie.en.srt", "Movie.eng.forced.srt"],
 "playable": true}
```
- `subtitles` covers every target language × format of the library (for uploads: every job created).
  `job_id`/`job_status` refer to the newest job for that pair, or `null`.
- `external` lists other subtitle files that sit beside the media.
- `status` filter: `missing` (at least one target subtitle missing), `complete`, `failed`.
- `playable`: the browser can probably play the container (`.mp4`, `.m4v`, `.webm`, `.mov`, audio files).

`probe` (from ffprobe; `null` when ffprobe is unavailable) =
```json
{"duration_ms": 5400000,
 "audio": [{"index": 0, "language": "en", "codec": "aac", "channels": 6, "title": "Surround", "default": true}],
 "subtitles": [{"index": 0, "language": "ar", "codec": "subrip", "title": null, "forced": false}]}
```
`audio[].index` is the audio-relative index (0 = first audio track) used for `options.audio_track`.

`POST /api/media/{id}/generate` body:
```json
{"languages": ["ar", "en"], "formats": ["srt"], "priority": 0,
 "options": {"source_language": "auto", "audio_track": null, "initial_prompt": ""}}
```
All `options` keys are optional; omitted keys use global settings. Creates one job per language × format
and returns their ids. Existing sidecars are overwritten when the job completes.

## Studio uploads

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/api/uploads?filename=<name>` | raw file bytes (`application/octet-stream`) | `MediaItem` |
| GET | `/api/uploads` | – | `[MediaItem & {jobs: [Job], expires_at}]` newest first |
| DELETE | `/api/uploads/{media_id}` | – | `{ok}` (deletes upload, its subtitles and jobs) |

- Allowed extensions: video (`mkv mp4 m4v avi mov ts webm wmv flv mpg mpeg`) and audio
  (`mp3 m4a aac wav flac ogg opus mka wma`).
- Max size comes from `SUBGEN_MAX_UPLOAD_MB` (default 20480); 413 when exceeded.
- After upload, call `POST /api/media/{id}/generate`. Uploads expire after the
  `upload_retention_hours` setting.
- Use `XMLHttpRequest` (`upload.onprogress`) to show upload progress.

## Subtitles

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/subtitles/{job_id}` | – | `{job_id, media_id, name, path, format, language, cues: [Cue], playable}` |
| PUT | `/api/subtitles/{job_id}` | `[Cue]` | `{ok}` |
| GET | `/api/subtitles/{job_id}/download` | – | file attachment |

`Cue` = `{"start_ms": 1000, "end_ms": 2500, "text": "Line one\nLine two"}` — `\n` separates lines.
Validation: at least one cue, `0 ≤ start_ms < end_ms`, text ≤ 4000 chars, ≤ 20000 cues. Cues are saved sorted
by start.

## Settings

`GET /api/settings` →
```json
{"values": {"beam_size": 5, "translation_provider": "huggingface", "libretranslate_api_key": ""},
 "schema": [SettingField]}
```
`SettingField` =
```json
{"key": "beam_size", "group": "transcription", "label": "Beam size", "type": "int",
 "default": 5, "min": 1, "max": 10, "step": 1, "options": null, "help": "…", "is_set": true}
```
- `type`: `string | text | int | float | bool | select | secret | model | language`.
  - `select`: choose from `options: [{value, label}]`.
  - `model`: a model repo id; `model_kind` (`whisper` or `translation`) says which installed models to offer
    (free text is still allowed).
  - `language`: a language code from `/api/languages` (may include `auto` when listed in `options`).
  - `secret`: value is always returned as `""`; `is_set` tells whether a value is stored.
- `group`: `transcription | segmentation | translation | scanning | output | workspace` (UI tabs, in this
  order).
- `GET` returns values typed (bool/int/float), not strings.

`PUT /api/settings` body `{"values": {<partial key/value map>}}` → `{ok, values}`.
Only send changed keys. For a `secret`, omit it (or send `null`) to keep it, send `""` to clear it.
422 `detail` names the field, e.g. `"Beam size: must be between 1 and 10"`.

`POST /api/settings/reset` body `{"keys": ["beam_size"]}` → `{ok, values}` (restores defaults).

`POST /api/providers/{kind}/test` (`kind` = `huggingface | ollama | libretranslate`) body
`{"values": {<unsaved overrides>}}` → `{ok, message, models?: [str]}`. Stored settings fill any omitted
keys (so secrets need not be resent).

`GET /api/languages` → `[Language]` sorted by name.

## Models

`GET /api/models` →
```json
{"installed": [{"kind": "whisper", "repo_id": "Systran/faster-whisper-small", "size": 486000000, "in_use": true}],
 "downloads": [{"id": "ab12", "kind": "whisper", "repo_id": "…", "status": "downloading",
                "error": null, "total_bytes": 486000000, "downloaded_bytes": 120000000, "progress": 24}],
 "recommended": [{"kind": "whisper", "repo_id": "Systran/faster-whisper-small", "name": "Whisper Small",
                  "size": 486000000, "description": "…", "tags": ["balanced"], "installed": true}]}
```
- Download `status`: `queued | downloading | completed | failed`. `progress` is 0–100 or `null` when the size
  is unknown.

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/models/search?q=&kind=` | – | `[{repo_id, downloads, likes, pipeline_tag}]` (needs internet) |
| POST | `/api/models/download` | `{kind, repo_id}` | download object |
| DELETE | `/api/models/downloads/{id}` | – | `{ok}` (dismiss a finished/failed entry) |
| DELETE | `/api/models/{kind}/{owner}/{name}` | – | `{ok}` (409 if selected in settings) |
| POST | `/api/models/select` | `{kind, repo_id}` | `{ok}` (sets it as the active model) |

## System

`GET /api/system` →
```json
{"version": "0.4.0", "platform": "Linux-…", "python": "3.12.3", "ffmpeg": "7.0.2",
 "device_mode": "auto", "compute_type": "auto",
 "gpus": [{"name": "NVIDIA RTX 4090", "memory_total": 25757220864, "memory_used": 1200000000}],
 "disks": [{"label": "Models", "path": "/models", "total": 0, "free": 0},
           {"label": "Config", "path": "/config", "total": 0, "free": 0}],
 "transcript_cache": {"count": 12, "bytes": 3400000},
 "workspace": {"count": 2, "bytes": 800000000},
 "worker": {…same as dashboard…}}
```

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/api/system/cache/clear` | – | `{deleted}` (transcript cache) |
| GET | `/api/audit?q=&limit=&offset=` | – | `{items: [{id, action, target, detail, created_at}], total}` |
| POST | `/api/backup` | – | SQLite file download (contains provider secrets; the server copy is deleted after sending) |
| POST | `/api/restore` | multipart `backup_file` | 202 `{ok, message}`; the app restarts (409 if a restore is running) |
