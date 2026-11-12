// Libraries: list, add/edit (with folder picker), enable, scan and remove.
import { api, qs } from '../api.js';
import { html, render, delegate, busy, element, $ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { openDialog, confirmDialog } from '../ui/dialog.js';
import { notify } from '../ui/toast.js';
import { pageHeader, emptyState, errorState, skeletonRows } from '../ui/components.js';
import { createLangPicker } from '../ui/lang-picker.js';
import { number, relative, dateTime, plural } from '../ui/format.js';
import { getLanguages, languageName } from '../store.js';

const FORMATS = [
  ['srt', 'SRT'],
  ['ass', 'ASS'],
  ['vtt', 'WebVTT'],
];

export async function mount(ctx) {
  ctx.setTitle('Libraries');
  const root = ctx.root;
  render(
    root,
    html`${pageHeader({
      title: 'Libraries',
      subtitle: 'Folders SubGen watches. Subtitles are written next to each media file.',
      actions: html`
        <button type="button" class="btn" data-action="scan-all">${icon('scan')}Scan all</button>
        <button type="button" class="btn btn-primary" data-action="add">${icon('plus')}Add library</button>`,
    })}
    <div class="library-grid" data-slot="list">${skeletonRows(2, 'block')}</div>`,
  );
  const listEl = $('[data-slot=list]', root);
  let libraries = [];
  let languages = [];

  async function load() {
    try {
      [libraries, languages] = await Promise.all([api.get('/api/libraries'), getLanguages().catch(() => [])]);
      paint();
    } catch (err) {
      if (err.status !== 401) render(listEl, errorState(err));
    }
  }

  function paint() {
    if (!libraries.length) {
      render(
        listEl,
        html`<div class="panel">${emptyState({
          iconName: 'libraries',
          title: 'No libraries yet',
          text: 'Add a folder from your media mount, choose the subtitle languages you want, and SubGen will queue every file that is missing them.',
          action: html`<button type="button" class="btn btn-primary" data-action="add">${icon('plus')}Add library</button>`,
        })}</div>`,
      );
      return;
    }
    render(listEl, html`${libraries.map((lib) => libraryCard(lib, languages))}`);
  }

  const find = (el) => libraries.find((l) => l.id === Number(el.dataset.id));

  const off = delegate(root, 'click', {
    'retry-load': () => load(),
    add: async () => {
      if (await libraryDialog(null)) load();
    },
    edit: async (el) => {
      const lib = find(el);
      if (lib && (await libraryDialog(lib))) load();
    },
    scan: (el) =>
      busy(el, async () => {
        const lib = find(el);
        try {
          const res = await api.post(`/api/libraries/${encodeURIComponent(lib.id)}/scan`);
          notify.ok(scanSummary(lib.name, res));
          load();
        } catch (err) {
          notify.error(err);
        }
      }),
    'scan-all': (el) =>
      busy(el, async () => {
        try {
          const res = await api.post('/api/libraries/scan');
          notify.ok(scanSummary('All libraries', res));
          reportScanErrors(res);
          load();
        } catch (err) {
          notify.error(err);
        }
      }),
    remove: async (el) => {
      const lib = find(el);
      if (!lib) return;
      const ok = await confirmDialog({
        title: `Remove “${lib.name}”?`,
        message: 'SubGen stops watching this folder and forgets its media and job history.',
        detail: 'Media files and subtitles already on disk are not touched.',
        confirmLabel: 'Remove library',
        danger: true,
      });
      if (!ok) return;
      try {
        await api.del(`/api/libraries/${encodeURIComponent(lib.id)}`);
        notify.ok(`Removed “${lib.name}”.`);
        load();
      } catch (err) {
        notify.error(err);
      }
    },
  });
  const offChange = delegate(root, 'change', {
    toggle: async (el) => {
      const lib = find(el);
      if (!lib) return;
      el.disabled = true;
      try {
        await api.put(`/api/libraries/${encodeURIComponent(lib.id)}`, {
          name: lib.name,
          path: lib.path,
          languages: lib.languages,
          formats: lib.formats,
          enabled: el.checked,
        });
        lib.enabled = el.checked;
        notify.ok(`${lib.name} ${el.checked ? 'enabled' : 'paused'}.`);
        load();
      } catch (err) {
        el.checked = !el.checked;
        notify.error(err);
      } finally {
        el.disabled = false;
      }
    },
  });

  load();
  return () => {
    off();
    offChange();
  };
}

export function scanSummary(name, res = {}) {
  const parts = [`${plural(res.found || 0, 'file')} found`, `${plural(res.queued || 0, 'job')} queued`];
  if (res.skipped) parts.push(`${number(res.skipped)} skipped`);
  if (res.pruned) parts.push(`${number(res.pruned)} removed`);
  return `${name}: ${parts.join(', ')}.`;
}

/** Libraries that could not be scanned (unmounted or unreadable folders). */
export function reportScanErrors(res = {}) {
  if (res.errors && res.errors.length) notify.warn(`Some libraries could not be scanned: ${res.errors.join('; ')}`);
}

function libraryCard(lib, languages) {
  const j = lib.jobs || {};
  return html`<article class="panel library-card" data-enabled="${lib.enabled ? 'true' : 'false'}">
    <div class="library-head">
      <span class="file-icon">${icon('folder')}</span>
      <div class="library-title">
        <h2><a href="#/libraries/${lib.id}">${lib.name}</a></h2>
        <code class="library-path break">${lib.path}</code>
      </div>
      <label class="switch" title="${lib.enabled ? 'Watching this folder' : 'Paused'}">
        <input type="checkbox" role="switch" data-action="toggle" data-id="${lib.id}" ${lib.enabled ? 'checked' : ''} aria-label="Watch ${lib.name}">
        <span class="small muted">${lib.enabled ? 'On' : 'Paused'}</span>
      </label>
    </div>
    <div class="library-langs">
      <div class="chips" aria-label="Target languages">
        ${(lib.languages || []).map((code) => html`<span class="chip" title="${languageName(languages, code)}">${String(code).toUpperCase()}</span>`)}
      </div>
      <span class="muted small">${(lib.formats || []).map((f) => f.toUpperCase()).join(', ')}</span>
    </div>
    <dl class="library-stats">
      <div><dt>Media</dt><dd class="num">${number(lib.media_count || 0)}</dd></div>
      <div><dt>Queued</dt><dd class="num">${number((j.queued || 0) + (j.processing || 0))}</dd></div>
      <div><dt>Done</dt><dd class="num">${number(j.completed || 0)}</dd></div>
      <div><dt>Failed</dt><dd class="num ${j.failed ? 'tone-danger' : ''}">${number(j.failed || 0)}</dd></div>
    </dl>
    <div class="library-foot">
      <span class="muted small">${lib.last_scan_at ? html`Scanned <time datetime="${lib.last_scan_at}" title="${dateTime(lib.last_scan_at)}">${relative(lib.last_scan_at)}</time>` : 'Not scanned yet'}</span>
      <span class="spacer"></span>
      <a class="btn btn-sm" href="#/libraries/${lib.id}">${icon('film')}Browse</a>
      <button type="button" class="btn btn-sm" data-action="scan" data-id="${lib.id}">${icon('scan')}Scan</button>
      <button type="button" class="btn btn-sm btn-ghost btn-icon" data-action="edit" data-id="${lib.id}" aria-label="Edit ${lib.name}" title="Edit">${icon('edit')}</button>
      <button type="button" class="btn btn-sm btn-ghost btn-icon btn-danger" data-action="remove" data-id="${lib.id}" aria-label="Remove ${lib.name}" title="Remove">${icon('trash')}</button>
    </div>
  </article>`;
}

/** Add / edit dialog. Resolves true when saved. */
export async function libraryDialog(lib) {
  let languages = [];
  try {
    languages = await getLanguages();
  } catch (err) {
    notify.error(err);
  }
  const editing = Boolean(lib);
  const formId = `lib-form-${Date.now()}`;
  const formats = new Set(lib ? lib.formats : ['srt']);
  const form = element(html`<form class="stack" id="${formId}" novalidate>
    <div class="form-grid">
      <div class="field span-2">
        <label for="${formId}-name">Name</label>
        <input class="input" id="${formId}-name" name="name" required maxlength="100" placeholder="Movies" value="${lib ? lib.name : ''}" autocomplete="off">
      </div>
      <div class="field span-2">
        <label for="${formId}-path">Folder</label>
        <div class="row nowrap-row">
          <input class="input mono" id="${formId}-path" name="path" required placeholder="/media/movies" value="${lib ? lib.path : ''}" autocomplete="off" spellcheck="false">
          <button type="button" class="btn" data-browse aria-expanded="false" aria-controls="${formId}-browser">${icon('folder')}Browse</button>
        </div>
        <div class="browser" id="${formId}-browser" hidden></div>
        <span class="field-help">A folder inside the media mount. Subtitles are written beside each file, so it must be writable.</span>
      </div>
    </div>
    <div class="lang-slot"></div>
    <fieldset class="fieldset">
      <legend>Subtitle formats</legend>
      <div class="check-row">
        ${FORMATS.map(([v, label]) => html`<label class="check"><input type="checkbox" name="formats" value="${v}" ${formats.has(v) ? 'checked' : ''}>${label}</label>`)}
      </div>
    </fieldset>
    <label class="switch"><input type="checkbox" role="switch" name="enabled" ${!lib || lib.enabled ? 'checked' : ''}>Watch this folder and queue new files automatically</label>
  </form>`);
  const picker = createLangPicker({
    languages,
    selected: lib ? lib.languages : [],
    label: 'Subtitle languages',
    help: 'A subtitle is created for each language and format.',
  });
  form.querySelector('.lang-slot').append(picker.el);

  const dlg = openDialog({
    title: editing ? `Edit ${lib.name}` : 'Add library',
    size: 'wide',
    body: form,
    footer: html`<button type="button" class="btn" data-dialog-close>Cancel</button>
      <button type="submit" class="btn btn-primary" form="${formId}">${editing ? 'Save library' : 'Add library'}</button>`,
  });

  const pathInput = form.querySelector('[name=path]');
  const browserEl = form.querySelector('.browser');
  const browseBtn = form.querySelector('[data-browse]');
  browseBtn.addEventListener('click', () => {
    const open = browserEl.hidden;
    browserEl.hidden = !open;
    browseBtn.setAttribute('aria-expanded', String(open));
    if (open) browse(pathInput.value.trim() || '');
  });

  async function browse(path) {
    render(browserEl, html`<div class="browser-path"><span class="skeleton"></span></div><ul class="browser-list"><li class="note">Loading folders…</li></ul>`);
    let data;
    try {
      data = await api.get(`/api/browse${qs({ path })}`);
    } catch (err) {
      if (path) return browse('');
      render(browserEl, html`<ul class="browser-list"><li class="note">${err.message}</li></ul>`);
      return;
    }
    render(
      browserEl,
      html`<div class="browser-path">
          <button type="button" class="btn btn-sm btn-icon" data-up ${data.parent ? '' : 'disabled'} aria-label="Parent folder" title="Parent folder">${icon('corner-up')}</button>
          <code>${data.path}</code>
          <button type="button" class="btn btn-sm btn-primary" data-use>Use this folder</button>
        </div>
        <ul class="browser-list" aria-label="Sub-folders of ${data.path}">
          ${data.dirs && data.dirs.length
            ? data.dirs.map((d) => html`<li><button type="button" data-dir="${d.path}">${icon('folder')}<span class="truncate">${d.name}</span>${icon('chevron-right')}</button></li>`)
            : html`<li class="note">No sub-folders here.${data.path === data.root ? ' Mount media folders into the container first.' : ''}</li>`}
        </ul>`,
    );
    browserEl.querySelector('[data-up]').addEventListener('click', () => data.parent && browse(data.parent));
    browserEl.querySelector('[data-use]').addEventListener('click', () => {
      pathInput.value = data.path;
      const nameInput = form.querySelector('[name=name]');
      if (!nameInput.value.trim() && data.path !== data.root) {
        const last = data.path.split('/').filter(Boolean).pop() || '';
        nameInput.value = last.charAt(0).toUpperCase() + last.slice(1);
      }
      browserEl.hidden = true;
      browseBtn.setAttribute('aria-expanded', 'false');
      pathInput.focus();
    });
    browserEl.querySelectorAll('[data-dir]').forEach((b) => b.addEventListener('click', () => browse(b.dataset.dir)));
    const firstDir = browserEl.querySelector('[data-dir]');
    if (firstDir) firstDir.focus();
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const body = {
      name: form.querySelector('[name=name]').value.trim(),
      path: pathInput.value.trim(),
      languages: picker.value,
      formats: Array.from(form.querySelectorAll('[name=formats]:checked')).map((b) => b.value),
      enabled: form.querySelector('[name=enabled]').checked,
    };
    const problem = !body.name
      ? 'Give the library a name.'
      : !body.path
        ? 'Choose the folder to watch.'
        : !body.languages.length
          ? 'Choose at least one subtitle language.'
          : !body.formats.length
            ? 'Choose at least one subtitle format.'
            : '';
    if (problem) {
      notify.error(problem);
      return;
    }
    const button = dlg.foot.querySelector('[type=submit]');
    button.disabled = true;
    try {
      if (editing) await api.put(`/api/libraries/${encodeURIComponent(lib.id)}`, body);
      else await api.post('/api/libraries', body);
      notify.ok(editing ? `Saved “${body.name}”.` : `Added “${body.name}”. Run a scan to find its media.`);
      dlg.close(true);
    } catch (err) {
      notify.error(err);
      button.disabled = false;
    }
  });
  return (await dlg.closed) === true;
}
