// Queue: filterable job history with selection, bulk actions and job details.
import { api, qs } from '../api.js';
import { html, render, patchList, delegate, debounce, element, busy, $, $$ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { createPoller } from '../ui/poller.js';
import { notify } from '../ui/toast.js';
import { openDialog, confirmDialog } from '../ui/dialog.js';
import { pageHeader, statusBadge, track, jobTiming, langLabel, stageProgress, emptyState, errorState, skeletonRows, pager } from '../ui/components.js';
import { relative, dateTime, elapsed, clock, speed, number, plural, STAGE_LABELS } from '../ui/format.js';
import { onWorker, workerState } from '../store.js';
import { jobActionButtons, jobHandlers, setPriority } from './job-actions.js';

const LIMIT = 50;
const TABS = [
  ['', 'All'],
  ['active', 'Active'],
  ['queued', 'Queued'],
  ['processing', 'Processing'],
  ['completed', 'Completed'],
  ['failed', 'Failed'],
  ['cancelled', 'Cancelled'],
];
const SORTS = [
  ['newest', 'Newest first'],
  ['oldest', 'Oldest first'],
  ['queue', 'Run order'],
];

export async function mount(ctx) {
  ctx.setTitle('Queue');
  const root = ctx.root;
  const state = {
    status: ctx.query.get('status') || '',
    q: ctx.query.get('q') || '',
    library_id: ctx.query.get('library') || '',
    sort: ctx.query.get('sort') || 'newest',
    offset: Math.max(0, Number(ctx.query.get('offset')) || 0),
  };
  if (!TABS.some(([v]) => v === state.status)) state.status = '';
  const selected = new Set();
  let page = { items: [], total: 0 };
  let loaded = false;

  render(
    root,
    html`${pageHeader({
      title: 'Queue',
      subtitle: 'Every subtitle job, waiting, running and finished. Jobs run one at a time, highest priority first.',
      actions: html`<button type="button" class="btn" data-action="clear-completed">${icon('history')}Clear completed jobs</button>`,
    })}
    <section class="panel queue-panel" aria-label="Jobs">
      <div class="tabs queue-tabs" role="group" aria-label="Filter by status">
        ${TABS.map(
          ([v, label]) => html`<button type="button" class="tab" data-action="tab" data-status="${v}" aria-pressed="${state.status === v ? 'true' : 'false'}">${label}<span class="count" data-count="${v}" hidden></span></button>`,
        )}
      </div>
      <div class="toolbar">
        <div class="input-group">
          ${icon('search')}
          <input class="input" type="search" data-q placeholder="Search file names" aria-label="Search file names" value="${state.q}">
        </div>
        <select class="select" data-library aria-label="Library"><option value="">All libraries</option></select>
        <select class="select" data-sort aria-label="Sort order">
          ${SORTS.map(([v, label]) => html`<option value="${v}" ${state.sort === v ? 'selected' : ''}>${label}</option>`)}
        </select>
        <span class="spacer"></span>
        <div data-slot="pager"></div>
      </div>
      <div class="bulkbar" data-slot="bulk" hidden role="region" aria-label="Bulk actions">
        <strong class="num" data-bulk-count></strong>
        <button type="button" class="btn btn-sm" data-action="bulk-cancel">${icon('x')}Cancel</button>
        <button type="button" class="btn btn-sm" data-action="bulk-retry">${icon('retry')}Retry</button>
        <button type="button" class="btn btn-sm" data-action="bulk-prioritize">${icon('arrow-up')}Set priority</button>
        <button type="button" class="btn btn-sm btn-danger" data-action="bulk-delete">${icon('history')}Remove from history</button>
        <span class="spacer"></span>
        <button type="button" class="btn btn-sm btn-ghost" data-action="clear-selection">Clear selection</button>
      </div>
      <div id="queue-table" data-slot="table">${skeletonRows(8)}</div>
    </section>`,
  );

  const tableEl = $('[data-slot=table]', root);
  const pagerEl = $('[data-slot=pager]', root);
  const bulkEl = $('[data-slot=bulk]', root);
  const searchEl = $('[data-q]', root);
  const libraryEl = $('[data-library]', root);
  const sortEl = $('[data-sort]', root);

  api
    .get('/api/libraries')
    .then((libs) => {
      for (const lib of libs) {
        const opt = document.createElement('option');
        opt.value = String(lib.id);
        opt.textContent = lib.name;
        if (String(lib.id) === state.library_id) opt.selected = true;
        libraryEl.append(opt);
      }
    })
    .catch(() => {});

  function paintCounts(ws) {
    const j = ws.jobs;
    $$('[data-count]', root).forEach((el) => {
      const key = el.dataset.count;
      let n = null;
      if (j) {
        if (key === 'active') n = (j.queued || 0) + (j.processing || 0);
        else if (key && key in j) n = j[key];
      }
      el.hidden = n === null || n === 0;
      el.textContent = n === null ? '' : number(n);
    });
  }
  const offWorker = onWorker(paintCounts);
  paintCounts(workerState);

  async function load() {
    try {
      page = await api.get(
        `/api/jobs${qs({ status: state.status, q: state.q, library_id: state.library_id, sort: state.sort, limit: LIMIT, offset: state.offset })}`,
      );
      loaded = true;
      // Drop selections that are no longer visible.
      const ids = new Set(page.items.map((j) => j.id));
      for (const id of [...selected]) if (!ids.has(id)) selected.delete(id);
      paint();
    } catch (err) {
      if (err.status !== 401 && !loaded) render(tableEl, errorState(err));
    }
  }

  function paint() {
    render(pagerEl, page.total > LIMIT || state.offset ? pager({ total: page.total, limit: LIMIT, offset: state.offset }) : html``);
    paintBulk();
    if (!page.items.length) {
      const filtered = state.q || state.library_id || state.status;
      render(
        tableEl,
        filtered
          ? emptyState({
              iconName: 'filter',
              title: state.status ? `No ${(TABS.find(([v]) => v === state.status) || ['', ''])[1].toLowerCase()} jobs` : 'No matching jobs',
              text: 'Try another status tab, library or search.',
              action: html`<button type="button" class="btn" data-action="clear-filters">Show all jobs</button>`,
            })
          : emptyState({
              iconName: 'queue',
              title: 'The queue is empty',
              text: 'Jobs appear here when a library scan finds media without subtitles or when you generate subtitles from Studio.',
              action: html`<div class="row"><a class="btn btn-primary" href="/libraries">${icon('libraries')}Go to libraries</a><a class="btn" href="/studio">Open Studio</a></div>`,
            }),
      );
      return;
    }
    let body = tableEl.querySelector('tbody');
    if (!body) {
      render(
        tableEl,
        html`<div class="table-wrap"><table class="table queue-table">
          <thead><tr>
            <th class="col-check" scope="col"><input type="checkbox" data-select-all aria-label="Select all jobs on this page"></th>
            <th scope="col">File</th>
            <th scope="col">Subtitle</th>
            <th scope="col" class="col-status">Status</th>
            <th scope="col">Updated</th>
            <th scope="col" class="col-actions"><span class="sr-only">Actions</span></th>
          </tr></thead>
          <tbody></tbody>
        </table></div>`,
      );
      body = tableEl.querySelector('tbody');
    }
    patchList(body, page.items, (j) => j.id, (job) => jobRow(job, selected.has(job.id)));
    syncSelectAll();
  }

  function syncSelectAll() {
    const all = $('[data-select-all]', tableEl);
    if (!all) return;
    const n = page.items.filter((j) => selected.has(j.id)).length;
    all.checked = n > 0 && n === page.items.length;
    all.indeterminate = n > 0 && n < page.items.length;
  }

  function paintBulk() {
    bulkEl.hidden = selected.size === 0;
    $('[data-bulk-count]', bulkEl).textContent = `${selected.size} selected`;
  }

  function syncQuery() {
    ctx.replaceQuery({ status: state.status, q: state.q, library: state.library_id, sort: state.sort === 'newest' ? '' : state.sort, offset: state.offset || '' });
  }

  function refilter() {
    state.offset = 0;
    selected.clear();
    syncQuery();
    loaded = false;
    const body = tableEl.querySelector('tbody');
    if (body) body.textContent = '';
    poller.trigger();
  }

  const hasActive = () => page.items.some((j) => j.status === 'queued' || j.status === 'processing');
  const poller = createPoller(load, () => (hasActive() || state.status === 'active' ? 3000 : 10000));

  const onSearch = debounce(() => {
    state.q = searchEl.value.trim();
    refilter();
  }, 300);
  searchEl.addEventListener('input', onSearch);
  libraryEl.addEventListener('change', () => {
    state.library_id = libraryEl.value;
    refilter();
  });
  sortEl.addEventListener('change', () => {
    state.sort = sortEl.value;
    refilter();
  });

  const findJob = (id) => page.items.find((j) => j.id === id);

  async function bulk(action, extra = {}, button) {
    const ids = [...selected];
    if (!ids.length) return;
    await busy(button, async () => {
      try {
        const res = await api.post('/api/jobs/bulk', { action, ids, ...extra });
        notify.ok(`${plural(res.affected || 0, 'job')} updated.`);
        if (action === 'delete') selected.clear();
        poller.trigger();
      } catch (err) {
        notify.error(err);
      }
    });
  }

  const offClick = delegate(root, 'click', {
    ...jobHandlers(findJob, () => poller.trigger()),
    'retry-load': () => poller.trigger(),
    tab: (el) => {
      state.status = el.dataset.status;
      $$('[data-action=tab]', root).forEach((t) => t.setAttribute('aria-pressed', String(t === el)));
      refilter();
    },
    'clear-filters': () => {
      state.status = '';
      state.q = '';
      state.library_id = '';
      searchEl.value = '';
      libraryEl.value = '';
      $$('[data-action=tab]', root).forEach((t) => t.setAttribute('aria-pressed', String(t.dataset.status === '')));
      refilter();
    },
    'page-prev': () => {
      state.offset = Math.max(0, state.offset - LIMIT);
      syncQuery();
      poller.trigger();
    },
    'page-next': () => {
      state.offset += LIMIT;
      syncQuery();
      poller.trigger();
    },
    details: (el) => showDetails(Number(el.dataset.id)),
    'clear-selection': () => {
      selected.clear();
      paint();
    },
    'bulk-cancel': (el) => bulk('cancel', {}, el),
    'bulk-retry': (el) => bulk('retry', {}, el),
    'bulk-delete': async (el) => {
      const ok = await confirmDialog({
        title: `Remove ${plural(selected.size, 'job')} from history?`,
        message: 'Processing jobs are skipped. Subtitle files on disk are kept.',
        confirmLabel: 'Remove from history',
        danger: true,
      });
      if (ok) bulk('delete', {}, el);
    },
    'bulk-prioritize': async (el) => {
      const value = await priorityDialog(selected.size);
      if (value !== undefined) bulk('prioritize', { priority: value }, el);
    },
    'clear-completed': async (el) => {
      const ok = await confirmDialog({
        title: 'Clear completed history?',
        message: 'All completed jobs are removed from the queue history.',
        detail: 'Subtitle files on disk are kept and can still be edited from the library browser.',
        confirmLabel: 'Clear history',
        danger: true,
      });
      if (!ok) return;
      await busy(el, async () => {
        try {
          const res = await api.post('/api/jobs/bulk', { action: 'delete', status: 'completed' });
          notify.ok(`Cleared ${plural(res.affected || 0, 'completed job')}.`);
          poller.trigger();
        } catch (err) {
          notify.error(err);
        }
      });
    },
  });

  const offChange = delegate(root, 'change', {
    select: (el) => {
      const id = Number(el.dataset.id);
      if (el.checked) selected.add(id);
      else selected.delete(id);
      const row = el.closest('tr');
      if (row) row.dataset.selected = String(el.checked);
      paintBulk();
      syncSelectAll();
    },
  });
  // Select-all has no data-action so it isn't confused with row handlers.
  const onSelectAll = (event) => {
    if (!event.target.matches('[data-select-all]')) return;
    const on = event.target.checked;
    page.items.forEach((j) => (on ? selected.add(j.id) : selected.delete(j.id)));
    paint();
  };
  root.addEventListener('change', onSelectAll);

  async function showDetails(id) {
    let job = findJob(id);
    try {
      job = await api.get(`/api/jobs/${encodeURIComponent(id)}`);
    } catch (err) {
      if (!job) {
        notify.error(err);
        return;
      }
    }
    const body = element(html`<div class="stack job-detail">
      <div class="row">${statusBadge(job.status)}<span class="muted">${job.status === 'processing' ? STAGE_LABELS[job.stage] || job.stage : ''}</span></div>
      ${job.status === 'processing' ? html`<div class="stack-sm">${track(stageProgress(job), { label: 'Progress' })}<span class="small muted num">${Math.round(stageProgress(job))}%, ${jobTiming(job).join(', ')}</span></div>` : ''}
      ${job.error ? html`<div class="stack-sm"><h3>Error</h3><pre class="error-text">${job.error}</pre></div>` : ''}
      <dl class="dl">
        <dt>Media</dt><dd>${job.media_name}</dd>
        <dt>Path</dt><dd><code>${job.media_path}</code></dd>
        <dt>Source</dt><dd>${job.source === 'upload' ? 'Studio upload' : job.library_name || 'Library'}</dd>
        <dt>Language</dt><dd>${job.language_name || job.language} (${job.language})${job.detected_language ? html`<span class="muted">, spoken language detected as ${job.detected_language}</span>` : ''}</dd>
        <dt>Format</dt><dd>${String(job.format).toUpperCase()}</dd>
        <dt>Priority</dt><dd>${job.status === 'queued'
          ? html`<span class="priority-editor">
              <button type="button" class="btn btn-sm btn-icon" data-action="priority-down" data-id="${job.id}" aria-label="Lower priority" title="Lower priority">${icon('chevron-down')}</button>
              <span class="num" data-priority-value aria-live="polite">${job.priority}</span>
              <button type="button" class="btn btn-sm btn-icon" data-action="priority-up" data-id="${job.id}" aria-label="Raise priority" title="Raise priority">${icon('chevron-up')}</button>
              <span class="small muted">Higher runs first</span>
            </span>`
          : job.priority}</dd>
        <dt>Media length</dt><dd>${job.duration_ms ? clock(job.duration_ms) : '—'}</dd>
        ${job.speed ? html`<dt>Speed</dt><dd>${speed(job.speed)}</dd>` : ''}
        <dt>Created</dt><dd>${dateTime(job.created_at)}</dd>
        <dt>Started</dt><dd>${dateTime(job.started_at)}</dd>
        <dt>Finished</dt><dd>${dateTime(job.finished_at)}</dd>
        <dt>Run time</dt><dd>${job.started_at ? elapsed(job.started_at, job.finished_at) : '—'}</dd>
        <dt>Waited</dt><dd>${job.started_at ? elapsed(job.created_at, job.started_at) : job.status === 'queued' ? `${elapsed(job.created_at)} so far` : '—'}</dd>
        ${job.output_path ? html`<dt>Output</dt><dd><code>${job.output_path}</code></dd>` : ''}
        ${job.options && Object.keys(job.options).length ? html`<dt>Overrides</dt><dd><code>${JSON.stringify(job.options)}</code></dd>` : ''}
      </dl>
    </div>`);
    const dlg = openDialog({
      title: `Job #${job.id}`,
      size: 'wide',
      body,
      footer: html`<div class="btn-group left">${jobActionButtons(job, { compact: false, include: ['edit', 'download', 'retry', 'cancel', 'regenerate', 'delete', 'delete-subtitle'] })}</div>
        <button type="button" class="btn" data-dialog-close>Close</button>`,
    });
    const handlers = jobHandlers(
      () => job,
      async () => {
        dlg.close();
        poller.trigger();
      },
    );
    dlg.el.addEventListener('click', async (event) => {
      const target = event.target.closest('[data-action]');
      const action = target && target.dataset.action;
      if (action === 'priority-up' || action === 'priority-down') {
        // Adjust in place; the dialog stays open.
        target.disabled = true;
        try {
          job.priority = await setPriority(job, (job.priority || 0) + (action === 'priority-up' ? 1 : -1));
          const valueEl = dlg.el.querySelector('[data-priority-value]');
          if (valueEl) valueEl.textContent = String(job.priority);
          poller.trigger();
        } catch (err) {
          notify.error(err);
        } finally {
          target.disabled = false;
        }
        return;
      }
      if (target && handlers[action]) handlers[action](target, event);
      else if (event.target.closest('a[href^="/editor"]')) dlg.close();
    });
  }

  poller.start();
  return () => {
    poller.stop();
    offClick();
    offChange();
    root.removeEventListener('change', onSelectAll);
    offWorker();
    onSearch.cancel();
  };
}

function jobRow(job, isSelected) {
  const timing = jobTiming(job);
  const when = job.finished_at || job.started_at || job.created_at;
  return html`<tr data-status="${job.status}" data-selected="${isSelected ? 'true' : 'false'}">
    <td class="col-check"><input type="checkbox" data-action="select" data-id="${job.id}" ${isSelected ? 'checked' : ''} aria-label="Select ${job.media_name} ${job.language} ${job.format}"></td>
    <td class="col-media">
      <button type="button" class="link-btn cell-title truncate" data-action="details" data-id="${job.id}" title="${job.media_path}">${job.media_name}</button>
      <span class="cell-sub truncate">${job.source === 'upload' ? 'Studio upload' : job.library_name || ''}</span>
    </td>
    <td class="col-target nowrap"><span class="tags">${langLabel(job)}</span></td>
    <td class="col-status">${statusCell(job, timing)}</td>
    <td class="col-when nowrap small muted"><time datetime="${when || ''}" title="${dateTime(when)}">${relative(when)}</time></td>
    <td class="col-actions">
      <div class="btn-group">
        ${primaryAction(job)}
        <button type="button" class="btn btn-ghost btn-icon btn-sm" data-action="details" data-id="${job.id}" aria-label="Details and more actions for job ${job.id}" title="Details and more actions">${icon('more')}</button>
      </div>
    </td>
  </tr>`;
}

function statusCell(job, timing) {
  if (job.status === 'processing') {
    const step = stageProgress(job);
    return html`<span class="badge" data-tone="processing">${icon('loader', { cls: 'spin' })}${STAGE_LABELS[job.stage] || 'Working'} ${Math.round(step)}%</span>
      ${track(step, { label: `${STAGE_LABELS[job.stage] || 'Progress'} for ${job.media_name}`, thin: true })}
      ${timing.length ? html`<span class="cell-sub num">${timing.join(', ')}</span>` : ''}`;
  }
  const sub =
    job.status === 'failed' && job.error
      ? html`<span class="cell-sub tone-danger truncate" title="${job.error}">${job.error}</span>`
      : job.status === 'queued' && job.priority
        ? html`<span class="cell-sub">Priority ${job.priority}</span>`
        : job.status === 'completed' && job.duration_ms
          ? html`<span class="cell-sub num">${clock(job.duration_ms)} of media</span>`
          : '';
  return html`${statusBadge(job.status)}${sub}`;
}

/** The one action a row offers directly; everything else lives in the details dialog. */
function primaryAction(job) {
  if (job.status === 'completed') return jobActionButtons(job, { compact: false, include: ['edit'] });
  if (job.status === 'failed' || job.status === 'cancelled') return jobActionButtons(job, { compact: false, include: ['retry'] });
  return jobActionButtons(job, { include: ['cancel'] });
}

function priorityDialog(count) {
  const formId = `prio-${Date.now()}`;
  const form = element(html`<form id="${formId}" class="stack" novalidate>
    <p>Set the priority for ${plural(count, 'selected job')}. Higher numbers run first.</p>
    <div class="field">
      <label for="${formId}-v">Priority (−100 to 100)</label>
      <input class="input" id="${formId}-v" type="number" min="-100" max="100" step="1" value="10" required>
    </div>
  </form>`);
  const dlg = openDialog({
    title: 'Set priority',
    size: 'narrow',
    body: form,
    footer: html`<button type="button" class="btn" data-dialog-close>Cancel</button><button type="submit" class="btn btn-primary" form="${formId}">Set priority</button>`,
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const v = Number(form.querySelector('input').value);
    if (!Number.isInteger(v) || v < -100 || v > 100) {
      notify.error('Priority must be a whole number from −100 to 100.');
      return;
    }
    dlg.close(v);
  });
  return dlg.closed;
}

