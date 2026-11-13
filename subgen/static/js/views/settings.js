// Settings: forms rendered from the server schema (GET /api/settings).
import { api } from '../api.js';
import { html, render, $, $$ } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { notify } from '../ui/toast.js';
import { pageHeader, errorState, skeletonRows } from '../ui/components.js';
import { languageOptions } from '../ui/lang-picker.js';
import { getLanguages } from '../store.js';
import { plural } from '../ui/format.js';

const GROUP_ORDER = ['transcription', 'segmentation', 'translation', 'scanning', 'output', 'workspace'];
const GROUP_LABEL = {
  transcription: 'Transcription',
  segmentation: 'Segmentation',
  translation: 'Translation',
  scanning: 'Scanning',
  output: 'Output',
  workspace: 'Workspace',
  security: 'Security',
};
const GROUP_INTRO = {
  transcription: 'How Whisper turns speech into text.',
  segmentation: 'How the transcript is cut into readable cues.',
  translation: 'Which local provider translates subtitles into other languages.',
  scanning: 'When and how libraries are checked for new media.',
  output: 'How subtitle files are written next to your media.',
  workspace: 'Studio uploads and temporary files.',
  security: 'Sign-in protection for the administrator account.',
};
const PROVIDERS = [
  ['huggingface', 'Hugging Face', 'Local translation model on this server'],
  ['ollama', 'Ollama', 'Open-source LLM through the Ollama API'],
  ['libretranslate', 'LibreTranslate', 'Self-hosted LibreTranslate server'],
];

