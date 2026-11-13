// Models: one table per model type (in use first, then installed, then available), downloads,
// and a Hugging Face search for anything not in the catalog.
import { api, qs } from '../api.js';
import { html, render, delegate, busy, patchList, $ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { createPoller } from '../ui/poller.js';
import { notify } from '../ui/toast.js';
import { confirmDialog } from '../ui/dialog.js';
import { pageHeader, emptyState, errorState, skeletonRows, track } from '../ui/components.js';
import { bytes, number } from '../ui/format.js';

const KIND_LABEL = { whisper: 'Transcription', translation: 'Translation' };
const KIND_INTRO = {
  whisper: 'faster-whisper models turn speech into text. Larger models are more accurate and slower; on a CPU, Small is the usual choice.',
  translation: 'Local models that translate subtitles into other languages. Used when the translation provider in Settings is the local Hugging Face model.',
};
const ACTIVE = new Set(['queued', 'downloading']);
const STATE_ORDER = { 'in-use': 0, downloading: 1, installed: 2, available: 3 };

export async function mount(ctx) {
  ctx.setTitle('Models');
  const root = ctx.root;
  render(
    root,
    html`${pageHeader({
      title: 'Models',
      subtitle: 'Speech recognition and translation models stored on this server. The models in use are installed automatically when they are missing; jobs never download anything themselves.',
    })}
    <div class="models">
      <div class="stack" data-slot="notices">
        <div class="stack-sm" data-slot="missing"></div>
        <section class="panel" aria-labelledby="dl-h" data-slot="downloads-panel" hidden>
          <div class="panel-head"><h2 id="dl-h">Downloads</h2></div>
          <ul class="list download-list" data-slot="downloads"></ul>
        </section>
      </div>
      <section class="model-kind" aria-labelledby="mk-whisper-h">
        <div class="section-head"><h2 id="mk-whisper-h">${KIND_LABEL.whisper}</h2><span class="count" data-slot="whisper-count"></span></div>
        <p class="kind-intro">${KIND_INTRO.whisper}</p>
        <div class="panel" data-slot="whisper">${skeletonRows(4)}</div>
      </section>
      <section class="model-kind" aria-labelledby="mk-translation-h">
        <div class="section-head"><h2 id="mk-translation-h">${KIND_LABEL.translation}</h2><span class="count" data-slot="translation-count"></span></div>
        <p class="kind-intro">${KIND_INTRO.translation}</p>
        <div class="panel" data-slot="translation">${skeletonRows(3)}</div>
      </section>
      <section class="model-kind" aria-labelledby="hf-h">
        <div class="section-head"><h2 id="hf-h">Add from Hugging Face</h2></div>
        <p class="kind-intro">Search for any CTranslate2 Whisper model or Transformers translation model, or paste its owner/name. The server needs internet access for this.</p>
        <div class="panel">
          <form class="toolbar" data-search-form role="search">
            <select class="select" name="kind" aria-label="Model type">
              <option value="whisper">Transcription (faster-whisper)</option>
              <option value="translation">Translation</option>
            </select>
            <div class="input-group">
              ${icon('search')}
              <input class="input" type="search" name="q" placeholder="Search or paste owner/name" aria-label="Search models" minlength="2" autocomplete="off" spellcheck="false">
            </div>
            <button type="submit" class="btn">Search</button>
          </form>
          <div data-slot="results"></div>
        </div>
      </section>
    </div>`,
  );

  const slots = {
    notices: $('[data-slot=notices]', root),
    missing: $('[data-slot=missing]', root),
    downloadsPanel: $('[data-slot=downloads-panel]', root),
    downloads: $('[data-slot=downloads]', root),
    whisper: $('[data-slot=whisper]', root),
    translation: $('[data-slot=translation]', root),
    whisperCount: $('[data-slot=whisper-count]', root),
    translationCount: $('[data-slot=translation-count]', root),
    results: $('[data-slot=results]', root),
  };
  let data = null;
  let lastActiveCount = 0;
  let results = null;

  async function load() {
    try {
      data = await api.get('/api/models');
      const activeCount = data.downloads.filter((d) => ACTIVE.has(d.status)).length;
      if (activeCount < lastActiveCount) {
        // A download finished: the catalog's installed flags changed too.
        const done = data.downloads.filter((d) => d.status === 'completed');
        if (done.length) notify.ok('Model download finished.');
      }
      lastActiveCount = activeCount;
      paint();
    } catch (err) {
      if (err.status === 401) return;
      if (!data) render(slots.whisper, errorState(err));
    }
  }

  const installedSet = () => new Set((data ? data.installed : []).map((m) => `${m.kind}:${m.repo_id}`));
  const downloadFor = (kind, repo) =>
    (data ? data.downloads : []).find((d) => d.kind === kind && d.repo_id === repo && ACTIVE.has(d.status)) || null;

  function paint() {
    paintMissing();
    paintDownloads();
    // Nothing to report: drop the empty block so it adds no gap above the tables.
    slots.notices.hidden = !slots.missing.childElementCount && slots.downloadsPanel.hidden;
    paintKind('whisper');
    paintKind('translation');
    if (results) paintResults();
  }

  /** Selected models that are not on disk yet (normally because they are being installed). */
  function paintMissing() {
    const missing = (data.selected || []).filter((m) => !m.installed);
    render(
      slots.missing,
      html`${missing.map((m) => {
        const downloading = Boolean(downloadFor(m.kind, m.repo_id));
        return html`<div class="callout missing-model" data-tone="${downloading ? 'info' : 'danger'}" role="status">
          ${icon(downloading ? 'loader' : 'alert', { cls: downloading ? 'spin' : '' })}
          <div>
            <strong>${KIND_LABEL[m.kind] || m.kind} model in use: <code class="break">${m.repo_id}</code></strong>
            <span>${downloading
              ? 'It is being installed now. Jobs wait until it is ready.'
              : 'It is not installed yet, so jobs that need it will fail. Install it now, or choose another installed model.'}</span>
            ${downloading ? '' : html`<div>${stateCell(m.kind, m.repo_id, 'available')}</div>`}
          </div>
        </div>`;
      })}`,
    );
  }

  function paintDownloads() {
    const list = data.downloads || [];
    slots.downloadsPanel.hidden = list.length === 0;
    patchList(
      slots.downloads,
      list,
      (d) => d.id,
      (d) => html`<li class="download-row" data-status="${d.status}">
        <div class="download-main">
          <span class="cell-title break">${d.repo_id}</span>
          <span class="cell-sub">${KIND_LABEL[d.kind] || d.kind}${d.status === 'failed' && d.error ? html`<span class="tone-danger">: ${d.error}</span>` : ''}</span>
        </div>
        <div class="download-progress">
          ${d.status === 'completed'
            ? html`<span class="badge" data-tone="ok">${icon('check')}Installed</span>`
            : d.status === 'failed'
              ? html`<span class="badge" data-tone="danger">${icon('x-circle')}Failed</span>`
              : html`${track(d.status === 'queued' ? null : d.progress, { label: `Download progress for ${d.repo_id}` })}
                <span class="small muted num">${d.status === 'queued' ? 'Waiting to start' : html`${d.progress !== null && d.progress !== undefined ? `${Math.round(d.progress)}%, ` : ''}${bytes(d.downloaded_bytes)}${d.total_bytes ? ` of ${bytes(d.total_bytes)}` : ''}`}</span>`}
        </div>
        <div class="job-actions">
          ${ACTIVE.has(d.status) ? '' : html`<button type="button" class="btn btn-ghost btn-sm" data-action="dismiss" data-id="${d.id}">Dismiss</button>`}
        </div>
      </li>`,
    );
  }

  /** Rows for one kind: the catalog plus any installed model that is not in it. */
  function modelRows(kind) {
    const installed = (data.installed || []).filter((m) => m.kind === kind);
    const byRepo = new Map(installed.map((m) => [m.repo_id, m]));
    const rows = (data.recommended || [])
      .filter((m) => m.kind === kind)
      .map((m, order) => ({ ...m, order, local: byRepo.get(m.repo_id) || null }));
    const catalog = new Set(rows.map((r) => r.repo_id));
    installed
      .filter((m) => !catalog.has(m.repo_id))
      .forEach((m, i) => rows.push({ kind, repo_id: m.repo_id, name: m.repo_id.split('/').pop(), size: m.size, description: 'Installed from Hugging Face.', order: 100 + i, local: m }));
    for (const r of rows) {
      r.state = r.local && r.local.in_use ? 'in-use' : downloadFor(kind, r.repo_id) ? 'downloading' : r.local ? 'installed' : 'available';
    }
    return rows.sort((a, b) => STATE_ORDER[a.state] - STATE_ORDER[b.state] || a.order - b.order);
  }

  function paintKind(kind) {
    const rows = modelRows(kind);
    const installedCount = rows.filter((r) => r.local).length;
    const countEl = kind === 'whisper' ? slots.whisperCount : slots.translationCount;
    countEl.textContent = installedCount ? `${number(installedCount)} installed` : 'None installed';
    render(
      slots[kind],
      html`<div class="table-wrap"><table class="table models-table">
        <thead><tr>
          <th scope="col" class="col-model">Model</th>
          <th scope="col" class="col-note">Good for</th>
          <th scope="col" class="col-size num">Size</th>
          <th scope="col" class="col-state col-actions">Status</th>
        </tr></thead>
        <tbody>${rows.map((r) => html`<tr data-state="${r.state}">
          <td class="col-model"><span class="model-name">${r.name}</span><code class="model-repo">${r.repo_id}</code></td>
          <td class="col-note"><span class="model-note">${r.description || ''}</span></td>
          <td class="col-size num nowrap">${bytes(r.local ? r.local.size : r.size)}</td>
          <td class="col-state col-actions">${stateCell(kind, r.repo_id, r.state)}</td>
        </tr>`)}</tbody>
      </table></div>`,
    );
  }

  function stateCell(kind, repoId, state) {
    if (state === 'downloading') {
      const d = downloadFor(kind, repoId);
      return html`<span class="model-state"><span class="model-progress">${track(d && d.status !== 'queued' ? d.progress : null, { label: `Download progress for ${repoId}`, thin: true })}<span class="small muted num">${d && d.progress !== null && d.progress !== undefined ? `Downloading, ${Math.round(d.progress)}%` : 'Starting download'}</span></span></span>`;
    }
    if (state === 'in-use') return html`<span class="model-state"><span class="badge" data-tone="signal">${icon('check')}In use</span></span>`;
    if (state === 'installed') {
      return html`<span class="model-state">
        <button type="button" class="btn btn-sm" data-action="select" data-kind="${kind}" data-repo="${repoId}">Use this model</button>
        <button type="button" class="btn btn-sm btn-ghost btn-icon btn-danger" data-action="delete" data-kind="${kind}" data-repo="${repoId}" aria-label="Delete ${repoId}" title="Delete">${icon('trash')}</button>
      </span>`;
    }
    return html`<span class="model-state"><button type="button" class="btn btn-sm" data-action="install" data-kind="${kind}" data-repo="${repoId}">${icon('download')}Install</button></span>`;
  }

  function paintResults() {
    const { kind, q, items, error } = results;
    if (error) {
      render(slots.results, emptyState({ iconName: 'alert', tone: 'error', compact: true, title: 'Search failed', text: error }));
      return;
    }
    const inst = installedSet();
    const looksLikeRepo = /^[\w.-]+\/[\w.-]+$/.test(q);
    const direct = looksLikeRepo && !items.some((r) => r.repo_id === q);
    const stateOf = (repo) => (inst.has(`${kind}:${repo}`) ? 'installed' : downloadFor(kind, repo) ? 'downloading' : 'available');
    const resultState = (repo) => {
      const st = stateOf(repo);
      if (st === 'installed') {
        const local = (data.installed || []).find((m) => m.kind === kind && m.repo_id === repo);
        return local && local.in_use ? 'in-use' : 'installed';
      }
      return st;
    };
    render(
      slots.results,
      html`${direct
        ? html`<div class="direct-install"><span>Install <code>${q}</code> directly as a ${KIND_LABEL[kind].toLowerCase()} model?</span>${stateCell(kind, q, resultState(q))}</div>`
        : ''}
      ${items.length
        ? html`<ul class="list result-list">${items.map(
            (r) => html`<li class="result-row">
              <div class="min0">
                <code class="break">${r.repo_id}</code>
                <span class="cell-sub num">${number(r.downloads || 0)} downloads, ${number(r.likes || 0)} likes${r.pipeline_tag ? `, ${r.pipeline_tag}` : ''}</span>
              </div>
              ${stateCell(kind, r.repo_id, resultState(r.repo_id))}
            </li>`,
          )}</ul>`
        : direct
          ? ''
          : emptyState({ iconName: 'search', compact: true, title: 'No models found', text: 'Try a shorter search, or paste an exact owner/name repository id.' })}`,
    );
  }

  const form = $('[data-search-form]', root);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const q = form.q.value.trim();
    const kind = form.kind.value;
    if (q.length < 2) {
      notify.error('Type at least two characters to search.');
      return;
    }
    const button = form.querySelector('[type=submit]');
    await busy(button, async () => {
      render(slots.results, skeletonRows(3));
      try {
        const items = await api.get(`/api/models/search${qs({ q, kind })}`);
        results = { kind, q, items: Array.isArray(items) ? items : [] };
      } catch (err) {
        results = { kind, q, items: [], error: err.message };
      }
      paintResults();
    });
  });

  const poller = createPoller(load, () => (data && data.downloads.some((d) => ACTIVE.has(d.status)) ? 2000 : null));

  async function deleteModel(kind, repo) {
    const [owner, ...rest] = repo.split('/');
    const path = [kind, owner, rest.join('/')].map(encodeURIComponent).join('/');
    try {
      await api.del(`/api/models/${path}`);
      notify.ok(`Deleted ${repo}.`);
      poller.trigger();
    } catch (err) {
      notify.error(err);
    }
  }

  /** After switching models, the one it replaced is no longer used: delete it or keep it. */
  async function offerDelete(kind, previous) {
    const size = (data && data.installed.find((m) => m.kind === kind && m.repo_id === previous)) || {};
    const ok = await confirmDialog({
      title: 'Delete the previous model?',
      message: `${previous} is no longer in use.${size.size ? ` Deleting it frees ${bytes(size.size)}.` : ''}`,
      detail: 'Keep it to switch back quickly later. You can also delete it from this page at any time.',
      confirmLabel: 'Delete model',
      cancelLabel: 'Keep it',
      danger: true,
    });
    if (ok) await deleteModel(kind, previous);
  }

  const off = delegate(root, 'click', {
    'retry-load': () => poller.trigger(),
    install: (el) =>
      busy(el, async () => {
        try {
          await api.post('/api/models/download', { kind: el.dataset.kind, repo_id: el.dataset.repo });
          notify.info(`Downloading ${el.dataset.repo}.`);
          poller.trigger();
        } catch (err) {
          notify.error(err);
        }
      }),
    select: async (el) => {
      const { kind, repo } = el.dataset;
      let res;
      await busy(el, async () => {
        try {
          res = await api.post('/api/models/select', { kind, repo_id: repo });
          notify.ok(`${repo} is now the ${KIND_LABEL[kind].toLowerCase()} model.`);
          poller.trigger();
        } catch (err) {
          notify.error(err);
        }
      });
      if (res && res.previous) await offerDelete(kind, res.previous);
    },
    delete: async (el) => {
      const { kind, repo } = el.dataset;
      const ok = await confirmDialog({
        title: 'Delete model?',
        message: `${repo} is removed from this server's model storage.`,
        detail: 'You can install it again later.',
        confirmLabel: 'Delete model',
        danger: true,
      });
      if (ok) await deleteModel(kind, repo);
    },
    dismiss: async (el) => {
      try {
        await api.del(`/api/models/downloads/${encodeURIComponent(el.dataset.id)}`);
        poller.trigger();
      } catch (err) {
        notify.error(err);
      }
    },
  });

  poller.start();
  return () => {
    poller.stop();
    off();
  };
}
