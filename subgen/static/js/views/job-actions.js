// Job actions shared by Overview, Queue and Studio.
import { api } from '../api.js';
import { html } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { confirmDialog } from '../ui/dialog.js';
import { notify } from '../ui/toast.js';

const id = (job) => encodeURIComponent(job.id);

export async function cancelJob(job) {
  await api.post(`/api/jobs/${id(job)}/cancel`);
  notify.ok(job.status === 'processing' ? 'Cancellation requested.' : 'Job cancelled.');
}

export async function retryJob(job) {
  await api.post(`/api/jobs/${id(job)}/retry`);
  notify.ok('Job queued again.');
}

export async function regenerateJob(job) {
  const ok = await confirmDialog({
    title: 'Regenerate this subtitle?',
    message: `A new job will transcribe “${job.media_name}” again and overwrite the ${String(job.language).toUpperCase()} ${String(job.format).toUpperCase()} file when it finishes.`,
    detail: 'Manual edits to the current subtitle will be lost.',
    confirmLabel: 'Regenerate',
  });
  if (!ok) return false;
  await api.post(`/api/jobs/${id(job)}/regenerate`);
  notify.ok('Regeneration queued.');
  return true;
}

export async function deleteJobHistory(job) {
  const ok = await confirmDialog({
    title: 'Remove from history?',
    message: `The job for “${job.media_name}” is removed from the queue history.`,
    detail: 'The subtitle file on disk is kept.',
    confirmLabel: 'Remove',
    danger: true,
  });
  if (!ok) return false;
  await api.del(`/api/jobs/${id(job)}`);
  notify.ok('Removed from history.');
  return true;
}

export async function deleteSubtitleFile(job) {
  const ok = await confirmDialog({
    title: 'Delete subtitle file?',
    message: `The ${String(job.language).toUpperCase()} ${String(job.format).toUpperCase()} subtitle for “${job.media_name}” is deleted from disk.`,
    detail: job.output_path || '',
    confirmLabel: 'Delete file',
    danger: true,
  });
  if (!ok) return false;
  await api.del(`/api/jobs/${id(job)}/subtitle`);
  notify.ok('Subtitle file deleted.');
  return true;
}

export async function setPriority(job, priority) {
  const value = Math.max(-100, Math.min(100, Math.round(priority)));
  await api.put(`/api/jobs/${id(job)}/priority`, { priority: value });
  return value;
}

export function downloadUrl(jobId) {
  return `/api/subtitles/${encodeURIComponent(jobId)}/download`;
}

/** Compact action buttons for a job, depending on its status. */
export function jobActionButtons(job, { compact = true, include = null } = {}) {
  const want = (name) => !include || include.includes(name);
  const b = (action, iconName, label, extra = '') =>
    html`<button type="button" class="btn btn-ghost btn-sm ${compact ? 'btn-icon' : ''} ${extra}" data-action="${action}" data-id="${job.id}" aria-label="${label}" title="${label}">${icon(iconName)}${compact ? '' : html`<span>${label}</span>`}</button>`;
  const out = [];
  if (job.status === 'completed') {
    if (want('edit')) out.push(html`<a class="btn btn-ghost btn-sm ${compact ? 'btn-icon' : ''}" href="/editor/${job.id}" aria-label="Edit subtitle" title="Edit subtitle">${icon('edit')}${compact ? '' : html`<span>Edit</span>`}</a>`);
    if (want('download')) out.push(html`<a class="btn btn-ghost btn-sm ${compact ? 'btn-icon' : ''}" href="${downloadUrl(job.id)}" download aria-label="Download subtitle" title="Download subtitle">${icon('download')}${compact ? '' : html`<span>Download</span>`}</a>`);
    if (want('regenerate')) out.push(b('regenerate', 'scan', 'Regenerate'));
    if (want('delete-subtitle')) out.push(b('delete-subtitle', 'trash', 'Delete subtitle file', 'btn-danger'));
  }
  if (job.status === 'failed' || job.status === 'cancelled') {
    if (want('retry')) out.push(b('retry', 'retry', 'Retry'));
  }
  if (job.status === 'queued' || job.status === 'processing') {
    if (want('cancel')) out.push(b('cancel', 'x', job.status === 'processing' ? 'Stop job' : 'Cancel job', 'btn-danger'));
  }
  if (job.status !== 'processing' && job.status !== 'queued' && want('delete')) {
    out.push(b('delete', 'history', 'Remove from history'));
  }
  return out;
}

/**
 * Build a delegated action handler map for job buttons. `lookup(id)` returns
 * the job object, `refresh()` reloads the view.
 */
export function jobHandlers(lookup, refresh) {
  const wrap = (fn) => async (el) => {
    const job = lookup(Number(el.dataset.id));
    if (!job) return;
    el.disabled = true;
    try {
      const changed = await fn(job);
      if (changed !== false) await refresh();
    } catch (err) {
      notify.error(err);
    } finally {
      if (el.isConnected) el.disabled = false;
    }
  };
  return {
    cancel: wrap(cancelJob),
    retry: wrap(retryJob),
    regenerate: wrap(regenerateJob),
    delete: wrap(deleteJobHistory),
    'delete-subtitle': wrap(deleteSubtitleFile),
    'priority-up': wrap((job) => setPriority(job, (job.priority || 0) + 1)),
    'priority-down': wrap((job) => setPriority(job, (job.priority || 0) - 1)),
  };
}
