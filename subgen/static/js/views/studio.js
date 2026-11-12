// Studio: upload a single file, generate subtitles for it, and follow the results.
import { api, uploadFile } from '../api.js';
import { html, render, patchList, delegate, element, $ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { createPoller } from '../ui/poller.js';
import { notify } from '../ui/toast.js';
import { confirmDialog } from '../ui/dialog.js';
import { pageHeader, statusBadge, track, jobProgressText, jobTiming, emptyState, errorState, skeletonRows } from '../ui/components.js';
import { bytes, clock, relative, dateTime, isAudio, fileExt, AUDIO_EXTS, VIDEO_EXTS } from '../ui/format.js';
import { getLanguages } from '../store.js';
import { buildGenerateForm, submitGenerate, openGenerateDialog } from './generate.js';
import { jobActionButtons, jobHandlers } from './job-actions.js';

const ACCEPT = [...VIDEO_EXTS, ...AUDIO_EXTS].map((e) => `.${e}`).join(',');

export async function mount(ctx) {
  ctx.setTitle('Studio');
  const root = ctx.root;
  render(
    root,
    html`
      ${pageHeader({
        title: 'Studio',
        subtitle: 'Subtitle a single file without adding it to a library. Uploads are removed automatically after the retention period.',
      })}
      <div class="studio stack">
        <section class="panel" aria-labelledby="studio-up-h">
          <div class="panel-head"><h2 id="studio-up-h">New upload</h2></div>
          <div class="panel-body" data-slot="stage"></div>
        </section>
        <section class="panel" aria-labelledby="studio-list-h">
          <div class="panel-head">
            <h2 id="studio-list-h">Uploads</h2>
            <span class="spacer"></span>
            <span class="meta" data-slot="count"></span>
          </div>
          <ul class="upload-list" data-slot="uploads">${skeletonRows(3, 'block')}</ul>
        </section>
      </div>`,
  );

  const stage = $('[data-slot=stage]', root);
  const list = $('[data-slot=uploads]', root);
  const count = $('[data-slot=count]', root);
  let uploads = [];
  let loaded = false;
  let currentUpload = null; // {abort}
  let pendingForm = null; // generate form for the last upload

  /* ----- Upload stage ----- */

  function showDropzone() {
    pendingForm = null;
    render(
      stage,
      html`<label class="dropzone" data-dropzone>
        ${icon('upload')}
        <strong>Drop a video or audio file here</strong>
        <span>or <span class="link-like">choose a file</span> from this computer</span>
        <span class="faint small">MKV, MP4, MOV, WebM, AVI, MP3, M4A, WAV, FLAC and more</span>
        <input type="file" accept="${ACCEPT}" data-file aria-label="Choose a media file to upload">
      </label>`,
    );
    const zone = $('[data-dropzone]', stage);
    const input = $('[data-file]', stage);
    input.addEventListener('change', () => {
      if (input.files && input.files[0]) startUpload(input.files[0]);
    });
    zone.addEventListener('dragover', (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
      zone.classList.add('is-over');
    });
    zone.addEventListener('dragleave', (event) => {
      if (!zone.contains(event.relatedTarget)) zone.classList.remove('is-over');
    });
    zone.addEventListener('drop', (event) => {
      event.preventDefault();
      zone.classList.remove('is-over');
      const files = event.dataTransfer && event.dataTransfer.files;
      if (!files || !files.length) return;
      if (files.length > 1) notify.info('Studio uploads one file at a time. Uploading the first file.');
      startUpload(files[0]);
    });
  }

  function startUpload(file) {
    const ext = fileExt(file.name);
    if (!VIDEO_EXTS.has(ext) && !AUDIO_EXTS.has(ext)) {
      notify.error(`“${file.name}” is not a supported video or audio file.`);
      return;
    }
    render(
      stage,
      html`<div class="upload-progress stack-sm" aria-live="polite">
        <div class="row">
          ${icon(isAudio(file.name) ? 'music' : 'film')}
          <strong class="truncate upload-name"></strong>
          <span class="spacer"></span>
          <button type="button" class="btn btn-sm" data-action="abort-upload">${icon('x')}Cancel upload</button>
        </div>
        ${track(0, { label: 'Upload progress' })}
        <div class="row small muted num"><span data-progress-text>Starting upload…</span></div>
      </div>`,
    );
    $('.upload-name', stage).textContent = file.name;
    const bar = $('.track', stage);
    const text = $('[data-progress-text]', stage);
    const started = performance.now();
    let lastPaint = 0;
    const job = uploadFile(file, (loaded, total) => {
      const now = performance.now();
      if (now - lastPaint < 100 && loaded < total) return;
      lastPaint = now;
      const pct = total ? (loaded / total) * 100 : 0;
      bar.style.setProperty('--value', String(pct));
      bar.setAttribute('aria-valuenow', String(Math.round(pct)));
      const rate = loaded / Math.max(0.001, (now - started) / 1000);
      text.textContent = `${bytes(loaded)} of ${bytes(total)} (${Math.round(pct)}%), ${bytes(rate)}/s`;
    });
    currentUpload = job;
    job.promise
      .then(async (media) => {
        currentUpload = null;
        notify.ok(`Uploaded “${media.name}”.`);
        poller.trigger();
        await showGenerate(media);
      })
      .catch((err) => {
        currentUpload = null;
        if (err.name === 'AbortError') notify.info('Upload cancelled.');
        else notify.error(err);
        if (ctx.isCurrent()) showDropzone();
      });
  }

  async function showGenerate(media) {
    if (!ctx.isCurrent()) return;
    let languages = [];
    try {
      languages = await getLanguages();
    } catch (err) {
      notify.error(err);
    }
    const form = buildGenerateForm({ media, probe: undefined, languages, defaults: { languages: [], formats: ['srt'] } });
    pendingForm = form;
    const wrap = element(html`<form class="stack" novalidate>
        <div class="row">
          ${icon(isAudio(media.name) ? 'music' : 'film')}
          <strong class="truncate media-name"></strong>
          ${media.duration_ms ? html`<span class="muted num">${clock(media.duration_ms)}</span>` : ''}
          <span class="muted num">${bytes(media.size)}</span>
        </div>
        <div class="form-slot"></div>
        <div class="row">
          <button type="submit" class="btn btn-primary">${icon('sparkle')}Queue subtitles</button>
          <button type="button" class="btn btn-ghost" data-action="new-upload">Upload another file</button>
        </div>
      </form>`);
    $('.media-name', wrap).textContent = media.name;
    $('.form-slot', wrap).append(form.el);
    stage.textContent = '';
    stage.append(wrap);
    // Audio track info needs the probe from GET /api/media/{id}; fetch it in the background.
    api
      .get(`/api/media/${encodeURIComponent(media.id)}`)
      .then((detail) => {
        if (pendingForm !== form || !detail.probe || !detail.probe.audio || !detail.probe.audio.length) return;
        const select = form.el.querySelector('[name=audio_track]');
        select.disabled = false;
        detail.probe.audio.forEach((t, i) => {
          const opt = document.createElement('option');
          opt.value = String(t.index);
          opt.textContent = [`Track ${i + 1}`, t.language && t.language.toUpperCase(), t.codec && t.codec.toUpperCase(), t.channels && `${t.channels} ch`, t.title && `“${t.title}”`, t.default && '(default)']
            .filter(Boolean)
            .join(', ');
          select.append(opt);
        });
        select.options[0].textContent = 'Use the default track';
      })
      .catch(() => {});
    wrap.addEventListener('submit', async (event) => {
      event.preventDefault();
      const problem = form.validate();
      if (problem) {
        notify.error(problem);
        return;
      }
      const button = wrap.querySelector('[type=submit]');
      button.disabled = true;
      try {
        await submitGenerate(media.id, form.payload());
        poller.trigger();
        showDropzone();
      } catch (err) {
        notify.error(err);
        button.disabled = false;
      }
    });
    const first = form.el.querySelector('input[type=search]');
    if (first) first.focus();
  }

  /* ----- Uploads list ----- */

  async function load() {
    try {
      uploads = await api.get('/api/uploads');
      loaded = true;
      paintUploads();
    } catch (err) {
      if (err.status === 401) return;
      if (!loaded) render(list, html`<li>${errorState(err, { compact: true })}</li>`);
    }
  }

  function paintUploads() {
    count.textContent = uploads.length ? `${uploads.length} file${uploads.length === 1 ? '' : 's'}` : '';
    if (!uploads.length) {
      render(list, html`<li>${emptyState({ iconName: 'upload', compact: true, title: 'No uploads', text: 'Files you upload above appear here with their subtitle jobs.' })}</li>`);
      return;
    }
    patchList(list, uploads, (u) => u.id, uploadCard);
  }

  const findJob = (jobId) => {
    for (const u of uploads) {
      const j = (u.jobs || []).find((x) => x.id === jobId);
      if (j) return j;
    }
    return null;
  };

  const hasActive = () => uploads.some((u) => (u.jobs || []).some((j) => j.status === 'queued' || j.status === 'processing'));
  const poller = createPoller(load, () => (hasActive() ? 3000 : 10000));

  const off = delegate(root, 'click', {
    ...jobHandlers(findJob, () => poller.trigger()),
    'retry-load': () => poller.trigger(),
    'abort-upload': () => currentUpload && currentUpload.abort(),
    'new-upload': () => showDropzone(),
    'generate-upload': async (el) => {
      const res = await openGenerateDialog(Number(el.dataset.id), { formats: ['srt'] });
      if (res) poller.trigger();
    },
    'delete-upload': async (el) => {
      const upload = uploads.find((u) => u.id === Number(el.dataset.id));
      if (!upload) return;
      const ok = await confirmDialog({
        title: 'Delete upload?',
        message: `“${upload.name}”, its subtitles and its jobs are deleted.`,
        confirmLabel: 'Delete upload',
        danger: true,
      });
      if (!ok) return;
      try {
        await api.del(`/api/uploads/${encodeURIComponent(upload.id)}`);
        notify.ok('Upload deleted.');
        poller.trigger();
      } catch (err) {
        notify.error(err);
      }
    },
  });

  ctx.setGuard(() => (currentUpload ? 'An upload is still in progress. Leaving this page cancels it.' : ''));
  showDropzone();
  poller.start();

  return () => {
    poller.stop();
    off();
    if (currentUpload) currentUpload.abort();
  };
}

function uploadCard(upload) {
  const jobs = upload.jobs || [];
  return html`<li class="upload-card">
    <div class="upload-head">
      <span class="file-icon">${icon(isAudio(upload.name) ? 'music' : 'film')}</span>
      <div class="upload-title">
        <span class="cell-title break">${upload.name}</span>
        <span class="cell-sub num">${bytes(upload.size)}${upload.duration_ms ? html`, ${clock(upload.duration_ms)}` : ''}, uploaded ${relative(upload.created_at)}${upload.expires_at ? html`, <time datetime="${upload.expires_at}" title="${dateTime(upload.expires_at)}">expires ${relative(upload.expires_at)}</time>` : ''}
        </span>
      </div>
      <div class="btn-group">
        <button type="button" class="btn btn-sm" data-action="generate-upload" data-id="${upload.id}">${icon('sparkle')}Generate</button>
        <button type="button" class="btn btn-sm btn-ghost btn-icon btn-danger" data-action="delete-upload" data-id="${upload.id}" aria-label="Delete upload ${upload.name}" title="Delete upload">${icon('trash')}</button>
      </div>
    </div>
    ${jobs.length
      ? html`<ul class="upload-jobs">${jobs.map(
          (job) => html`<li class="upload-job" data-status="${job.status}">
            <span class="badge plain" data-tone="outline" title="${job.language_name || job.language}">${String(job.language).toUpperCase()}</span>
            <span class="badge plain" data-tone="outline">${String(job.format).toUpperCase()}</span>
            ${statusBadge(job.status)}
            <div class="upload-job-progress">
              ${job.status === 'processing'
                ? html`${track(job.progress, { label: `Progress ${job.language} ${job.format}`, thin: true })}<span class="small muted num">${jobProgressText(job)} ${Math.round(job.progress || 0)}%${jobTiming(job).length ? html`, ${jobTiming(job).join(', ')}` : ''}</span>`
                : job.status === 'failed'
                  ? html`<span class="small tone-danger truncate" title="${job.error || ''}">${job.error || 'Failed'}</span>`
                  : job.status === 'completed'
                    ? html`<span class="small muted">Finished ${relative(job.finished_at)}</span>`
                    : html`<span class="small muted">${jobProgressText(job)}</span>`}
            </div>
            <div class="job-actions">${jobActionButtons(job, { include: ['edit', 'download', 'cancel', 'retry'] })}</div>
          </li>`,
        )}</ul>`
      : html`<p class="small muted upload-nojobs">No subtitles requested yet. Choose Generate to queue some.</p>`}
  </li>`;
}
