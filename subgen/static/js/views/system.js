// System: runtime info, hardware, storage, cache, backup/restore and audit log.
import { api, qs, downloadBlob, request } from '../api.js';
import { html, render, delegate, busy, debounce, $ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { notify } from '../ui/toast.js';
import { confirmDialog } from '../ui/dialog.js';
import { pageHeader, errorState, skeletonRows, emptyState, pager, meter, workerBadge } from '../ui/components.js';
import { bytes, dateTime, relative, plural } from '../ui/format.js';

const AUDIT_LIMIT = 50;

export async function mount(ctx) {
  ctx.setTitle('System');
  const root = ctx.root;
  render(
    root,
    html`${pageHeader({ title: 'System', subtitle: 'Runtime, hardware, storage and administrative history.' })}
    <div class="system-grid">
      <section class="panel" aria-labelledby="sys-rt-h" data-slot="runtime">${skeletonRows(4)}</section>
      <section class="panel" aria-labelledby="sys-hw-h" data-slot="hardware">${skeletonRows(3)}</section>
      <section class="panel" aria-labelledby="sys-disk-h" data-slot="storage">${skeletonRows(3)}</section>
      <section class="panel" aria-labelledby="sys-bk-h">
        <div class="panel-head"><h2 id="sys-bk-h">Backup and restore</h2></div>
        <div class="panel-body stack">
          <p class="small muted">The backup is a consistent copy of the SubGen database: libraries, settings, job history and audit log. It does not include media, subtitles or models.</p>
          <div class="row">
            <button type="button" class="btn" data-action="backup">${icon('download')}Download backup</button>
            <label class="btn file-btn">${icon('upload')}Restore from backup…<input type="file" accept=".db,.sqlite,.sqlite3,application/vnd.sqlite3,application/octet-stream" data-restore class="sr-only"></label>
          </div>
          <p class="small muted">Restoring replaces the current database and restarts SubGen. You will need to sign in again.</p>
        </div>
      </section>
    </div>
    <section class="panel audit" aria-labelledby="sys-audit-h">
      <div class="panel-head">
        <h2 id="sys-audit-h">Audit log</h2>
        <span class="spacer"></span>
        <div class="input-group audit-search">
          ${icon('search')}
          <input class="input" type="search" data-audit-q placeholder="Search actions and targets" aria-label="Search audit log">
        </div>
      </div>
      <div data-slot="audit">${skeletonRows(6)}</div>
      <div class="panel-foot" data-slot="audit-foot" hidden></div>
    </section>`,
  );

  const slots = {
    runtime: $('[data-slot=runtime]', root),
    hardware: $('[data-slot=hardware]', root),
    storage: $('[data-slot=storage]', root),
    audit: $('[data-slot=audit]', root),
    auditFoot: $('[data-slot=audit-foot]', root),
  };
  const audit = { q: '', offset: 0, total: 0 };
  let restoreTimer = null;

  async function loadSystem() {
    try {
      const s = await api.get('/api/system');
      paintRuntime(slots.runtime, s);
      paintHardware(slots.hardware, s);
      paintStorage(slots.storage, s);
    } catch (err) {
      if (err.status !== 401) render(slots.runtime, errorState(err, { compact: true, retryAction: 'reload-system' }));
    }
  }

  async function loadAudit() {
    try {
      const res = await api.get(`/api/audit${qs({ q: audit.q, limit: AUDIT_LIMIT, offset: audit.offset })}`);
      audit.total = res.total || 0;
      paintAudit(res.items || []);
    } catch (err) {
      if (err.status !== 401) render(slots.audit, errorState(err, { compact: true, retryAction: 'reload-audit' }));
    }
  }

  function paintAudit(items) {
    if (!items.length) {
      render(slots.audit, emptyState({ iconName: 'history', compact: true, title: audit.q ? 'No matching entries' : 'No audit entries yet', text: audit.q ? 'Try a different search.' : 'Sign-ins, settings changes, deletions and subtitle edits are recorded here.' }));
      slots.auditFoot.hidden = true;
      return;
    }
    render(
      slots.audit,
      html`<div class="table-wrap"><table class="table audit-table">
        <thead><tr><th scope="col">When</th><th scope="col">Action</th><th scope="col">Target</th><th scope="col">Detail</th></tr></thead>
        <tbody>${items.map(
          (e) => html`<tr>
            <td class="nowrap small"><time datetime="${e.created_at || ''}" title="${relative(e.created_at)}">${dateTime(e.created_at)}</time></td>
            <td class="nowrap"><span class="badge plain" data-tone="outline">${e.action}</span></td>
            <td class="break">${e.target || html`<span class="faint">—</span>`}</td>
            <td class="break small muted">${typeof e.detail === 'object' && e.detail !== null ? JSON.stringify(e.detail) : e.detail || ''}</td>
          </tr>`,
        )}</tbody>
      </table></div>`,
    );
    slots.auditFoot.hidden = false;
    render(slots.auditFoot, html`<span class="small muted">${plural(audit.total, 'entry', 'entries')}</span>${pager({ total: audit.total, limit: AUDIT_LIMIT, offset: audit.offset })}`);
  }

  const searchEl = $('[data-audit-q]', root);
  const onSearch = debounce(() => {
    audit.q = searchEl.value.trim();
    audit.offset = 0;
    loadAudit();
  }, 300);
  searchEl.addEventListener('input', onSearch);

  const off = delegate(root, 'click', {
    'reload-system': () => loadSystem(),
    'reload-audit': () => loadAudit(),
    'page-prev': () => {
      audit.offset = Math.max(0, audit.offset - AUDIT_LIMIT);
      loadAudit();
    },
    'page-next': () => {
      audit.offset += AUDIT_LIMIT;
      loadAudit();
    },
    'clear-cache': async (el) => {
      const ok = await confirmDialog({
        title: 'Clear transcript cache?',
        message: 'Stored transcripts are deleted. Future jobs for the same media will transcribe the audio again instead of reusing them.',
        confirmLabel: 'Clear cache',
        danger: true,
      });
      if (!ok) return;
      await busy(el, async () => {
        try {
          const res = await api.post('/api/system/cache/clear');
          notify.ok(`Deleted ${plural(res.deleted || 0, 'cached transcript')}.`);
          loadSystem();
          loadAudit();
        } catch (err) {
          notify.error(err);
        }
      });
    },
    backup: (el) =>
      busy(el, async () => {
        try {
          const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
          await downloadBlob('POST', '/api/backup', `subgen-backup-${stamp}.db`);
          notify.ok('Backup downloaded.');
          loadAudit();
        } catch (err) {
          notify.error(err);
        }
      }),
  });

  const restoreInput = $('[data-restore]', root);
  restoreInput.addEventListener('change', async () => {
    const file = restoreInput.files && restoreInput.files[0];
    restoreInput.value = '';
    if (!file) return;
    const ok = await confirmDialog({
      title: 'Restore this backup?',
      message: `SubGen replaces its current database with “${file.name}” (${bytes(file.size)}) and restarts.`,
      detail: 'Everything changed since that backup is lost. Make a fresh backup first if you are unsure.',
      confirmLabel: 'Restore and restart',
      danger: true,
    });
    if (!ok) return;
    const body = new FormData();
    body.append('backup_file', file, file.name);
    try {
      const res = await request('POST', '/api/restore', { body });
      notify.ok(`${(res && res.message) || 'Backup accepted.'} Redirecting to sign in…`);
      restoreTimer = setTimeout(() => window.location.assign('/login'), 5000);
    } catch (err) {
      notify.error(err);
    }
  });

  loadSystem();
  loadAudit();
  return () => {
    off();
    onSearch.cancel();
    clearTimeout(restoreTimer);
  };
}

function paintRuntime(el, s) {
  render(
    el,
    html`<div class="panel-head"><h2 id="sys-rt-h">Runtime</h2><span class="spacer"></span>${s.worker ? workerBadge(s.worker.state) : ''}</div>
    <div class="panel-body">
      <dl class="dl">
        <dt>SubGen</dt><dd>${s.version || '—'}</dd>
        <dt>Platform</dt><dd class="break">${s.platform || '—'}</dd>
        <dt>Python</dt><dd>${s.python || '—'}</dd>
        <dt>FFmpeg</dt><dd>${s.ffmpeg || html`<span class="tone-danger">Not found</span>`}</dd>
        <dt>Device</dt><dd>${s.device_mode || '—'}${s.worker && s.worker.device ? html` <span class="muted">(using ${String(s.worker.device).toUpperCase()})</span>` : ''}</dd>
        <dt>Compute type</dt><dd>${s.compute_type || '—'}${s.worker && s.worker.compute_type && s.worker.compute_type !== s.compute_type ? html` <span class="muted">(using ${s.worker.compute_type})</span>` : ''}</dd>
        ${s.worker && s.worker.model ? html`<dt>Loaded model</dt><dd class="break">${s.worker.model}</dd>` : ''}
      </dl>
      ${s.worker && s.worker.state === 'error' && s.worker.message ? html`<pre class="error-text">${s.worker.message}</pre>` : ''}
    </div>`,
  );
}

function paintHardware(el, s) {
  const gpus = s.gpus || [];
  render(
    el,
    html`<div class="panel-head"><h2 id="sys-hw-h">GPUs</h2></div>
    <div class="panel-body">
      ${gpus.length
        ? html`<ul class="usage-list">${gpus.map((g) => {
            const pct = g.memory_total ? (g.memory_used / g.memory_total) * 100 : 0;
            return html`<li>
              <div class="usage-head"><span>${icon('cpu')}${g.name}</span><span class="small muted num">${bytes(g.memory_used)} of ${bytes(g.memory_total)} memory</span></div>
              ${meter(pct, `${g.name} memory used`)}
            </li>`;
          })}</ul>`
        : html`<p class="muted small">No NVIDIA GPU is visible to SubGen. Jobs run on the CPU.</p>`}
    </div>`,
  );
}

function paintStorage(el, s) {
  const disks = s.disks || [];
  const cache = s.transcript_cache || { count: 0, bytes: 0 };
  const ws = s.workspace || { count: 0, bytes: 0 };
  render(
    el,
    html`<div class="panel-head"><h2 id="sys-disk-h">Storage</h2></div>
    <div class="panel-body stack">
      <ul class="usage-list">${disks.map((d) => {
        const used = Math.max(0, (d.total || 0) - (d.free || 0));
        const pct = d.total ? (used / d.total) * 100 : 0;
        return html`<li>
          <div class="usage-head"><span>${icon('disk')}${d.label}</span><span class="small muted num">${d.total ? `${bytes(d.free)} free of ${bytes(d.total)}` : 'Unknown'}</span></div>
          <code class="usage-path">${d.path}</code>
          ${meter(pct, `${d.label} disk used`)}
        </li>`;
      })}</ul>
      <div class="storage-rows">
        <div class="storage-row">
          <div><strong>Transcript cache</strong><p class="small muted num">${plural(cache.count || 0, 'transcript')}, ${bytes(cache.bytes || 0)}. Reused when the same media is subtitled into another language.</p></div>
          <button type="button" class="btn btn-sm" data-action="clear-cache" ${cache.count ? '' : 'disabled'}>${icon('trash')}Clear</button>
        </div>
        <div class="storage-row">
          <div><strong>Studio workspace</strong><p class="small muted num">${plural(ws.count || 0, 'upload')}, ${bytes(ws.bytes || 0)}</p></div>
          <a class="btn btn-sm" href="/studio">Open Studio</a>
        </div>
      </div>
    </div>`,
  );
}
