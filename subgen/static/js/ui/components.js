// Shared presentational pieces rendered as SafeHtml.
import { html } from './dom.js';
import { icon } from './icons.js';
import { STATUS_LABELS, STAGE_LABELS, WORKER_LABELS, clock, duration, speed, number } from './format.js';

const STATUS_ICON = {
  queued: 'clock',
  processing: 'loader',
  completed: 'check',
  failed: 'x-circle',
  cancelled: 'ban',
};

export function statusBadge(status) {
  const spin = status === 'processing' ? 'spin' : '';
  return html`<span class="badge" data-tone="${status}">${icon(STATUS_ICON[status] || 'circle', { cls: spin })}${STATUS_LABELS[status] || status}</span>`;
}

export function workerBadge(state) {
  const name = { idle: 'check', loading: 'hourglass', processing: 'loader', error: 'alert', offline: 'alert' }[state] || 'circle';
  return html`<span class="badge" data-tone="${state === 'idle' ? 'ok' : state}">${icon(name, { cls: state === 'processing' ? 'spin' : '' })}${WORKER_LABELS[state] || state}</span>`;
}

/** Timeline-style progress track. value: 0-100 or null for indeterminate. */
export function track(value, { label = 'Progress', tone = '', thin = false } = {}) {
  const indeterminate = value === null || value === undefined || !Number.isFinite(Number(value));
  const v = indeterminate ? 0 : Math.max(0, Math.min(100, Number(value)));
  return html`<div class="track ${indeterminate ? 'indeterminate' : ''} ${thin ? 'thin' : ''}" data-tone="${tone}" data-value="${v}"
    role="progressbar" aria-label="${label}" aria-valuemin="0" aria-valuemax="100" ${indeterminate ? '' : html`aria-valuenow="${Math.round(v)}"`}><span class="fill"></span><span class="head"></span></div>`;
}

export function meter(value, label) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  const level = v >= 92 ? 'critical' : v >= 80 ? 'high' : 'normal';
  return html`<div class="meter" data-value="${v}" data-level="${level}" role="meter" aria-label="${label}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(v)}"><span></span></div>`;
}

/** Short description of where a job is in the pipeline. */
export function jobProgressText(job) {
  if (job.status === 'queued') return 'Waiting in queue';
  if (job.status !== 'processing') return STATUS_LABELS[job.status] || job.status;
  return STAGE_LABELS[job.stage] || job.stage || 'Working';
}

export function jobTiming(job) {
  const pieces = [];
  if (job.duration_ms) pieces.push(`${clock(job.processed_ms || 0)} / ${clock(job.duration_ms)}`);
  const sp = speed(job.speed);
  if (sp) pieces.push(sp);
  if (job.eta_ms !== null && job.eta_ms !== undefined && job.status === 'processing') pieces.push(`${duration(job.eta_ms)} left`);
  return pieces;
}

export function langLabel(job) {
  return html`<span class="badge plain" data-tone="outline" title="${job.language_name || job.language}">${String(job.language || '').toUpperCase()}</span>
    <span class="badge plain" data-tone="outline">${String(job.format || '').toUpperCase()}</span>`;
}

export function emptyState({ iconName = 'info', title, text = '', action = null, compact = false, tone = '' }) {
  return html`<div class="empty ${compact ? 'compact' : ''}" data-tone="${tone}">
    <span class="empty-icon">${icon(iconName)}</span>
    <h3>${title}</h3>
    ${text ? html`<p>${text}</p>` : ''}
    ${action || ''}
  </div>`;
}

export function errorState(err, { retryAction = 'retry-load', compact = false } = {}) {
  const message = err && err.message ? err.message : String(err || 'Something went wrong.');
  return emptyState({
    iconName: 'alert',
    tone: 'error',
    compact,
    title: 'Could not load this data',
    text: message,
    action: html`<button type="button" class="btn" data-action="${retryAction}">${icon('retry')}Try again</button>`,
  });
}

export function skeletonRows(n = 4, cls = '') {
  return html`<div class="skeleton-list" role="status" aria-label="Loading">${Array.from({ length: n }, () => html`<span class="skeleton ${cls}"></span>`)}</div>`;
}

/** Pager: "1–50 of 340" + prev/next buttons (data-action="page-prev|page-next"). */
export function pager({ total, limit, offset }) {
  const start = total ? offset + 1 : 0;
  const end = Math.min(total, offset + limit);
  return html`<div class="pager">
    <span>${number(start)}–${number(end)} of ${number(total)}</span>
    <button type="button" class="btn btn-sm btn-icon" data-action="page-prev" aria-label="Previous page" ${offset <= 0 ? 'disabled' : ''}>${icon('chevron-left')}</button>
    <button type="button" class="btn btn-sm btn-icon" data-action="page-next" aria-label="Next page" ${end >= total ? 'disabled' : ''}>${icon('chevron-right')}</button>
  </div>`;
}

/** Derive a chip state from a MediaItem subtitle entry. */
export function subtitleState(sub) {
  if (sub.job_status === 'processing') return 'processing';
  if (sub.job_status === 'queued') return 'queued';
  if (sub.exists) return 'complete';
  if (sub.job_status === 'failed') return 'failed';
  return 'missing';
}

const CHIP_ICON = { complete: 'check', processing: 'loader', queued: 'clock', failed: 'x', missing: 'minus' };
const CHIP_TEXT = { complete: 'available', processing: 'processing', queued: 'queued', failed: 'failed', missing: 'missing' };

export function subtitleChip(sub) {
  const state = subtitleState(sub);
  const inner = html`${icon(CHIP_ICON[state], { cls: state === 'processing' ? 'spin' : '' })}${String(sub.language).toUpperCase()}<span class="fmt">${sub.format}</span><span class="sr-only">${CHIP_TEXT[state]}</span>`;
  if (state === 'complete' && sub.job_id) {
    return html`<a class="chip" data-state="${state}" href="/editor/${sub.job_id}" title="Edit ${sub.language}.${sub.format}">${inner}</a>`;
  }
  return html`<span class="chip" data-state="${state}" title="${sub.language}.${sub.format}: ${CHIP_TEXT[state]}">${inner}</span>`;
}

/** Page header: optional back crumb, h1, subtitle and an actions slot. */
export function pageHeader({ title, subtitle = '', actions = '', crumb = null }) {
  return html`<header class="page-header">
    <div class="titles">
      ${crumb ? html`<a class="crumb" href="${crumb.href}">${icon('chevron-left')}${crumb.label}</a>` : ''}
      <h1>${title}</h1>
      ${subtitle ? html`<p class="subtitle">${subtitle}</p>` : ''}
    </div>
    <div class="actions">${actions}</div>
  </header>`;
}