export async function mount(ctx) {
  ctx.setTitle('Settings');
  const root = ctx.root;
  render(root, html`${pageHeader({ title: 'Settings', subtitle: 'Defaults for every subtitle job, and sign-in security. Individual jobs can override some of them.' })}${skeletonRows(6)}`);

  let payload;
  let languages = [];
  let models = { installed: [] };
  try {
    [payload, languages, models] = await Promise.all([
      api.get('/api/settings'),
      getLanguages().catch(() => []),
      api.get('/api/models').catch(() => ({ installed: [] })),
    ]);
  } catch (err) {
    if (err.status === 401) return undefined;
    render(root, html`${pageHeader({ title: 'Settings' })}<div class="panel">${errorState(err)}</div>`);
    root.querySelector('[data-action=retry-load]').addEventListener('click', () => ctx.reload());
    return undefined;
  }
  if (!ctx.isCurrent()) return undefined;

  const schema = payload.schema || [];
  const original = { ...(payload.values || {}) };
  const fieldByKey = new Map(schema.map((f) => [f.key, f]));
  const cleared = new Set(); // secret keys the user asked to clear
  const groups = [...GROUP_ORDER.filter((g) => schema.some((f) => f.group === g)), ...[...new Set(schema.map((f) => f.group))].filter((g) => !GROUP_ORDER.includes(g)), 'security'];
  let activeGroup = groups.includes(ctx.query.get('tab')) ? ctx.query.get('tab') : groups[0];

  render(
    root,
    html`${pageHeader({ title: 'Settings', subtitle: 'Defaults for every subtitle job, and sign-in security. Individual jobs can override some of them.' })}
    <form class="settings" novalidate data-form>
      <div class="tabs settings-tabs" role="tablist" aria-label="Settings sections" aria-orientation="vertical">
        ${groups.map(
          (g) => html`<button type="button" class="tab" role="tab" id="tab-${g}" aria-controls="panel-${g}" data-group="${g}" aria-selected="${g === activeGroup ? 'true' : 'false'}" tabindex="${g === activeGroup ? '0' : '-1'}">${GROUP_LABEL[g] || g}<span class="dirty-dot" hidden></span><span class="sr-only" data-dirty-sr></span></button>`,
        )}
      </div>
      ${groups.map(
        (g) => html`<section class="panel settings-panel" role="tabpanel" id="panel-${g}" aria-labelledby="tab-${g}" data-panel="${g}" ${g === activeGroup ? '' : 'hidden'}>
          <div class="panel-head"><div><h2>${GROUP_LABEL[g] || g}</h2><p class="small muted">${GROUP_INTRO[g] || ''}</p></div></div>
          ${g === 'translation' ? providerTests() : ''}
          ${g === 'security'
            ? html`<div class="security" data-security>${skeletonRows(2)}</div>`
            : html`<div class="settings-fields">
              ${schema.filter((f) => f.group === g).map((f) => fieldRow(f, original[f.key], languages, models))}
            </div>`}
        </section>`,
      )}
      <div class="savebar" data-savebar hidden>
        <span class="msg">${icon('edit')}<span data-dirty-count></span></span>
        <button type="button" class="btn btn-ghost" data-discard>Discard</button>
        <button type="submit" class="btn btn-primary">${icon('save')}Save changes</button>
      </div>
    </form>`,
  );

  const form = $('[data-form]', root);
  const savebar = $('[data-savebar]', root);

  /* ---------- reading values ---------- */

  function control(key) {
    return form.querySelector(`[data-key="${CSS.escape(key)}"]`);
  }

  /** Returns {value, valid}. */
  function readValue(field) {
    const el = control(field.key);
    if (!el) return { value: original[field.key], valid: true };
    switch (field.type) {
      case 'bool':
        return { value: el.checked, valid: true };
      case 'int':
      case 'float': {
        if (el.value.trim() === '') return { value: null, valid: false };
        const n = Number(el.value);
        if (!Number.isFinite(n) || (field.type === 'int' && !Number.isInteger(n))) return { value: null, valid: false };
        if ((field.min !== null && field.min !== undefined && n < field.min) || (field.max !== null && field.max !== undefined && n > field.max)) return { value: n, valid: false };
        return { value: n, valid: true };
      }
      case 'secret':
        if (cleared.has(field.key)) return { value: '', valid: true };
        return { value: el.value, valid: true };
      default:
        return { value: el.value, valid: true };
    }
  }

  function isChanged(field) {
    const { value } = readValue(field);
    if (field.type === 'secret') return cleared.has(field.key) || value !== '';
    if (field.type === 'int' || field.type === 'float') return value === null || Number(value) !== Number(original[field.key]);
    return value !== original[field.key] && !(value === '' && (original[field.key] === null || original[field.key] === undefined));
  }

  function changedValues() {
    const out = {};
    for (const f of schema) if (isChanged(f)) out[f.key] = readValue(f).value;
    return out;
  }

  function isDefault(field) {
    if (field.type === 'secret') return !(field.is_set && !cleared.has(field.key)) && !readValue(field).value;
    const { value } = readValue(field);
    if (field.type === 'int' || field.type === 'float') return Number(value) === Number(field.default);
    return value === field.default || (value === '' && (field.default === null || field.default === undefined));
  }

  /* ---------- painting state ---------- */

  function refresh() {
    let count = 0;
    const dirtyGroups = new Set();
    for (const f of schema) {
      const row = form.querySelector(`[data-row="${CSS.escape(f.key)}"]`);
      if (!row) continue;
      const changed = isChanged(f);
      const { valid } = readValue(f);
      if (changed) {
        count += 1;
        dirtyGroups.add(f.group);
      }
      row.classList.toggle('is-changed', changed);
      if (f.type === 'secret') {
        const input = row.querySelector('input[type=password]');
        if (input) input.placeholder = f.is_set && !cleared.has(f.key) ? '••••••••  (stored)' : 'Not set';
      }
      const el = control(f.key);
      if (el && f.type !== 'bool') {
        if (valid) el.removeAttribute('aria-invalid');
        else el.setAttribute('aria-invalid', 'true');
      }
      const err = row.querySelector('[data-error]');
      if (err) err.hidden = valid;
      const reset = row.querySelector('[data-reset]');
      if (reset) reset.hidden = f.type === 'secret' || isDefault(f);
      if (f.type === 'secret') {
        const hint = row.querySelector('[data-secret-state]');
        const clearBtn = row.querySelector('[data-clear]');
        if (hint) hint.textContent = cleared.has(f.key) ? 'Will be cleared when you save.' : f.is_set ? 'A value is stored. Type a new one to replace it.' : 'No value stored.';
        if (clearBtn) clearBtn.hidden = !f.is_set || cleared.has(f.key);
      }
    }
    $$('[data-group]', form).forEach((tab) => {
      const d = dirtyGroups.has(tab.dataset.group);
      tab.querySelector('.dirty-dot').hidden = !d;
      tab.querySelector('[data-dirty-sr]').textContent = d ? ' (unsaved changes)' : '';
    });
    savebar.hidden = count === 0;
    $('[data-dirty-count]', savebar).textContent = `${plural(count, 'unsaved change')}`;
    return count;
  }

  function setControl(field, value) {
    const el = control(field.key);
    if (!el) return;
    if (field.type === 'bool') el.checked = Boolean(value);
    else if (field.type === 'secret') el.value = '';
    else el.value = value === null || value === undefined ? '' : String(value);
  }

  function selectTab(group, focus = false) {
    activeGroup = group;
    $$('[data-group]', form).forEach((tab) => {
      const on = tab.dataset.group === group;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
      if (on && focus) tab.focus();
    });
    $$('[data-panel]', form).forEach((p) => {
      p.hidden = p.dataset.panel !== group;
    });
    ctx.replaceQuery({ tab: group === groups[0] ? '' : group });
  }

  /* ---------- events ---------- */

  form.addEventListener('input', () => refresh());
  form.addEventListener('change', () => refresh());

  form.addEventListener('click', async (event) => {
    const tab = event.target.closest('[data-group]');
    if (tab) {
      selectTab(tab.dataset.group);
      return;
    }
    const reset = event.target.closest('[data-reset]');
    if (reset) {
      const f = fieldByKey.get(reset.dataset.reset);
      if (f.type === 'secret') cleared.add(f.key);
      setControl(f, f.default);
      refresh();
      const el = control(f.key);
      if (el) el.focus();
      return;
    }
    const clear = event.target.closest('[data-clear]');
    if (clear) {
      cleared.add(clear.dataset.clear);
      control(clear.dataset.clear).value = '';
      refresh();
      control(clear.dataset.clear).focus();
      return;
    }
    const test = event.target.closest('[data-test]');
    if (test) {
      runTest(test);
      return;
    }
    if (event.target.closest('[data-discard]')) discard();
  });

  // Typing a new secret value cancels a pending clear.
  form.addEventListener('input', (event) => {
    const key = event.target.dataset && event.target.dataset.key;
    if (key && fieldByKey.get(key) && fieldByKey.get(key).type === 'secret' && event.target.value) {
      cleared.delete(key);
      refresh();
    }
  });

  // Arrow-key navigation between tabs (WAI-ARIA tabs pattern).
  $('.settings-tabs', form).addEventListener('keydown', (event) => {
    const i = groups.indexOf(activeGroup);
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = groups[(i + 1) % groups.length];
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = groups[(i - 1 + groups.length) % groups.length];
    else if (event.key === 'Home') next = groups[0];
    else if (event.key === 'End') next = groups[groups.length - 1];
    if (next) {
      event.preventDefault();
      selectTab(next, true);
    }
  });

  function discard() {
    cleared.clear();
    for (const f of schema) setControl(f, original[f.key]);
    refresh();
    notify.info('Changes discarded.');
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const invalidField = schema.find((f) => !readValue(f).valid);
    if (invalidField) {
      selectTab(invalidField.group);
      const el = control(invalidField.key);
      if (el) el.focus();
      notify.error(`${invalidField.label}: ${rangeText(invalidField)}`);
      return;
    }
    const values = changedValues();
    if (!Object.keys(values).length) return;
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
      const res = await api.put('/api/settings', { values });
      Object.assign(original, res && res.values ? res.values : values);
      for (const [key, value] of Object.entries(values)) {
        const f = fieldByKey.get(key);
        if (f && f.type === 'secret') f.is_set = value !== '';
      }
      cleared.clear();
      for (const f of schema) setControl(f, original[f.key]);
      refresh();
      notify.ok(`Saved ${plural(Object.keys(values).length, 'setting')}.`);
    } catch (err) {
      notify.error(err);
      // 422 details start with the field label, e.g. "Beam size: must be between 1 and 10".
      const f = schema.find((x) => err.message && err.message.startsWith(`${x.label}:`));
      if (f) {
        selectTab(f.group);
        const el = control(f.key);
        if (el) {
          el.setAttribute('aria-invalid', 'true');
          el.focus();
        }
      }
    } finally {
      button.disabled = false;
    }
  });

  async function runTest(button) {
    const kind = button.dataset.test;
    const out = form.querySelector(`[data-test-result="${kind}"]`);
    button.disabled = true;
    render(out, html`<span class="badge" data-tone="info">${icon('loader', { cls: 'spin' })}Testing…</span>`);
    try {
      const res = await api.post(`/api/providers/${encodeURIComponent(kind)}/test`, { values: changedValues() });
      render(out, html`<span class="badge" data-tone="${res.ok ? 'ok' : 'danger'}">${icon(res.ok ? 'check' : 'x-circle')}${res.ok ? 'Connected' : 'Failed'}</span> <span class="small test-msg"></span>`);
      out.querySelector('.test-msg').textContent = res.message || '';
      if (kind === 'ollama' && Array.isArray(res.models)) fillOllamaModels(res.models);
    } catch (err) {
      render(out, html`<span class="badge" data-tone="danger">${icon('x-circle')}Failed</span> <span class="small test-msg"></span>`);
      out.querySelector('.test-msg').textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }

  function fillOllamaModels(names) {
    const field = schema.find((f) => f.group === 'translation' && /ollama/.test(f.key) && /model/.test(f.key));
    if (!field) return;
    const input = control(field.key);
    if (!input || input.tagName !== 'INPUT') return;
    let list = document.getElementById('ollama-models');
    if (!list) {
      list = document.createElement('datalist');
      list.id = 'ollama-models';
      input.after(list);
      input.setAttribute('list', 'ollama-models');
    }
    list.textContent = '';
    for (const name of names) {
      const opt = document.createElement('option');
      opt.value = name;
      list.append(opt);
    }
    notify.info(`${plural(names.length, 'Ollama model')} available. Pick one from the ${field.label} suggestions.`);
  }

  ctx.setGuard(() => (refresh() ? 'You have unsaved settings. They will be lost if you leave.' : ''));
  refresh();
  mountSecurity($('[data-security]', root));
  return undefined;
}

