// Full-page subtitle editor with media preview, timing tools and QC warnings.
//
// Performance: cue rows are built once (one innerHTML pass) and then updated in
// place. Rows use `content-visibility: auto` so the browser skips layout and
// paint for off-screen rows, which keeps ~2000 cues responsive. Structural
// operations (insert/delete/split/merge) rebuild the list in one pass.
import { api } from '../api.js';
import { html, render, escapeHtml, $ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { notify } from '../ui/toast.js';
import { confirmDialog } from '../ui/dialog.js';
import { pageHeader, errorState, skeletonRows } from '../ui/components.js';
import { timecode, parseTimecode, isAudio, plural } from '../ui/format.js';
import { downloadUrl } from './job-actions.js';

const MIN_DURATION = 700;
const MAX_CPS = 21;
const MAX_LINE = 42;
const MAX_LINES = 2;
const MAX_TEXT = 4000;
const MAX_CUES = 20000;
const MAX_MS = 100 * 3600 * 1000;

let uidSeq = 0;
const uid = () => {
  uidSeq += 1;
  return uidSeq;
};

export async function mount(ctx) {
  const root = ctx.root;
  const jobId = Number(ctx.params.jobId);
  ctx.setTitle('Subtitle editor');
  render(root, html`${pageHeader({ title: 'Subtitle editor', crumb: { href: '/queue', label: 'Queue' } })}${skeletonRows(8)}`);

  let doc;
  try {
    doc = await api.get(`/api/subtitles/${encodeURIComponent(jobId)}`);
  } catch (err) {
    if (err.status === 401) return undefined;
    render(
      root,
      html`${pageHeader({ title: 'Subtitle editor', crumb: { href: '/queue', label: 'Queue' } })}
      <div class="panel">${errorState(err)}</div>`,
    );
    root.querySelector('[data-action=retry-load]').addEventListener('click', () => ctx.reload());
    return undefined;
  }
  if (!ctx.isCurrent()) return undefined;
  return createEditor(ctx, doc, jobId);
}

function createEditor(ctx, doc, jobId) {
  const root = ctx.root;
  ctx.setTitle(`Edit ${doc.name}`);
  /** @type {{id:number,start_ms:number,end_ms:number,text:string}[]} */
  let cues = (doc.cues || []).map((c) => ({ id: uid(), start_ms: c.start_ms, end_ms: c.end_ms, text: c.text || '' }));
  sortCues();
  let dirty = false;
  let saving = false;
  const selected = new Set();
  let editSeq = 0;
  const invalid = new Set(); // "id:start" / "id:end" with unparsable timecodes
  const caret = new Map(); // id -> last caret position in its textarea
  let activeId = null;
  let follow = true;
  let lastFocusedId = null;

  const audioOnly = isAudio(doc.name) || isAudio(doc.path);
  const mediaSrc = `/api/media/${encodeURIComponent(doc.media_id)}/stream`;

  render(
    root,
    html`
      ${pageHeader({
        title: doc.name,
        crumb: { href: '/queue', label: 'Queue' },
        subtitle: html`${String(doc.language || '').toUpperCase()} ${String(doc.format || '').toUpperCase()} subtitle, <span data-cue-count class="num"></span> <span class="dirty-flag" data-dirty hidden>${icon('edit')}Unsaved changes</span>`,
        actions: html`
          <a class="btn" href="${downloadUrl(jobId)}" download>${icon('download')}Download</a>
          <button type="button" class="btn btn-primary" data-act="save" aria-keyshortcuts="Control+S Meta+S">${icon('save')}Save<kbd class="kbd-hint" aria-hidden="true">⌘S</kbd></button>`,
      })}
      <div class="editor ${doc.playable ? '' : 'no-media'}">
        <aside class="editor-side" aria-label="Preview and tools">
          ${doc.playable
            ? html`<div class="player ${audioOnly ? 'audio' : ''}">
                ${audioOnly
                  ? html`<audio controls preload="metadata" src="${mediaSrc}" data-media></audio>`
                  : html`<video controls preload="metadata" playsinline src="${mediaSrc}" data-media></video>`}
                <div class="caption-overlay" data-caption aria-live="off"></div>
              </div>
              <div class="player-bar">
                <span class="num playhead" data-playhead>00:00:00,000</span>
                <span class="spacer"></span>
                <label class="check small"><input type="checkbox" data-follow checked>Follow playback</label>
              </div>`
            : html`<div class="no-preview">
                ${icon('eye')}
                <p><strong>No preview for this file.</strong> The browser cannot play this container format. Timing tools still work.</p>
              </div>`}

          <details class="tool" open>
            <summary><h2 id="tool-shift-h">Shift timing</h2></summary>
            <div class="row nowrap-row">
              <label class="sr-only" for="shift-ms">Shift by milliseconds</label>
              <input class="input num" id="shift-ms" type="number" step="100" value="500" data-shift>
              <span class="muted small">ms</span>
            </div>
            <div class="btn-group">
              <button type="button" class="btn btn-sm" data-act="shift" data-dir="-1">${icon('chevron-left')}Earlier</button>
              <button type="button" class="btn btn-sm" data-act="shift" data-dir="1">Later${icon('chevron-right')}</button>
            </div>
            <p class="small muted" data-shift-scope>Applies to all cues.</p>
          </details>

          <details class="tool" open>
            <summary><h2 id="tool-find-h">Find and replace</h2></summary>
            <input class="input" type="search" placeholder="Find" aria-label="Find text" data-find>
            <input class="input" type="text" placeholder="Replace with" aria-label="Replace with" data-replace>
            <label class="check small"><input type="checkbox" data-case>Match case</label>
            <div class="btn-group">
              <button type="button" class="btn btn-sm" data-act="find-next">${icon('search')}Find next</button>
              <button type="button" class="btn btn-sm" data-act="replace-all">${icon('replace')}Replace all</button>
            </div>
            <p class="small muted num" data-find-count aria-live="polite"></p>
          </details>

          <details class="tool" open>
            <summary><h2 id="tool-qc-h">Checks</h2></summary>
            <p class="small" data-qc-summary aria-live="polite"></p>
            <button type="button" class="btn btn-sm" data-act="next-issue">${icon('alert')}Next issue</button>
            <p class="small muted">Flags overlaps, cues under 0.7 s, reading speed over ${MAX_CPS} characters per second, lines over ${MAX_LINE} characters and more than ${MAX_LINES} lines.</p>
          </details>
        </aside>

        <section class="editor-main panel" aria-label="Cues">
          <div class="cue-toolbar">
            <label class="check small"><input type="checkbox" data-select-all>Select all</label>
            <span class="small muted num" data-sel-count></span>
            <span class="spacer"></span>
            <button type="button" class="btn btn-sm" data-act="add-start">${icon('plus')}Add cue</button>
            <button type="button" class="btn btn-sm btn-danger" data-act="delete-selected" disabled>${icon('trash')}Delete selected</button>
          </div>
          <ol class="cue-list" data-cues aria-label="Subtitle cues"></ol>
        </section>
      </div>`,
  );

  const listEl = $('[data-cues]', root);
  const media = $('[data-media]', root);
  const captionEl = $('[data-caption]', root);
  const playheadEl = $('[data-playhead]', root);
  const countEl = $('[data-cue-count]', root);
  const dirtyEl = $('[data-dirty]', root);
  const qcEl = $('[data-qc-summary]', root);
  const selCountEl = $('[data-sel-count]', root);
  const deleteSelBtn = $('[data-act=delete-selected]', root);
  const shiftScopeEl = $('[data-shift-scope]', root);
  const findInput = $('[data-find]', root);
  const replaceInput = $('[data-replace]', root);
  const caseInput = $('[data-case]', root);
  const findCountEl = $('[data-find-count]', root);
  // Tools start collapsed on phones so the cue list is reachable.
  if (window.matchMedia('(max-width: 700px)').matches) root.querySelectorAll('details.tool').forEach((d) => d.removeAttribute('open'));
  const kbd = $('.kbd-hint', root);
  if (kbd && !/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) kbd.textContent = 'Ctrl+S';

  /* ---------- model helpers ---------- */

  function sortCues() {
    cues.sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms);
  }
  const indexOf = (id) => cues.findIndex((c) => c.id === id);
  const rowOf = (id) => listEl.querySelector(`li[data-id="${id}"]`);

  function setDirty(value = true) {
    if (value) editSeq += 1;
    dirty = value;
    dirtyEl.hidden = !dirty;
  }

  function warningsFor(i) {
    const c = cues[i];
    const out = [];
    const next = cues[i + 1];
    const dur = c.end_ms - c.start_ms;
    if (dur <= 0) out.push(['error', 'End is before start']);
    if (next && c.end_ms > next.start_ms) out.push(['warn', `Overlaps next by ${c.end_ms - next.start_ms} ms`]);
    if (dur > 0 && dur < MIN_DURATION) out.push(['warn', `Short: ${dur} ms`]);
    const chars = c.text.replace(/\n/g, '').length;
    if (dur > 0 && chars) {
      const cps = chars / (dur / 1000);
      if (cps > MAX_CPS) out.push(['warn', `${cps.toFixed(1)} chars/s`]);
    }
    const lines = c.text.split('\n');
    const longest = Math.max(0, ...lines.map((l) => l.length));
    if (longest > MAX_LINE) out.push(['warn', `Line of ${longest} chars`]);
    if (lines.length > MAX_LINES) out.push(['warn', `${lines.length} lines`]);
    if (!c.text.trim()) out.push(['warn', 'Empty text']);
    if (c.text.length > MAX_TEXT) out.push(['error', `Over ${MAX_TEXT} chars`]);
    return out;
  }

  /* ---------- rendering ---------- */

  function textRows(text) {
    return Math.min(6, Math.max(2, text.split('\n').length));
  }

  function warningsMarkup(warnings) {
    return warnings
      .map(([tone, text]) => `<span class="badge" data-tone="${tone === 'error' ? 'danger' : 'warn'}">${icon('alert').value}${escapeHtml(text)}</span>`)
      .join('');
  }

  function rowMarkup(c, i) {
    const id = c.id;
    const warnings = warningsFor(i);
    return `<li class="cue${warnings.length ? ' has-warning' : ''}" data-id="${id}">
      <div class="cue-gutter">
        <input type="checkbox" class="cue-check" data-role="select" aria-label="Select cue ${i + 1}"${selected.has(id) ? ' checked' : ''}>
        <button type="button" class="cue-index num" data-act="seek" title="Play from this cue" aria-label="Play from cue ${i + 1}">${i + 1}</button>
      </div>
      <div class="cue-times">
        <div class="tc"><button type="button" class="btn btn-ghost btn-icon btn-sm" data-act="set-start" title="Set start to playhead" aria-label="Set start of cue ${i + 1} to playhead">${icon('to-start').value}</button>
          <input class="input tc-input mono" data-role="start" value="${timecode(c.start_ms)}" aria-label="Start of cue ${i + 1}" spellcheck="false" autocomplete="off"></div>
        <div class="tc"><button type="button" class="btn btn-ghost btn-icon btn-sm" data-act="set-end" title="Set end to playhead" aria-label="Set end of cue ${i + 1} to playhead">${icon('to-end').value}</button>
          <input class="input tc-input mono" data-role="end" value="${timecode(c.end_ms)}" aria-label="End of cue ${i + 1}" spellcheck="false" autocomplete="off"></div>
        <span class="cue-dur small muted num" data-role="dur">${((c.end_ms - c.start_ms) / 1000).toFixed(2)} s</span>
      </div>
      <div class="cue-body">
        <textarea class="textarea cue-text" data-role="text" dir="auto" rows="${textRows(c.text)}" aria-label="Text of cue ${i + 1}" maxlength="${MAX_TEXT}">${escapeHtml(c.text)}</textarea>
        <div class="cue-warn" data-role="warn">${warningsMarkup(warnings)}</div>
      </div>
      <div class="cue-actions">
        <button type="button" class="btn btn-ghost btn-icon btn-sm" data-act="insert" title="Insert cue after" aria-label="Insert cue after cue ${i + 1}">${icon('insert-below').value}</button>
        <button type="button" class="btn btn-ghost btn-icon btn-sm" data-act="split" title="Split at cursor or playhead" aria-label="Split cue ${i + 1}">${icon('scissors').value}</button>
        <button type="button" class="btn btn-ghost btn-icon btn-sm" data-act="merge" title="Merge with next" aria-label="Merge cue ${i + 1} with next"${i === cues.length - 1 ? ' disabled' : ''}>${icon('merge').value}</button>
        <button type="button" class="btn btn-ghost btn-icon btn-sm btn-danger" data-act="delete" title="Delete cue" aria-label="Delete cue ${i + 1}">${icon('trash').value}</button>
      </div>
    </li>`;
  }

  function renderAll({ keepScroll = true } = {}) {
    invalid.clear(); // rebuilt rows show the last valid timecodes
    const scrollY = window.scrollY;
    const focusInfo = currentFocus();
    if (!cues.length) {
      listEl.innerHTML = `<li class="cue-empty"><p>This subtitle has no cues.</p><button type="button" class="btn btn-primary btn-sm" data-act="add-start">Add the first cue</button></li>`;
    } else {
      listEl.innerHTML = cues.map(rowMarkup).join('');
    }
    countEl.textContent = plural(cues.length, 'cue');
    paintSelection();
    paintQcSummary();
    if (keepScroll) window.scrollTo(0, scrollY);
    if (focusInfo) restoreFocus(focusInfo);
    activeId = null;
    syncActive(true);
  }

  function paintWarnings(i) {
    const c = cues[i];
    if (!c) return;
    const row = rowOf(c.id);
    if (!row) return;
    const warnings = warningsFor(i);
    const el = row.querySelector('[data-role=warn]');
    el.innerHTML = warningsMarkup(warnings);
    row.classList.toggle('has-warning', warnings.length > 0);
    row.querySelector('[data-role=dur]').textContent = `${((c.end_ms - c.start_ms) / 1000).toFixed(2)} s`;
  }

  function paintRowTimes(i) {
    const c = cues[i];
    const row = rowOf(c.id);
    if (!row) return;
    const s = row.querySelector('[data-role=start]');
    const e = row.querySelector('[data-role=end]');
    if (!invalid.has(`${c.id}:start`)) s.value = timecode(c.start_ms);
    if (!invalid.has(`${c.id}:end`)) e.value = timecode(c.end_ms);
  }

  let qcTimer = null;
  function paintQcSummary() {
    clearTimeout(qcTimer);
    qcTimer = setTimeout(() => {
      let n = 0;
      for (let i = 0; i < cues.length; i += 1) if (warningsFor(i).length) n += 1;
      qcEl.textContent = n ? `${plural(n, 'cue')} with issues.` : 'No issues found.';
      qcEl.dataset.tone = n ? 'warn' : 'ok';
    }, 150);
  }

  function paintSelection() {
    const n = selected.size;
    selCountEl.textContent = n ? `${n} selected` : '';
    deleteSelBtn.disabled = n === 0;
    shiftScopeEl.textContent = n ? `Applies to ${plural(n, 'selected cue')}.` : 'Applies to all cues.';
    const all = $('[data-select-all]', root);
    all.checked = n > 0 && n === cues.length;
    all.indeterminate = n > 0 && n < cues.length;
  }

  function currentFocus() {
    const a = document.activeElement;
    if (!a || !listEl.contains(a)) return null;
    const row = a.closest('li[data-id]');
    if (!row) return null;
    return { id: Number(row.dataset.id), role: a.dataset.role, act: a.dataset.act, start: a.selectionStart, end: a.selectionEnd };
  }

  function restoreFocus(info, fallbackId) {
    const row = rowOf(info ? info.id : fallbackId) || (fallbackId ? rowOf(fallbackId) : null);
    if (!row) return;
    const sel = info && info.role ? `[data-role="${info.role}"]` : info && info.act ? `[data-act="${info.act}"]` : '[data-role=text]';
    const el = row.querySelector(sel) || row.querySelector('[data-role=text]');
    if (!el) return;
    el.focus({ preventScroll: true });
    if (info && typeof info.start === 'number' && el.setSelectionRange) {
      try {
        el.setSelectionRange(info.start, info.end);
      } catch {
        /* not a text control */
      }
    }
  }

  function focusCue(id, role = 'text') {
    const row = rowOf(id);
    if (!row) return;
    row.scrollIntoView({ block: 'center' });
    const el = row.querySelector(`[data-role=${role}]`);
    if (el) el.focus({ preventScroll: true });
  }

  /* ---------- playback sync ---------- */

  function playheadMs() {
    return media ? Math.round(media.currentTime * 1000) : null;
  }

  function cueAt(ms) {
    // Binary search for the last cue starting at or before ms, then scan back for overlaps.
    let lo = 0;
    let hi = cues.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (cues[mid].start_ms <= ms) {
        found = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    for (let i = found; i >= 0 && i > found - 5; i -= 1) {
      if (cues[i].start_ms <= ms && ms < cues[i].end_ms) return cues[i];
    }
    return null;
  }

  function syncActive(force = false) {
    if (!media) return;
    const ms = playheadMs();
    playheadEl.textContent = timecode(ms);
    const cue = cueAt(ms);
    const id = cue ? cue.id : null;
    if (captionEl) captionEl.textContent = cue ? cue.text : '';
    if (captionEl) captionEl.hidden = !cue || !cue.text;
    if (id === activeId && !force) return;
    if (activeId !== null) {
      const prev = rowOf(activeId);
      if (prev) prev.classList.remove('is-active');
    }
    activeId = id;
    if (id === null) return;
    const row = rowOf(id);
    if (!row) return;
    row.classList.add('is-active');
    const editing = document.activeElement && listEl.contains(document.activeElement);
    if (follow && !media.paused && !editing) row.scrollIntoView({ block: 'nearest' });
  }

  let raf = 0;
  function loop() {
    syncActive();
    raf = media && !media.paused ? requestAnimationFrame(loop) : 0;
  }
  if (media) {
    media.addEventListener('play', () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(loop);
    });
    media.addEventListener('seeked', () => syncActive());
    media.addEventListener('timeupdate', () => {
      if (!raf) syncActive();
    });
    media.addEventListener('error', () => {
      notify.warn('The media preview could not be played in this browser. Timing tools still work.');
    });
    $('[data-follow]', root).addEventListener('change', (e) => {
      follow = e.target.checked;
    });
  }

  function seek(ms, play = false) {
    if (!media) return;
    media.currentTime = Math.max(0, ms / 1000);
    if (play) media.play().catch(() => {});
    syncActive(true);
  }

  /* ---------- edits ---------- */

  function commitTime(input) {
    const row = input.closest('li[data-id]');
    const id = Number(row.dataset.id);
    const i = indexOf(id);
    if (i < 0) return;
    const role = input.dataset.role;
    const key = `${id}:${role}`;
    const ms = parseTimecode(input.value);
    if (ms === null) {
      invalid.add(key);
      input.setAttribute('aria-invalid', 'true');
      input.title = 'Use HH:MM:SS,mmm, for example 00:01:23,450';
      return;
    }
    invalid.delete(key);
    input.removeAttribute('aria-invalid');
    input.removeAttribute('title');
    const c = cues[i];
    const field = role === 'start' ? 'start_ms' : 'end_ms';
    if (c[field] === ms) {
      input.value = timecode(ms);
      return;
    }
    c[field] = ms;
    input.value = timecode(ms);
    setDirty();
    afterTimingChange(id, i);
  }

  /** Re-sort if needed; otherwise update warnings for the cue and its neighbours. */
  function afterTimingChange(id, i) {
    const prev = cues[i - 1];
    const next = cues[i + 1];
    const c = cues[i];
    const outOfOrder = (prev && prev.start_ms > c.start_ms) || (next && next.start_ms < c.start_ms);
    if (outOfOrder) {
      sortCues();
      renderAll();
      return;
    }
    paintWarnings(i - 1);
    paintWarnings(i);
    paintWarnings(i + 1);
    paintQcSummary();
    if (activeId === null || activeId === id) syncActive(true);
  }

  function insertAfter(i, startOverride = null) {
    const c = cues[i];
    const next = cues[i + 1];
    const start = startOverride !== null ? startOverride : c ? c.end_ms + 1 : 0;
    let end = start + 2000;
    if (next && next.start_ms - 1 > start + MIN_DURATION) end = Math.min(end, next.start_ms - 1);
    const cue = { id: uid(), start_ms: start, end_ms: end, text: '' };
    cues.splice(i + 1, 0, cue);
    sortCues();
    setDirty();
    renderAll();
    focusCue(cue.id);
  }

  function deleteCue(i) {
    const [removed] = cues.splice(i, 1);
    selected.delete(removed.id);
    setDirty();
    renderAll();
    const neighbour = cues[i] || cues[i - 1];
    if (neighbour) restoreFocus(null, neighbour.id);
    notify.info(`Deleted cue ${i + 1}.`);
  }

  function splitCue(i) {
    const c = cues[i];
    if (c.end_ms - c.start_ms < 2) return;
    const text = c.text;
    const pos = caret.has(c.id) ? caret.get(c.id) : -1;
    let at = pos > 0 && pos < text.length ? pos : -1;
    if (at < 0) {
      // No caret inside the text: split on the space closest to the middle.
      const mid = Math.floor(text.length / 2);
      let best = -1;
      for (let k = 0; k < text.length; k += 1) {
        if (/\s/.test(text[k]) && (best < 0 || Math.abs(k - mid) < Math.abs(best - mid))) best = k;
      }
      at = best > 0 ? best : mid;
    }
    const head = text.slice(0, at).trim();
    const tail = text.slice(at).trim();
    const ph = playheadMs();
    let splitMs;
    if (ph !== null && ph > c.start_ms + 100 && ph < c.end_ms - 100) splitMs = ph;
    else {
      const ratio = text.length ? at / text.length : 0.5;
      splitMs = Math.round(c.start_ms + (c.end_ms - c.start_ms) * Math.min(0.9, Math.max(0.1, ratio)));
    }
    splitMs = Math.min(c.end_ms - 1, Math.max(c.start_ms + 1, splitMs));
    const second = { id: uid(), start_ms: splitMs, end_ms: c.end_ms, text: tail };
    c.end_ms = splitMs;
    c.text = head;
    cues.splice(i + 1, 0, second);
    caret.delete(c.id);
    sortCues();
    setDirty();
    renderAll();
    focusCue(second.id);
  }

  function mergeNext(i) {
    const c = cues[i];
    const next = cues[i + 1];
    if (!next) return;
    c.end_ms = Math.max(c.end_ms, next.end_ms);
    c.text = [c.text.trim(), next.text.trim()].filter(Boolean).join('\n');
    cues.splice(i + 1, 1);
    selected.delete(next.id);
    setDirty();
    renderAll();
    restoreFocus(null, c.id);
  }

  function setFromPlayhead(i, which) {
    const ms = playheadMs();
    if (ms === null) return;
    const c = cues[i];
    if (which === 'start') c.start_ms = ms;
    else c.end_ms = ms;
    invalid.delete(`${c.id}:${which}`);
    const row = rowOf(c.id);
    if (row) row.querySelector(`[data-role=${which}]`).removeAttribute('aria-invalid');
    setDirty();
    paintRowTimes(i);
    afterTimingChange(c.id, i);
  }

  function shift(direction) {
    const amount = Math.round(Number($('[data-shift]', root).value));
    if (!Number.isFinite(amount) || amount === 0) {
      notify.error('Enter the number of milliseconds to shift by.');
      return;
    }
    const delta = amount * direction;
    const targets = selected.size ? cues.filter((c) => selected.has(c.id)) : cues;
    if (!targets.length) return;
    const minStart = Math.min(...targets.map((c) => c.start_ms));
    if (minStart + delta < 0) {
      notify.error(`Cannot shift earlier than 00:00:00,000. The earliest cue starts at ${timecode(minStart)}.`);
      return;
    }
    targets.forEach((c) => {
      c.start_ms += delta;
      c.end_ms += delta;
    });
    sortCues();
    setDirty();
    renderAll();
    notify.ok(`Shifted ${plural(targets.length, 'cue')} ${delta > 0 ? 'later' : 'earlier'} by ${Math.abs(delta)} ms.`);
  }

  /* ---------- find & replace ---------- */

  function makeMatcher() {
    const q = findInput.value;
    if (!q) return null;
    const flags = caseInput.checked ? 'g' : 'gi';
    return new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), flags);
  }

  function countMatches() {
    const re = makeMatcher();
    if (!re) {
      findCountEl.textContent = '';
      return 0;
    }
    let n = 0;
    for (const c of cues) {
      const m = c.text.match(re);
      if (m) n += m.length;
    }
    findCountEl.textContent = n ? `${plural(n, 'match', 'matches')}` : 'No matches';
    return n;
  }

  function findNext() {
    if (!cues.length) return;
    const re = makeMatcher();
    if (!re) {
      findInput.focus();
      return;
    }
    const startIdx = lastFocusedId !== null ? Math.max(0, indexOf(lastFocusedId)) : 0;
    const startPos = lastFocusedId !== null && caret.has(lastFocusedId) ? caret.get(lastFocusedId) : 0;
    for (let k = 0; k <= cues.length; k += 1) {
      const i = (startIdx + k) % cues.length;
      const c = cues[i];
      re.lastIndex = k === 0 ? startPos : 0;
      const m = re.exec(c.text);
      if (m) {
        const row = rowOf(c.id);
        row.scrollIntoView({ block: 'center' });
        const ta = row.querySelector('[data-role=text]');
        ta.focus({ preventScroll: true });
        ta.setSelectionRange(m.index, m.index + m[0].length);
        caret.set(c.id, m.index + m[0].length);
        lastFocusedId = c.id;
        return;
      }
    }
    notify.info('No matches.');
  }

  function replaceAll() {
    const re = makeMatcher();
    if (!re) {
      findInput.focus();
      return;
    }
    const replacement = replaceInput.value;
    let n = 0;
    for (const c of cues) {
      const next = c.text.replace(re, () => {
        n += 1;
        return replacement;
      });
      c.text = next;
    }
    if (!n) {
      notify.info('No matches to replace.');
      return;
    }
    setDirty();
    renderAll();
    countMatches();
    notify.ok(`Replaced ${plural(n, 'match', 'matches')}.`);
  }

  function nextIssue() {
    const start = lastFocusedId !== null ? indexOf(lastFocusedId) + 1 : 0;
    for (let k = 0; k < cues.length; k += 1) {
      const i = (start + k) % cues.length;
      if (warningsFor(i).length) {
        focusCue(cues[i].id);
        lastFocusedId = cues[i].id;
        return;
      }
    }
    notify.ok('No issues found.');
  }

  /* ---------- save ---------- */

  function validate() {
    if (invalid.size) return 'Fix the highlighted timecodes before saving.';
    if (!cues.length) return 'A subtitle needs at least one cue. Delete the file from the queue instead.';
    if (cues.length > MAX_CUES) return `A subtitle can have at most ${MAX_CUES} cues.`;
    for (let i = 0; i < cues.length; i += 1) {
      const c = cues[i];
      if (c.start_ms < 0) return `Cue ${i + 1} starts before zero.`;
      if (c.end_ms <= c.start_ms) return `Cue ${i + 1} must end after it starts.`;
      if (c.end_ms > MAX_MS) return `Cue ${i + 1} ends after 100 hours.`;
      if (c.text.length > MAX_TEXT) return `Cue ${i + 1} is longer than ${MAX_TEXT} characters.`;
    }
    return '';
  }

  async function save() {
    if (saving) return;
    const focused = document.activeElement;
    if (focused && listEl.contains(focused) && /^(start|end)$/.test(focused.dataset.role || '')) commitTime(focused);
    const problem = validate();
    if (problem) {
      notify.error(problem);
      const firstInvalid = listEl.querySelector('[aria-invalid=true]');
      if (firstInvalid) {
        firstInvalid.scrollIntoView({ block: 'center' });
        firstInvalid.focus({ preventScroll: true });
      }
      return;
    }
    saving = true;
    const button = $('[data-act=save]', root);
    button.disabled = true;
    try {
      sortCues();
      const seq = editSeq;
      await api.put(
        `/api/subtitles/${encodeURIComponent(jobId)}`,
        cues.map((c) => ({ start_ms: c.start_ms, end_ms: c.end_ms, text: c.text })),
      );
      if (seq === editSeq) setDirty(false); // edits made while saving stay unsaved
      notify.ok('Subtitle saved.');
    } catch (err) {
      notify.error(err);
    } finally {
      saving = false;
      button.disabled = false;
    }
  }

  /* ---------- events ---------- */

  const rowIndex = (el) => {
    const row = el.closest('li[data-id]');
    return row ? indexOf(Number(row.dataset.id)) : -1;
  };

  const onRootClick = (event) => {
    const target = event.target.closest('[data-act]');
    if (!target || !root.contains(target)) return;
    const act = target.dataset.act;
    const i = rowIndex(target);
    switch (act) {
      case 'save':
        save();
        break;
      case 'seek':
        if (i >= 0) {
          if (media) seek(cues[i].start_ms, true);
          else focusCue(cues[i].id);
        }
        break;
      case 'set-start':
      case 'set-end':
        if (!media) notify.info('There is no media preview to take the playhead from.');
        else if (i >= 0) setFromPlayhead(i, act === 'set-start' ? 'start' : 'end');
        break;
      case 'insert':
        if (i >= 0) insertAfter(i);
        break;
      case 'add-start': {
        // Insert at the playhead when there is a preview, otherwise after the last cue.
        const after = media ? cues.findIndex((c) => c.start_ms > playheadMs()) : -1;
        insertAfter(after === -1 ? cues.length - 1 : after - 1, media ? playheadMs() : null);
        break;
      }
      case 'split':
        if (i >= 0) splitCue(i);
        break;
      case 'merge':
        if (i >= 0) mergeNext(i);
        break;
      case 'delete':
        if (i >= 0) deleteCue(i);
        break;
      case 'delete-selected':
        deleteSelected();
        break;
      case 'shift':
        shift(Number(target.dataset.dir));
        break;
      case 'find-next':
        findNext();
        break;
      case 'replace-all':
        replaceAll();
        break;
      case 'next-issue':
        nextIssue();
        break;
      default:
        break;
    }
  };
  root.addEventListener('click', onRootClick);

  async function deleteSelected() {
    const n = selected.size;
    if (!n) return;
    const ok = await confirmDialog({ title: `Delete ${plural(n, 'cue')}?`, message: 'The selected cues are removed. Nothing is written to disk until you save.', confirmLabel: 'Delete cues', danger: true });
    if (!ok) return;
    cues = cues.filter((c) => !selected.has(c.id));
    selected.clear();
    setDirty();
    renderAll();
  }

  listEl.addEventListener('change', (event) => {
    const t = event.target;
    if (t.dataset.role === 'select') {
      const id = Number(t.closest('li[data-id]').dataset.id);
      if (t.checked) selected.add(id);
      else selected.delete(id);
      paintSelection();
    } else if (t.dataset.role === 'start' || t.dataset.role === 'end') {
      commitTime(t);
    }
  });

  listEl.addEventListener('input', (event) => {
    const t = event.target;
    if (t.dataset.role !== 'text') return;
    const i = rowIndex(t);
    if (i < 0) return;
    cues[i].text = t.value;
    caret.set(cues[i].id, t.selectionStart);
    const rows = textRows(t.value);
    if (t.rows !== rows) t.rows = rows;
    setDirty();
    paintWarnings(i);
    paintQcSummary();
    if (activeId === cues[i].id && captionEl) {
      captionEl.textContent = t.value;
      captionEl.hidden = !t.value;
    }
  });

  const rememberCaret = (event) => {
    const t = event.target;
    if (!t || t.dataset.role !== 'text') return;
    const row = t.closest('li[data-id]');
    if (row) caret.set(Number(row.dataset.id), t.selectionStart);
  };
  listEl.addEventListener('keyup', rememberCaret);
  listEl.addEventListener('mouseup', rememberCaret);
  listEl.addEventListener('focusin', (event) => {
    const row = event.target.closest('li[data-id]');
    if (row) lastFocusedId = Number(row.dataset.id);
  });

  listEl.addEventListener('keydown', (event) => {
    const t = event.target;
    // Enter in a timecode field commits it.
    if ((t.dataset.role === 'start' || t.dataset.role === 'end') && event.key === 'Enter') {
      event.preventDefault();
      commitTime(t);
    }
  });

  $('[data-select-all]', root).addEventListener('change', (event) => {
    selected.clear();
    if (event.target.checked) cues.forEach((c) => selected.add(c.id));
    listEl.querySelectorAll('[data-role=select]').forEach((box) => {
      box.checked = event.target.checked;
    });
    paintSelection();
  });

  const onFind = () => countMatches();
  findInput.addEventListener('input', onFind);
  caseInput.addEventListener('change', onFind);
  findInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      findNext();
    }
  });

  const onKey = (event) => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 's') {
      event.preventDefault();
      save();
    }
  };
  document.addEventListener('keydown', onKey);

  ctx.setGuard(() => (dirty ? 'This subtitle has unsaved changes. They will be lost if you leave.' : ''));

  renderAll({ keepScroll: false });

  return () => {
    document.removeEventListener('keydown', onKey);
    root.removeEventListener('click', onRootClick);
    cancelAnimationFrame(raf);
    clearTimeout(qcTimer);
    if (media) {
      media.pause();
      media.removeAttribute('src');
      media.load();
    }
  };
}
