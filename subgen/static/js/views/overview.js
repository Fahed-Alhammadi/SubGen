// Overview: the job being subtitled now (as a media timeline), what runs next, recent results
// and a short summary of the last 24 hours.
import { api } from '../api.js';
import { html, render, patchList, delegate, busy, $ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { createPoller } from '../ui/poller.js';
import { notify } from '../ui/toast.js';
import { pageHeader, statusBadge, langLabel, stageProgress, emptyState, errorState, skeletonRows } from '../ui/components.js';
import { number, relative, duration, clock, speed, plural, STAGE_LABELS, STATUS_LABELS } from '../ui/format.js';
import { publishDashboard, publishOffline } from '../store.js';
import { jobActionButtons, jobHandlers } from './job-actions.js';

const STEPS = [
  ['prepare', 'Prepare'],
  ['transcribe', 'Transcribe'],
  ['translate', 'Translate'],
  ['write', 'Write file'],
];
const STEP_OF_STAGE = {
  loading: 'prepare',
  probing: 'prepare',
  extracting: 'prepare',
  transcribing: 'transcribe',
  cached: 'transcribe',
  translating: 'translate',
  writing: 'write',
  done: 'write',
};

export async function mount(ctx) {
  ctx.setTitle('Overview');
  const root = ctx.root;
  render(
    root,
    html`
      ${pageHeader({
        title: 'Overview',
        subtitle: 'What SubGen is subtitling right now, and what finished recently.',
        actions: html`
          <button type="button" class="btn" data-action="scan-all">${icon('scan')}Scan libraries</button>
          <a class="btn btn-primary" href="/studio">${icon('upload')}Upload a file</a>`,
      })}
      <div class="overview">
        <section class="now" aria-labelledby="ov-now-h" aria-live="off" data-slot="now">${skeletonRows(3)}</section>
        <aside class="ov-side" aria-label="Summary">
          <section class="side-block" aria-labelledby="ov-24-h" data-slot="last24"></section>
          <section class="side-block" aria-labelledby="ov-tot-h" data-slot="totals"></section>
        </aside>
        <section class="ov-next" aria-labelledby="ov-next-h">
          <div class="section-head">
            <h2 id="ov-next-h">Up next</h2>
            <span class="count" data-slot="next-count"></span>
            <span class="spacer"></span>
            <a class="btn btn-ghost btn-sm" href="/queue?status=active">Open queue${icon('chevron-right')}</a>
          </div>
          <ol class="list feed panel" data-slot="next" aria-live="off">${skeletonRows(2)}</ol>
        </section>
        <section class="ov-recent" aria-labelledby="ov-recent-h">
          <div class="section-head">
            <h2 id="ov-recent-h">Recently finished</h2>
            <span class="spacer"></span>
            <a class="btn btn-ghost btn-sm" href="/queue">Full history${icon('chevron-right')}</a>
          </div>
          <ul class="list feed panel" data-slot="recent">${skeletonRows(4)}</ul>
        </section>
      </div>`,
  );

  const slots = {
    now: $('[data-slot=now]', root),
    last24: $('[data-slot=last24]', root),
    totals: $('[data-slot=totals]', root),
    next: $('[data-slot=next]', root),
    nextCount: $('[data-slot=next-count]', root),
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
        render(slots.now, errorState(err, { compact: true }));
        slots.next.textContent = '';
        slots.recent.textContent = '';
      }
    }
  }

  function paint() {
    const active = data.active || [];
    const current = active.find((j) => j.status === 'processing') || null;
    const next = active.filter((j) => j !== current);
    paintNow(slots.now, data, current, next.length);
    paintNext(slots.next, next);
    slots.nextCount.textContent = next.length ? number(next.length) : '';
    paintRecent(slots.recent, data.recent || []);
    paintLast24(slots.last24, data.last_24h || {});
    paintTotals(slots.totals, data);
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

/* ---------- Now: the live job as a media timeline ---------- */

function paintNow(el, d, job, waiting) {
  const w = d.worker || { state: 'idle' };
  if (job) {
    render(el, nowJob(job, w));
    return;
  }
  let state;
  if (w.state === 'error') {
    state = {
      iconName: 'alert',
      tone: 'danger',
      label: 'Stopped by an error',
      title: 'The last job failed',
      text: html`<pre class="error-text">${w.message || 'Unknown error'}</pre><span>Check the <a href="/models">installed models</a> or <a href="/settings">settings</a>, then retry the job from the queue.</span>`,
    };
  } else if (w.state === 'loading') {
    state = {
      iconName: 'hourglass',
      tone: 'busy',
      label: 'Getting ready',
      title: w.message || (w.model ? `Loading ${w.model}` : 'Loading the speech model'),
      text: waiting ? `${plural(waiting, 'job')} will start when it is ready.` : 'Jobs start as soon as it is ready.',
      indeterminate: true,
    };
  } else if (waiting) {
    state = {
      iconName: 'clock',
      tone: 'busy',
      label: 'Starting',
      title: 'Starting the next job',
      text: `${plural(waiting, 'job')} waiting.`,
      indeterminate: true,
    };
  } else {
    const last = (d.recent || []).find((j) => j.status === 'completed');
    state = {
      iconName: 'check',
      tone: 'idle',
      label: 'Idle',
      title: 'Nothing to subtitle right now',
      text: last
        ? html`Last finished: <a href="/editor/${last.id}">${last.media_name}</a>, ${languageAndFormat(last)}, ${relative(last.finished_at)}.`
        : 'New media is picked up on the next library scan, or upload a single file in Studio.',
    };
  }
  render(
    el,
    html`<div class="now-head">
        <span class="now-state" data-tone="${state.tone}">${icon(state.iconName)}${state.label}</span>
        <span class="spacer"></span>
        ${workerLine(w)}
      </div>
      <h2 class="now-title" id="ov-now-h">${state.title}</h2>
      <div class="now-meta">${state.text}</div>
      ${state.indeterminate ? timeline({ value: null }) : ''}`,
  );
}

function nowJob(job, w) {
  const transcribing = job.stage === 'transcribing';
  const position = job.duration_ms ? Math.min(100, ((job.processed_ms || 0) / job.duration_ms) * 100) : null;
  const pastTranscription = ['cached', 'translating', 'writing', 'done'].includes(job.stage);
  const value = transcribing ? position : pastTranscription ? 100 : null;
  const spoken = job.detected_language ? job.detected_language.toUpperCase() : null;
  const needsTranslation = job.detected_language ? job.detected_language !== job.language : null;
  const facts = [];
  if (job.speed) facts.push(html`<span>${speed(job.speed)}</span>`);
  if (job.eta_ms !== null && job.eta_ms !== undefined) facts.push(html`<span>${duration(job.eta_ms)} left</span>`);
  return html`<div class="now-head">
      <span class="now-state" data-tone="busy">${icon('loader', { cls: 'spin' })}${STAGE_LABELS[job.stage] || 'Working'}</span>
      <span class="spacer"></span>
      ${workerLine(w)}
      ${jobActionButtons(job, { compact: false, include: ['cancel'] })}
    </div>
    <h2 class="now-title truncate" id="ov-now-h" title="${job.media_path || job.media_name}">${job.media_name}</h2>
    <div class="now-meta">
      ${languageAndFormat(job)} subtitle for ${job.source === 'upload' ? 'a Studio upload' : html`the ${job.library_name || 'library'} library`}${spoken ? html`, spoken language ${spoken}` : ''}.
    </div>
    ${timeline({ value, durationMs: job.duration_ms, processedMs: transcribing ? job.processed_ms : pastTranscription ? job.duration_ms : null, label: `Progress for ${job.media_name}` })}
    <div class="now-foot">
      <ol class="steps" aria-label="Pipeline steps">${STEPS.map(([key, label]) => stepItem(key, label, job, needsTranslation))}</ol>
      <span class="spacer"></span>
      ${facts.length ? html`<span class="now-facts num">${facts}</span>` : ''}
    </div>`;
}

function stepItem(key, label, job, needsTranslation) {
  const order = STEPS.map(([k]) => k);
  const currentKey = STEP_OF_STAGE[job.stage] || 'prepare';
  const current = order.indexOf(currentKey);
  const mine = order.indexOf(key);
  let state = mine < current ? 'done' : mine === current ? 'current' : 'todo';
  if (key === 'translate' && needsTranslation === false) state = 'skipped';
  if (key === 'transcribe' && job.stage === 'cached' && state === 'current') state = 'done';
  const iconName = state === 'done' ? 'check' : state === 'current' ? 'loader' : state === 'skipped' ? 'minus' : 'circle';
  const note =
    key === 'transcribe' && job.stage === 'cached'
      ? 'reused'
      : state === 'skipped'
        ? 'not needed'
        : state === 'current' && (key === 'transcribe' || key === 'translate')
          ? `${Math.round(stageProgress(job))}%`
          : '';
  return html`<li class="step" data-state="${state}" ${state === 'current' ? 'aria-current="step"' : ''}>${icon(iconName, { cls: state === 'current' ? 'spin' : '' })}<span>${label}</span>${note ? html`<span class="step-note">${note}</span>` : ''}</li>`;
}

function workerLine(w) {
  if (!w || !w.model) return '';
  const device = w.device ? String(w.device).toUpperCase() : '';
  const where = device ? `on ${device}${w.compute_type ? `, ${w.compute_type}` : ''}` : '';
  return html`<span class="now-worker" title="Speech model">${icon('cpu')}<span>${w.model}${where ? html` <span class="muted">${where}</span>` : ''}</span></span>`;
}

/** Media timeline: timecode scale, ruler, the covered region and a playhead with its time. */
function timeline({ value = null, durationMs = 0, processedMs = null, label = 'Progress' }) {
  const indeterminate = value === null || value === undefined || !Number.isFinite(Number(value));
  const v = indeterminate ? 0 : Math.max(0, Math.min(100, Number(value)));
  const marks = durationMs ? [0, 0.25, 0.5, 0.75, 1].map((f) => clock(durationMs * f)) : [];
  const flag = processedMs !== null && processedMs !== undefined && durationMs ? clock(processedMs) : indeterminate ? '' : `${Math.round(v)}%`;
  return html`<div class="timeline ${indeterminate ? 'is-indeterminate' : ''}" data-value="${v}"
      role="progressbar" aria-label="${label}" aria-valuemin="0" aria-valuemax="100" ${indeterminate ? '' : html`aria-valuenow="${Math.round(v)}"`}>
    <div class="tl-scale num" aria-hidden="true">${marks.length ? marks.map((m) => html`<span>${m}</span>`) : html`<span></span>`}</div>
    <div class="tl-body">
      <div class="tl-ruler"></div>
      <div class="tl-track"><span class="tl-fill"></span></div>
      ${indeterminate ? '' : html`<span class="tl-head"><span class="tl-flag num">${flag}</span></span>`}
    </div>
  </div>`;
}

function languageAndFormat(job) {
  return `${job.language_name || String(job.language || '').toUpperCase()} ${String(job.format || '').toUpperCase()}`;
}

/* ---------- Up next ---------- */

function paintNext(el, jobs) {
  if (!jobs.length) {
    render(
      el,
      html`<li>${emptyState({
        iconName: 'check-circle',
        compact: true,
        title: 'Nothing waiting',
        text: 'Scan a library to find media without subtitles, or upload a single file in Studio.',
      })}</li>`,
    );
    return;
  }
  patchList(el, jobs, (j) => j.id, nextRow);
}

export function nextRow(job) {
  return html`<li class="feed-row" data-status="${job.status}">
    <div class="feed-main">
      <span class="cell-title truncate" title="${job.media_path || job.media_name}">${job.media_name}</span>
      <span class="feed-meta">
        <span class="feed-tags">${langLabel(job)}</span>
        <span>${job.source === 'upload' ? 'Studio upload' : job.library_name || ''}</span>
        ${job.priority ? html`<span>Priority ${job.priority}</span>` : ''}
      </span>
    </div>
    <time class="feed-time" datetime="${job.created_at || ''}">Queued ${relative(job.created_at)}</time>
    <div class="job-actions">${jobActionButtons(job, { include: ['cancel'] })}</div>
  </li>`;
}

/* ---------- Recently finished ---------- */

function paintRecent(el, jobs) {
  if (!jobs.length) {
    render(el, html`<li>${emptyState({ iconName: 'history', compact: true, title: 'No finished jobs yet', text: 'Completed, failed and cancelled jobs appear here.' })}</li>`);
    return;
  }
  patchList(
    el,
    jobs,
    (j) => j.id,
    (job) => html`<li class="feed-row" data-status="${job.status}">
      <div class="feed-main">
        <span class="feed-title">${statusBadge(job.status)}<span class="cell-title truncate" title="${job.media_path || job.media_name}">${job.media_name}</span></span>
        <span class="feed-meta">
          <span class="feed-tags">${langLabel(job)}</span>
          <span>${job.source === 'upload' ? 'Studio upload' : job.library_name || ''}</span>
          ${job.status === 'failed' && job.error ? html`<span class="tone-danger truncate feed-error" title="${job.error}">${job.error}</span>` : ''}
        </span>
      </div>
      <time class="feed-time" datetime="${job.finished_at || ''}" title="${STATUS_LABELS[job.status]} ${job.finished_at || ''}">${relative(job.finished_at)}</time>
      <div class="job-actions">${jobActionButtons(job, { compact: job.status !== 'failed', include: ['edit', 'download', 'retry'] })}</div>
    </li>`,
  );
}

/* ---------- Side summary ---------- */

function paintLast24(el, s) {
  const hours = (Number(s.audio_ms) || 0) / 3600000;
  const audio = hours >= 1 ? `${hours >= 10 ? hours.toFixed(0) : hours.toFixed(1)} h` : `${Math.round(hours * 60)} min`;
  render(
    el,
    html`<h2 id="ov-24-h">Last 24 hours</h2>
      <dl class="figures">
        <div><dt>Subtitles written</dt><dd class="num">${number(s.completed || 0)}</dd></div>
        <div data-tone="${s.failed ? 'danger' : ''}"><dt>Failed${s.failed ? html` <a href="/queue?status=failed">Review</a>` : ''}</dt><dd class="num">${number(s.failed || 0)}</dd></div>
        <div><dt title="${duration(s.audio_ms || 0)}">Audio transcribed</dt><dd class="num">${audio}</dd></div>
      </dl>`,
  );
}

function paintTotals(el, d) {
  const j = d.jobs || {};
  const row = (label, value, href, tone = '') =>
    html`<div data-tone="${tone}"><dt><a href="${href}">${label}</a></dt><dd class="num">${number(value || 0)}</dd></div>`;
  render(
    el,
    html`<h2 id="ov-tot-h">All time</h2>
      <dl class="totals">
        ${row('Libraries', d.libraries, '/libraries')}
        ${row('Media files', d.media, '/libraries')}
        ${row('Waiting in queue', j.queued, '/queue?status=queued')}
        ${row('Subtitles completed', j.completed, '/queue?status=completed')}
        ${row('Failed jobs', j.failed, '/queue?status=failed', j.failed ? 'danger' : '')}
      </dl>`,
  );
}