/* ---------- Security: two-factor sign-in (TOTP) ---------- */

function groupSecret(secret) {
  return secret.replace(/(.{4})/g, '$1 ').trim();
}

function mountSecurity(box) {
  let state = null; // {enabled, managed_by_env}
  let setup = null; // {secret, uri} while enabling

  async function load() {
    try {
      state = await api.get('/api/totp');
      paint();
    } catch (err) {
      if (err.status !== 401) render(box, errorState(err, { compact: true }));
    }
  }

  function paint() {
    if (state.managed_by_env) {
      render(box, html`<div class="security-row">
        <div><h3>Two-factor sign-in</h3><p class="small muted">After your password, sign-in asks for a six-digit code from an authenticator app.</p></div>
        <span class="badge" data-tone="ok">${icon('check')}On</span>
      </div>
      <div class="callout" data-tone="info" role="note">${icon('info')}<div>It is configured on the server with <code>SUBGEN_TOTP_SECRET</code>. Remove that variable and restart SubGen to manage it here.</div></div>`);
      return;
    }
    if (state.enabled) {
      render(box, html`<div class="security-row">
        <div><h3>Two-factor sign-in</h3><p class="small muted">After your password, sign-in asks for a six-digit code from your authenticator app.</p></div>
        <span class="badge" data-tone="ok">${icon('check')}On</span>
      </div>
      <div class="security-form">
        <div class="field">
          <label for="totp-off-code">Current code</label>
          <input class="input num" id="totp-off-code" data-code inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456" aria-describedby="totp-off-help">
          <span class="field-help" id="totp-off-help">Enter a code from your authenticator app to turn two-factor sign-in off.</span>
        </div>
        <button type="button" class="btn btn-danger" data-totp="disable">Turn off</button>
      </div>`);
      return;
    }
    if (!setup) {
      render(box, html`<div class="security-row">
        <div><h3>Two-factor sign-in</h3><p class="small muted">Ask for a six-digit code from an authenticator app (such as Google Authenticator, Microsoft Authenticator, 1Password or Bitwarden) after the password.</p></div>
        <span class="badge">Off</span>
      </div>
      <div><button type="button" class="btn btn-primary" data-totp="setup">${icon('check')}Set up two-factor sign-in</button></div>`);
      return;
    }
    render(box, html`<div class="security-row">
        <div><h3>Set up two-factor sign-in</h3><p class="small muted">Nothing changes until you confirm a code.</p></div>
        <span class="badge">Off</span>
      </div>
      <ol class="security-steps">
        <li>
          <p>In your authenticator app, add an account and enter this setup key (time-based, 6 digits):</p>
          <div class="security-secret">
            <code class="mono break" data-secret>${groupSecret(setup.secret)}</code>
            <button type="button" class="btn btn-sm" data-totp="copy">Copy key</button>
            <a class="btn btn-sm btn-ghost" data-uri href="${setup.uri}">Open in authenticator app</a>
          </div>
        </li>
        <li>
          <div class="security-form">
            <div class="field">
              <label for="totp-on-code">Code from the app</label>
              <input class="input num" id="totp-on-code" data-code inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456">
            </div>
            <button type="button" class="btn btn-primary" data-totp="enable">Verify and turn on</button>
            <button type="button" class="btn btn-ghost" data-totp="cancel">Cancel</button>
          </div>
        </li>
      </ol>`);
    const input = box.querySelector('[data-code]');
    if (input) input.focus();
  }

  function readCode() {
    const input = box.querySelector('[data-code]');
    const code = input ? input.value.trim() : '';
    if (!/^\d{6}$/.test(code)) {
      notify.error('Enter the six-digit code from your authenticator app.');
      if (input) {
        input.setAttribute('aria-invalid', 'true');
        input.focus();
      }
      return null;
    }
    return code;
  }

  async function act(button) {
    const action = button.dataset.totp;
    if (action === 'cancel') {
      setup = null;
      paint();
      return;
    }
    if (action === 'copy') {
      try {
        await navigator.clipboard.writeText(setup.secret);
        notify.ok('Setup key copied.');
      } catch {
        notify.error('Copy failed. Select the key and copy it manually.');
      }
      return;
    }
    let path;
    let body;
    if (action === 'setup') {
      path = '/api/totp/setup';
    } else {
      const code = readCode();
      if (!code) return;
      path = action === 'enable' ? '/api/totp/enable' : '/api/totp/disable';
      body = action === 'enable' ? { secret: setup.secret, code } : { code };
    }
    button.disabled = true;
    try {
      const res = await api.post(path, body);
      if (action === 'setup') {
        setup = res;
      } else {
        setup = null;
        state = res;
        notify.ok(action === 'enable' ? 'Two-factor sign-in is on. Sign-in now asks for a code after the password.' : 'Two-factor sign-in is off.');
      }
      paint();
    } catch (err) {
      notify.error(err);
      button.disabled = false;
    }
  }

  box.addEventListener('click', (event) => {
    const button = event.target.closest('[data-totp]');
    if (button) act(button);
  });
  // The panel sits inside the settings form: Enter must verify the code, not save settings.
  box.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || !event.target.matches('[data-code]')) return;
    event.preventDefault();
    const button = box.querySelector('[data-totp=enable], [data-totp=disable]');
    if (button) act(button);
  });
  load();
}

