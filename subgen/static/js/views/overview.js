// Overview: worker status, live jobs, totals and recent results.
import { api } from '../api.js';
import { html, render, patchList, delegate, busy, $ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { createPoller } from '../ui/poller.js';
import { notify } from '../ui/toast.js';
import {
  pageHeader, statusBadge, workerBadge, track, jobProgressText, jobTiming, langLabel, emptyState, errorState, skeletonRows,
} from '../ui/components.js';
import { number, relative, duration, plural, STATUS_LABELS } from '../ui/format.js';
import { publishDashboard, publishOffline } from '../store.js';
import { jobActionButtons, jobHandlers } from './job-actions.js';

export async function mount(ctx) {
  ctx.setTitle('Overview');
  const root = ctx.root;
  render(
    root,
    html`
      ${pageHeader({
        title: 'Overview',
        subtitle: 'What the subtitle worker is doing right now.',
        actions: html`
          <button type="button" class="btn" data-action="scan-all">${icon('scan')}Scan all libraries</button>
          <a class="btn btn-primary" href="/studio">${icon('upload')}New upload</a>`,
      })}
      <div class="overview">
        <section class="panel worker-card" aria-labelledby="ov-worker-h" data-slot="worker">${skeletonRows(3)}</section>
        <section class="panel last24" aria-labelledby="ov-24-h" data-slot="last24">${skeletonRows(3)}</section>
        <section class="ov-stats" aria-label="Totals" data-slot="stats"></section>
        <section class="panel ov-active" aria-labelledby="ov-active-h">
          <div class="panel-head">
            <h2 id="ov-active-h">Now processing and up next</h2>
            <span class="spacer"></span>
            <a class="btn btn-ghost btn-sm" href="/queue?status=active">Open queue${icon('chevron-right')}</a>
          </div>
          <ol class="job-feed" data-slot="active" aria-live="off">${skeletonRows(3)}</ol>
        </section>
        <section class="panel ov-recent" aria-labelledby="ov-recent-h">
          <div class="panel-head">
            <h2 id="ov-recent-h">Recently finished</h2>
            <span class="spacer"></span>
            <a class="btn btn-ghost btn-sm" href="/queue">Full history${icon('chevron-right')}</a>
          </div>
          <ul class="recent-list" data-slot="recent">${skeletonRows(4)}</ul>
        </section>
      </div>`,
  );

  const slots = {
    worker: $('[data-slot=worker]', root),
    last24: $('[data-slot=last24]', root),
    stats: $('[data-slot=stats]', root),
    active: $('[data-slot=active]', root),
    recent: $('[data-slot=recent]', root),
  };
  let data = null;

  const findJob = (jobId) => (data ? [...data.active, ...data.recent].find((j) => j.id === jobId) : null);

  async function load() {
    try {
      data = await api.get('/api/dashboard');
      publishDashboard(data);
      paint();
    } catch (err) {
      if (err.status === 401) return;
      publishOffline();
      if (!data) {
        render(slots.worker, errorState(err, { compact: true }));
        slots.last24.textContent = '';
        slots.active.textContent = '';
        slots.recent.textContent = '';
      }
    }
  }

  function paint() {
    paintWorker(slots.worker, data.worker);
    paintLast24(slots.last24, data.last_24h || {});
    paintStats(slots.stats, data);
    paintActive(slots.active, data.active || []);
    paintRecent(slots.recent, data.recent || []);
  }

  const poller = createPoller(load, 3000);
  const offClick = delegate(root, 'click', {
    ...jobHandlers(findJob, () => poller.trigger()),
    'retry-load': () => poller.trigger(),
    'scan-all': (el) =>
      busy(el, async () => {
        try {
          const res = await api.post('/api/libraries/scan');
          notify.ok(`Scan finished: ${plural(res.found || 0, 'file')} found, ${plural(res.queued || 0, 'job')} queued.`);
          if (res.errors && res.errors.length) notify.warn(`Some libraries could not be scanned: ${res.errors.join('; ')}`);
          poller.trigger();
        } catch (err) {
          notify.error(err);
        }
      }),
  });
  poller.start();

  return () => {
    poller.stop();
    offClick();
  };
}

function paintWorker(el, worker) {
  const w = worker || { state: 'idle' };
  render(
    el,
    html`
      <div class="panel-head">
        <h2 id="ov-worker-h">Worker</h2>
        <span class="spacer"></span>
        ${workerBadge(w.state)}
      </div>
      <div class="panel-body">
        <dl class="dl worker-dl">
          <dt>Model</dt><dd class="break">${w.model || html`<span class="muted">Not loaded</span>`}</dd>
          <dt>Device</dt><dd>${w.device ? String(w.device).toUpperCase() : '—'}${w.compute_type ? html` <span class="muted">${w.compute_type}</span>` : ''}</dd>
          <dt>Current job</dt><dd>${w.job_id ? html`<a href="/queue?status=processing">#${w.job_id}</a>` : html`<span class="muted">None</span>`}</dd>
          <dt>Updated</dt><dd><time datetime="${w.updated_at || ''}">${w.updated_at ? relative(w.updated_at) : '—'}</time></dd>
        </dl>
        ${w.state === 'error' && w.message ? html`<div class="callout" data-tone="danger" role="note">${icon('alert')}<div><strong>Last error</strong><pre class="error-text">${w.message}</pre><a href="/models">Check installed models</a> or <a href="/settings">review settings</a>.</div></div>` : ''}
      </div>`,
  );
}

function paintLast24(el, s) {
  const hours = (Number(s.audio_ms) || 0) / 3600000;
  render(
    el,
    html`
      <div class="panel-head"><h2 id="ov-24-h">Last 24 hours</h2></div>
      <div class="panel-body last24-grid">
        <div><span class="big num">${number(s.completed || 0)}</span><span class="muted small">subtitles completed</span></div>
        <div><span class="big num ${s.failed ? 'tone-danger' : ''}">${number(s.failed || 0)}</span><span class="muted small">failed${s.failed ? html` <a href="/queue?status=failed">Review</a>` : ''}</span></div>
        <div><span class="big num">${hours >= 1 ? (hours >= 10 ? hours.toFixed(0) : hours.toFixed(1)) : Math.round(hours * 60)}<small>${hours >= 1 ? 'h' : 'min'}</small></span><span class="muted small" title="${duration(s.audio_ms || 0)}">of audio transcribed</span></div>
      </div>`,
  );
}

function paintStats(el, d) {
  const j = d.jobs || {};
  const tile = (label, value, iconName, href, tone = '') =>
    html`<a class="stat" href="${href}" data-tone="${tone}"><span class="label">${icon(iconName)}${label}</span><span class="value">${number(value || 0)}</span></a>`;
  render(
    el,
    html`<div class="stat-strip">
      ${tile('Libraries', d.libraries, 'libraries', '/libraries')}
      ${tile('Media files', d.media, 'film', '/libraries')}
      ${tile('Queued', j.queued, 'clock', '/queue?status=queued')}
      ${tile('Processing', j.processing, 'loader', '/queue?status=processing')}
      ${tile('Completed', j.completed, 'check', '/queue?status=completed')}
      ${tile('Failed', j.failed, 'x-circle', '/queue?status=failed', j.failed ? 'failed' : '')}
    </div>`,
  );
}

export function activeJobRow(job) {
  const timing = jobTiming(job);
  return html`<li class="job-row" data-status="${job.status}">
    <div class="job-main">
      <div class="job-title">
        <span class="cell-title truncate" title="${job.media_path || job.media_name}">${job.media_name}</span>
        <span class="job-tags">${langLabel(job)}</span>
      </div>
      <div class="job-meta muted small">
        ${statusBadge(job.status)}
        <span>${jobProgressText(job)}</span>
        ${job.library_name ? html`<span class="faint">${job.library_name}</span>` : ''}
        ${job.priority ? html`<span class="faint">Priority ${job.priority}</span>` : ''}
      </div>
    </div>
    <div class="job-progress">
      ${job.status === 'processing'
        ? html`${track(job.stage === 'loading' || job.stage === 'probing' ? null : job.progress, { label: `Progress for ${job.media_name}` })}
          <div class="job-timing small num"><span class="pct">${Math.round(job.progress || 0)}%</span>${timing.map((t) => html`<span>${t}</span>`)}</div>`
        : html`<div class="job-timing small muted">${job.duration_ms ? html`<span class="num">${duration(job.duration_ms)} of media</span>` : ''}</div>`}
    </div>
    <div class="job-actions">${jobActionButtons(job, { include: ['cancel'] })}</div>
  </li>`;
}

function paintActive(el, jobs) {
  if (!jobs.length) {
    patchList(el, [], () => '', () => html``);
    render(
      el,
      html`<li>${emptyState({
        iconName: 'check-circle',
        compact: true,
        title: 'Nothing in the queue',
        text: 'Scan a library to find media without subtitles, or upload a file in Studio.',
        action: html`<div class="row"><button type="button" class="btn" data-action="scan-all">${icon('scan')}Scan all libraries</button><a class="btn btn-ghost" href="/studio">Open Studio</a></div>`,
      })}</li>`,
    );
    return;
  }
  patchList(el, jobs, (j) => j.id, activeJobRow);
}

function paintRecent(el, jobs) {
  if (!jobs.length) {
    render(el, html`<li>${emptyState({ iconName: 'history', compact: true, title: 'No finished jobs yet', text: 'Completed, failed and cancelled jobs appear here.' })}</li>`);
    return;
  }
  patchList(
    el,
    jobs,
    (j) => j.id,
    (job) => html`<li class="recent-row" data-status="${job.status}">
      ${statusBadge(job.status)}
      <div class="recent-main">
        <span class="cell-title truncate" title="${job.media_path || job.media_name}">${job.media_name}</span>
        <span class="cell-sub truncate">${String(job.language || '').toUpperCase()} ${String(job.format || '').toUpperCase()}${job.library_name ? html`, ${job.library_name}` : ''}${job.status === 'failed' && job.error ? html`<span class="tone-danger">: ${job.error}</span>` : ''}</span>
      </div>
      <time class="muted small nowrap" datetime="${job.finished_at || ''}" title="${STATUS_LABELS[job.status]} ${job.finished_at || ''}">${relative(job.finished_at)}</time>
      <div class="job-actions">${jobActionButtons(job, { include: ['edit', 'download', 'retry'] })}</div>
    </li>`,
  );
}
