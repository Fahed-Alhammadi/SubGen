// Models: recommended catalog, installed models, Hugging Face search and downloads.
import { api, qs } from '../api.js';
import { html, render, delegate, busy, patchList, $ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { createPoller } from '../ui/poller.js';
import { notify } from '../ui/toast.js';
import { confirmDialog } from '../ui/dialog.js';
import { pageHeader, emptyState, errorState, skeletonRows, track } from '../ui/components.js';
import { bytes, number } from '../ui/format.js';

const KIND_LABEL = { whisper: 'Transcription', translation: 'Translation' };
const ACTIVE = new Set(['queued', 'downloading']);

export async function mount(ctx) {
  ctx.setTitle('Models');
  const root = ctx.root;
  render(
    root,
    html`${pageHeader({
      title: 'Models',
      subtitle: 'Speech recognition and translation models stored on this server. The models in use are installed automatically when they are missing; jobs never download anything themselves.',
    })}
    <div class="stack models">
      <div data-slot="missing"></div>
      <section class="panel" aria-labelledby="dl-h" data-slot="downloads-panel" hidden>
        <div class="panel-head"><h2 id="dl-h">Downloads</h2></div>
        <ul class="download-list" data-slot="downloads"></ul>
      </section>
      <section aria-labelledby="rec-h" class="stack-sm">
        <h2 id="rec-h" class="section-title">Recommended</h2>
        <div data-slot="recommended">${skeletonRows(2, 'block')}</div>
      </section>
      <section class="panel" aria-labelledby="inst-h">
        <div class="panel-head"><h2 id="inst-h">Installed</h2><span class="spacer"></span><span class="meta" data-slot="installed-total"></span></div>
        <div data-slot="installed">${skeletonRows(3)}</div>
      </section>
      <section class="panel" aria-labelledby="hf-h">
        <div class="panel-head"><h2 id="hf-h">Search Hugging Face</h2><span class="spacer"></span><span class="meta">Needs internet access on the server</span></div>
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
      </section>
    </div>`,
  );

  const slots = {
    missing: $('[data-slot=missing]', root),
    downloadsPanel: $('[data-slot=downloads-panel]', root),
    downloads: $('[data-slot=downloads]', root),
    recommended: $('[data-slot=recommended]', root),
    installed: $('[data-slot=installed]', root),
    installedTotal: $('[data-slot=installed-total]', root),
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
      if (!data) render(slots.installed, errorState(err));
    }
  }

  const installedSet = () => new Set((data ? data.installed : []).map((m) => `${m.kind}:${m.repo_id}`));
  const inUseSet = () => new Set((data ? data.installed : []).filter((m) => m.in_use).map((m) => `${m.kind}:${m.repo_id}`));
  const downloadingSet = () => new Set((data ? data.downloads : []).filter((d) => ACTIVE.has(d.status)).map((d) => `${d.kind}:${d.repo_id}`));

  function paint() {
    paintMissing();
    paintDownloads();
    paintRecommended();
    paintInstalled();
    if (results) paintResults();
  }

  /** Selected models that are not on disk yet (normally because they are being installed). */
  function paintMissing() {
    const dl = downloadingSet();
    const missing = (data.selected || []).filter((m) => !m.installed);
    render(
      slots.missing,
      html`${missing.map((m) => {
        const downloading = dl.has(`${m.kind}:${m.repo_id}`);
        return html`<div class="callout missing-model" data-tone="${downloading ? 'info' : 'danger'}" role="status">
          ${icon(downloading ? 'loader' : 'alert', { cls: downloading ? 'spin' : '' })}
          <div>
            <strong>${KIND_LABEL[m.kind] || m.kind} model in use: <code class="break">${m.repo_id}</code></strong>
            <span class="small">${downloading
              ? 'It is being installed now. Jobs wait until it is ready.'
              : 'It is not installed yet, so jobs that need it will fail. Install it now, or choose another installed model.'}</span>
            ${downloading ? '' : html`<div>${modelButtons(m.kind, m.repo_id, false, false, false)}</div>`}
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

  function modelButtons(kind, repoId, installed, inUse, downloading) {
    if (downloading) return html`<span class="badge" data-tone="info">${icon('loader', { cls: 'spin' })}Downloading</span>`;
    if (!installed) return html`<button type="button" class="btn btn-sm btn-primary" data-action="install" data-kind="${kind}" data-repo="${repoId}">${icon('download')}Install</button>`;
    if (inUse) return html`<span class="badge" data-tone="signal">${icon('check')}In use</span>`;
    return html`<span class="badge" data-tone="ok">${icon('check')}Installed</span>
      <button type="button" class="btn btn-sm" data-action="select" data-kind="${kind}" data-repo="${repoId}">Use this model</button>`;
  }

  function paintRecommended() {
    const rec = data.recommended || [];
    if (!rec.length) {
      render(slots.recommended, html`<p class="muted">No recommendations available.</p>`);
      return;
    }
    const inst = installedSet();
    const used = inUseSet();
    const dl = downloadingSet();
    const groups = ['whisper', 'translation'].map((kind) => [kind, rec.filter((m) => m.kind === kind)]).filter(([, items]) => items.length);
    render(
      slots.recommended,
      html`${groups.map(
        ([kind, items]) => html`<div class="rec-group">
          <h3 class="rec-kind">${KIND_LABEL[kind] || kind}</h3>
          <div class="rec-grid">
            ${items.map((m) => {
              const key = `${m.kind}:${m.repo_id}`;
              const installed = m.installed || inst.has(key);
              return html`<article class="panel rec-card" data-installed="${installed ? 'true' : 'false'}">
                <div class="rec-head">
                  <h4>${m.name}</h4>
                  <span class="muted small num">${m.size ? bytes(m.size) : ''}</span>
                </div>
                <code class="small muted break">${m.repo_id}</code>
                <p class="small">${m.description || ''}</p>
                ${m.tags && m.tags.length ? html`<div class="chips">${m.tags.map((t) => html`<span class="chip">${t}</span>`)}</div>` : ''}
                <div class="rec-foot">${modelButtons(m.kind, m.repo_id, installed, used.has(key), dl.has(key))}</div>
              </article>`;
            })}
          </div>
        </div>`,
      )}`,
    );
  }

  function paintInstalled() {
    const list = data.installed || [];
    const total = list.reduce((sum, m) => sum + (Number(m.size) || 0), 0);
    slots.installedTotal.textContent = list.length ? `${list.length} model${list.length === 1 ? '' : 's'}, ${bytes(total)}` : '';
    if (!list.length) {
      render(
        slots.installed,
        emptyState({
          iconName: 'models',
          title: 'No models installed',
          text: 'Install a transcription model from the recommendations above. Subtitle jobs fail until one is installed.',
        }),
      );
      return;
    }
    render(
      slots.installed,
      html`<div class="table-wrap"><table class="table">
        <thead><tr><th scope="col">Model</th><th scope="col">Type</th><th scope="col" class="num">Size</th><th scope="col">Status</th><th scope="col" class="col-actions"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>${list.map(
          (m) => html`<tr>
            <td><code class="break">${m.repo_id}</code></td>
            <td>${KIND_LABEL[m.kind] || m.kind}</td>
            <td class="num">${bytes(m.size)}</td>
            <td>${m.in_use ? html`<span class="badge" data-tone="signal">${icon('check')}In use</span>` : html`<span class="muted small">Installed</span>`}</td>
            <td class="col-actions"><div class="btn-group">
              ${m.in_use ? '' : html`<button type="button" class="btn btn-sm" data-action="select" data-kind="${m.kind}" data-repo="${m.repo_id}">Use</button>`}
              <button type="button" class="btn btn-sm btn-ghost btn-danger" data-action="delete" data-kind="${m.kind}" data-repo="${m.repo_id}" ${m.in_use ? html`disabled title="Choose another model before deleting this one"` : ''} aria-label="Delete ${m.repo_id}">${icon('trash')}Delete</button>
            </div></td>
          </tr>`,
        )}</tbody>
      </table></div>`,
    );
  }

  function paintResults() {
    const { kind, q, items, error } = results;
    if (error) {
      render(slots.results, emptyState({ iconName: 'alert', tone: 'error', compact: true, title: 'Search failed', text: error }));
      return;
    }
    const inst = installedSet();
    const dl = downloadingSet();
    const looksLikeRepo = /^[\w.-]+\/[\w.-]+$/.test(q);
    const direct = looksLikeRepo && !items.some((r) => r.repo_id === q);
    render(
      slots.results,
      html`${direct
        ? html`<div class="direct-install"><span>Install <code>${q}</code> directly as a ${KIND_LABEL[kind].toLowerCase()} model?</span>${modelButtons(kind, q, inst.has(`${kind}:${q}`), false, dl.has(`${kind}:${q}`))}</div>`
        : ''}
      ${items.length
        ? html`<ul class="result-list">${items.map(
            (r) => html`<li class="result-row">
              <div class="min0">
                <code class="break">${r.repo_id}</code>
                <span class="cell-sub num">${number(r.downloads || 0)} downloads, ${number(r.likes || 0)} likes${r.pipeline_tag ? `, ${r.pipeline_tag}` : ''}</span>
              </div>
              <div class="btn-group">${modelButtons(kind, r.repo_id, inst.has(`${kind}:${r.repo_id}`), false, dl.has(`${kind}:${r.repo_id}`))}</div>
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
      detail: 'Keep it to switch back quickly later. You can also delete it from the Installed list at any time.',
      confirmLabel: 'Delete model',
      cancelLabel: 'Keep it',
      danger: true,
    });
    if (ok) await deleteModel(kind, previous);
  }

  const poller = createPoller(load, () => (data && data.downloads.some((d) => ACTIVE.has(d.status)) ? 2000 : null));

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