function rangeText(f) {
  const hasMin = f.min !== null && f.min !== undefined;
  const hasMax = f.max !== null && f.max !== undefined;
  if (hasMin && hasMax) return `enter a ${f.type === 'int' ? 'whole number' : 'number'} from ${f.min} to ${f.max}.`;
  if (hasMin) return `enter a number of at least ${f.min}.`;
  if (hasMax) return `enter a number no larger than ${f.max}.`;
  return f.type === 'int' ? 'enter a whole number.' : 'enter a number.';
}

function providerTests() {
  return html`<div class="provider-tests">
    ${PROVIDERS.map(
      ([kind, name, desc]) => html`<div class="provider-test">
        <div><strong>${name}</strong><p class="small muted">${desc}</p></div>
        <button type="button" class="btn btn-sm" data-test="${kind}">${icon('link')}Test ${name}</button>
        <div class="test-result" data-test-result="${kind}" aria-live="polite"></div>
      </div>`,
    )}
    <p class="small muted provider-note">Tests use the values on this page, including unsaved changes.</p>
  </div>`;
}

function displayDefault(f) {
  if (f.type === 'bool') return f.default ? 'On' : 'Off';
  if (f.type === 'secret') return 'empty';
  if (f.type === 'select' && Array.isArray(f.options)) {
    const o = f.options.find((x) => x.value === f.default);
    if (o) return o.label;
  }
  return f.default === '' || f.default === null || f.default === undefined ? 'empty' : String(f.default);
}

