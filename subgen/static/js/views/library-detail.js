// Library media browser: search, filter by subtitle status, generate per file.
import { api, qs } from '../api.js';
import { html, render, delegate, debounce, busy, patchList, $ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { notify } from '../ui/toast.js';
import { createPoller } from '../ui/poller.js';
import { pageHeader, emptyState, errorState, skeletonRows, pager, subtitleChip, subtitleState } from '../ui/components.js';
import { bytes, clock, relative, isAudio, dirname, number } from '../ui/format.js';
import { openGenerateDialog } from './generate.js';
import { libraryDialog, scanSummary } from './libraries.js';

const LIMIT = 50;
const FILTERS = [
  ['', 'All'],
  ['missing', 'Missing'],
  ['complete', 'Complete'],
  ['failed', 'Failed'],
];

export async function mount(ctx) {
  const root = ctx.root;
  const libraryId = Number(ctx.params.id);
  const state = {
    q: ctx.query.get('q') || '',
    status: ctx.query.get('status') || '',
    offset: Math.max(0, Number(ctx.query.get('offset')) || 0),
  };
  let library = null;
  let page = { items: [], total: 0 };
  let loaded = false;
  let missing = false; // the library list loaded and this id is not in it

  render(root, html`<div data-slot="header">${pageHeader({ title: 'Library', crumb: { href: '#/libraries', label: 'Libraries' } })}</div>
    <section class="panel" aria-label="Media files">
      <div class="toolbar">
        <div class="input-group">
          ${icon('search')}
          <input class="input" type="search" data-q placeholder="Search file names" aria-label="Search file names" value="${state.q}">
        </div>
        <div class="segmented" role="group" aria-label="Subtitle status">
          ${FILTERS.map(([v, label]) => html`<button type="button" data-action="filter" data-value-filter="${v}" aria-pressed="${state.status === v ? 'true' : 'false'}">${label}</button>`)}
        </div>
        <span class="spacer"></span>
        <div data-slot="pager"></div>
      </div>
      <div data-slot="table">${skeletonRows(6)}</div>
    </section>`);

  const headerEl = $('[data-slot=header]', root);
  const tableEl = $('[data-slot=table]', root);
  const pagerEl = $('[data-slot=pager]', root);

  async function loadLibrary() {
    try {
      const libs = await api.get('/api/libraries');
      library = libs.find((l) => l.id === libraryId) || null;
      missing = !library;
    } catch (err) {
      if (err.status !== 401) notify.error(err);
    }
    paintHeader();
  }

  function paintHeader() {
    ctx.setTitle(library ? library.name : 'Library');
    if (missing) {
      render(headerEl, pageHeader({ title: 'Library not found', crumb: { href: '#/libraries', label: 'Libraries' } }));
      render(
        headerEl.nextElementSibling,
        emptyState({
          iconName: 'libraries',
          title: 'This library does not exist',
          text: 'It may have been removed. Pick another library from the list.',
          action: html`<a class="btn btn-primary" href="#/libraries">Go to libraries</a>`,
        }),
      );
      return;
    }
    render(
      headerEl,
      pageHeader({
        title: library ? library.name : 'Library',
        crumb: { href: '#/libraries', label: 'Libraries' },
        subtitle: library
          ? html`<span class="subtitle-meta"><code>${library.path}</code><span>${number(library.media_count || 0)} files, ${library.last_scan_at ? html`scanned ${relative(library.last_scan_at)}` : 'not scanned yet'}${library.enabled ? '' : ', paused'}</span></span>`
          : '',
        actions: library
          ? html`<button type="button" class="btn" data-action="edit-library">${icon('edit')}Edit</button>
              <button type="button" class="btn btn-primary" data-action="scan-library">${icon('scan')}Scan now</button>`
          : '',
      }),
    );
  }

  async function loadMedia() {
    try {
      page = await api.get(`/api/media${qs({ library_id: libraryId, q: state.q, status: state.status, limit: LIMIT, offset: state.offset })}`);
      loaded = true;
      paintTable();
    } catch (err) {
      if (err.status !== 401 && !loaded) render(tableEl, errorState(err));
    }
  }

  function paintTable() {
    render(pagerEl, page.total > LIMIT || state.offset ? pager({ total: page.total, limit: LIMIT, offset: state.offset }) : html``);
    if (!page.items.length) {
      const filtered = state.q || state.status;
      render(
        tableEl,
        filtered
          ? emptyState({ iconName: 'search', title: 'No matching files', text: 'Try a different search or status filter.', action: html`<button type="button" class="btn" data-action="clear-filters">Clear filters</button>` })
          : emptyState({
              iconName: 'film',
              title: 'No media found yet',
              text: 'Scan the library to index its video and audio files.',
              action: html`<button type="button" class="btn btn-primary" data-action="scan-library">${icon('scan')}Scan now</button>`,
            }),
      );
      return;
    }
    let body = tableEl.querySelector('tbody');
    if (!body) {
      render(
        tableEl,
        html`<div class="table-wrap"><table class="table media-table">
          <thead><tr>
            <th scope="col">File</th>
            <th scope="col" class="num">Length</th>
            <th scope="col">Subtitles</th>
            <th scope="col">Other subtitle files</th>
            <th scope="col" class="col-actions"><span class="sr-only">Actions</span></th>
          </tr></thead>
          <tbody></tbody>
        </table></div>`,
      );
      body = tableEl.querySelector('tbody');
    }
    // Patch rows in place so polling keeps focus and open <details> intact.
    patchList(body, page.items, (m) => m.id, mediaRow);
  }

  function syncQuery() {
    ctx.replaceQuery({ q: state.q, status: state.status, offset: state.offset || '' });
  }

  const hasActive = () => page.items.some((m) => (m.subtitles || []).some((s) => ['queued', 'processing'].includes(subtitleState(s))));
  const poller = createPoller(loadMedia, () => (hasActive() ? 3000 : 15000));

  const search = $('[data-q]', root);
  const onSearch = debounce(() => {
    state.q = search.value.trim();
    state.offset = 0;
    syncQuery();
    poller.trigger();
  }, 300);
  search.addEventListener('input', onSearch);

  const off = delegate(root, 'click', {
    'retry-load': () => poller.trigger(),
    filter: (el) => {
      state.status = el.dataset.valueFilter;
      state.offset = 0;
      root.querySelectorAll('[data-action=filter]').forEach((b) => b.setAttribute('aria-pressed', String(b === el)));
      syncQuery();
      poller.trigger();
    },
    'clear-filters': () => {
      state.q = '';
      state.status = '';
      state.offset = 0;
      search.value = '';
      root.querySelectorAll('[data-action=filter]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.valueFilter === '')));
      syncQuery();
      poller.trigger();
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
    generate: async (el) => {
      const res = await openGenerateDialog(Number(el.dataset.id), library ? { languages: library.languages, formats: library.formats } : {});
      if (res) poller.trigger();
    },
    'edit-library': async () => {
      if (library && (await libraryDialog(library))) {
        await loadLibrary();
        poller.trigger();
      }
    },
    'scan-library': (el) =>
      busy(el, async () => {
        try {
          const res = await api.post(`/api/libraries/${libraryId}/scan`);
          notify.ok(scanSummary(library ? library.name : 'Library', res));
          await loadLibrary();
          poller.trigger();
        } catch (err) {
          notify.error(err);
        }
      }),
  });

  await loadLibrary();
  if (!missing) poller.start();
  return () => {
    poller.stop();
    off();
    onSearch.cancel();
  };
}

function mediaRow(m) {
  const external = m.external || [];
  return html`<tr>
    <td>
      <div class="media-cell">
        <span class="file-icon small-icon">${icon(isAudio(m.name) ? 'music' : 'film')}</span>
        <div class="min0">
          <span class="cell-title break">${m.name}</span>
          <span class="cell-sub truncate" title="${m.path}">${dirname(m.path)}${m.size ? html`, ${bytes(m.size)}` : ''}</span>
        </div>
      </div>
    </td>
    <td class="num nowrap">${m.duration_ms ? clock(m.duration_ms) : '—'}</td>
    <td><div class="chips">${(m.subtitles || []).map(subtitleChip)}</div></td>
    <td>
      ${external.length
        ? html`<details class="external"><summary>${external.length} file${external.length === 1 ? '' : 's'}</summary><ul>${external.map((f) => html`<li class="break">${f}</li>`)}</ul></details>`
        : html`<span class="faint">None</span>`}
    </td>
    <td class="col-actions">
      <button type="button" class="btn btn-sm" data-action="generate" data-id="${m.id}" aria-label="Generate subtitles for ${m.name}">${icon('sparkle')}Generate</button>
    </td>
  </tr>`;
}