function fieldRow(f, value, languages, models) {
  const id = `set-${f.key}`;
  const helpId = `${id}-help`;
  const describedBy = [f.help ? helpId : '', `${id}-err`, f.type === 'secret' ? `${id}-secret` : ''].filter(Boolean).join(' ');
  let controlHtml;
  switch (f.type) {
    case 'bool':
      controlHtml = html`<label class="switch"><input type="checkbox" role="switch" id="${id}" data-key="${f.key}" ${value ? 'checked' : ''} aria-describedby="${describedBy}"><span class="small muted">Enabled</span></label>`;
      break;
    case 'int':
    case 'float':
      controlHtml = html`<input class="input num" type="number" id="${id}" data-key="${f.key}" value="${value ?? ''}"
        ${f.min !== null && f.min !== undefined ? html`min="${f.min}"` : ''} ${f.max !== null && f.max !== undefined ? html`max="${f.max}"` : ''}
        step="${f.step ?? (f.type === 'int' ? 1 : 'any')}" inputmode="${f.type === 'int' ? 'numeric' : 'decimal'}" aria-describedby="${describedBy}">`;
      break;
    case 'select':
      controlHtml = html`<select class="select" id="${id}" data-key="${f.key}" aria-describedby="${describedBy}">
        ${(f.options || []).map((o) => html`<option value="${o.value}" ${o.value === value ? 'selected' : ''}>${o.label}</option>`)}
      </select>`;
      break;
    case 'language': {
      const extra = Array.isArray(f.options) ? f.options : [];
      const hasAuto = extra.some((o) => (o.value ?? o) === 'auto');
      controlHtml = html`<select class="select" id="${id}" data-key="${f.key}" aria-describedby="${describedBy}">
        ${extra.length
          ? extra.map((o) => html`<option value="${o.value ?? o}" ${(o.value ?? o) === value ? 'selected' : ''}>${o.label ?? o}</option>`)
          : languageOptions(languages, value, { includeAuto: hasAuto })}
        ${value && value !== 'auto' && !languages.some((l) => l.code === value) && !extra.some((o) => (o.value ?? o) === value) ? html`<option value="${value}" selected>${value}</option>` : ''}
      </select>`;
      break;
    }
    case 'model': {
      const listId = `${id}-models`;
      const installed = (models.installed || []).filter((m) => !f.model_kind || m.kind === f.model_kind);
      controlHtml = html`<input class="input mono" type="text" id="${id}" data-key="${f.key}" value="${value ?? ''}" list="${listId}" spellcheck="false" autocomplete="off" aria-describedby="${describedBy}">
        <datalist id="${listId}">${installed.map((m) => html`<option value="${m.repo_id}"></option>`)}</datalist>`;
      break;
    }
    case 'secret':
      controlHtml = html`<div class="row nowrap-row">
          <input class="input" type="password" id="${id}" data-key="${f.key}" value="" autocomplete="new-password" placeholder="${f.is_set ? '••••••••  (stored)' : 'Not set'}" aria-describedby="${describedBy}">
          <button type="button" class="btn btn-sm" data-clear="${f.key}" ${f.is_set ? '' : 'hidden'}>Clear</button>
        </div>
        <span class="field-help" id="${id}-secret" data-secret-state></span>`;
      break;
    case 'text':
      controlHtml = html`<textarea class="textarea" id="${id}" data-key="${f.key}" rows="3" aria-describedby="${describedBy}">${value ?? ''}</textarea>`;
      break;
    default:
      controlHtml = html`<input class="input" type="text" id="${id}" data-key="${f.key}" value="${value ?? ''}" autocomplete="off" aria-describedby="${describedBy}">`;
  }
  return html`<div class="setting" data-row="${f.key}" data-type="${f.type}">
    <div class="setting-label">
      <label for="${id}">${f.label}</label>
      <span class="changed-tag small">Modified</span>
      ${f.help ? html`<p class="field-help" id="${helpId}">${f.help}</p>` : ''}
    </div>
    <div class="setting-control">
      ${controlHtml}
      <span class="field-error" id="${id}-err" data-error hidden>${f.type === 'int' || f.type === 'float' ? rangeText(f).replace(/^./, (c) => c.toUpperCase()) : 'Invalid value.'}</span>
      <button type="button" class="btn btn-ghost btn-sm reset-btn" data-reset="${f.key}" hidden title="Default: ${displayDefault(f)}">${icon('undo')}Reset to default <span class="muted">(${displayDefault(f)})</span></button>
    </div>
  </div>`;
}

