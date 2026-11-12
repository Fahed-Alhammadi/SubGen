// Searchable language multi-select built from checkboxes inside a fieldset.
//
//   const picker = createLangPicker({ languages, selected: ['ar'], label: 'Target languages' });
//   form.append(picker.el); picker.value → ['ar', ...]
import { html, element, escapeHtml } from './dom.js';
import { icon } from './icons.js';

let uid = 0;

export function createLangPicker({ languages, selected = [], label = 'Languages', help = '', name = 'languages', filter = null, onChange = null }) {
  uid += 1;
  const id = `lp-${uid}`;
  const list = filter ? languages.filter(filter) : languages.slice();
  const chosen = new Set(selected);
  // Keep unknown codes that are already configured so they are not silently dropped.
  for (const code of chosen) {
    if (!list.some((l) => l.code === code)) list.unshift({ code, name: code.toUpperCase(), translation: true, whisper: true });
  }

  const el = element(html`
    <fieldset class="fieldset" aria-describedby="${help ? `${id}-help` : ''}">
      <legend>${label}</legend>
      <div class="lang-picker">
        <div class="selected" aria-label="Selected languages"></div>
        <div class="input-group">
          ${icon('search')}
          <input class="input" type="search" id="${id}-q" placeholder="Search languages" aria-label="Search languages" autocomplete="off">
        </div>
        <div class="options" role="group" aria-label="${label}"></div>
      </div>
      ${help ? html`<p class="field-help" id="${id}-help">${help}</p>` : ''}
    </fieldset>`);

  const selectedEl = el.querySelector('.selected');
  const optionsEl = el.querySelector('.options');
  const search = el.querySelector('input[type=search]');

  // Build all option rows once; filtering only toggles `hidden`.
  optionsEl.innerHTML = list
    .map(
      (lang) => `<label class="lang-option" data-code="${escapeHtml(lang.code)}" data-search="${escapeHtml(`${lang.name} ${lang.code}`.toLowerCase())}">
        <input type="checkbox" name="${escapeHtml(name)}" value="${escapeHtml(lang.code)}" ${chosen.has(lang.code) ? 'checked' : ''}>
        <span>${escapeHtml(lang.name)}</span><span class="code">${escapeHtml(lang.code)}</span>
        ${lang.translation === false ? '<span class="note">transcription only</span>' : ''}
      </label>`,
    )
    .join('');

  function renderSelected() {
    selectedEl.textContent = '';
    if (!chosen.size) {
      const note = document.createElement('span');
      note.className = 'empty-note';
      note.textContent = 'None selected';
      selectedEl.append(note);
      return;
    }
    for (const code of chosen) {
      const lang = list.find((l) => l.code === code);
      const btn = element(html`<button type="button" class="lang-token" data-code="${code}" aria-label="Remove ${lang ? lang.name : code}">${lang ? lang.name : code}${icon('x')}</button>`);
      selectedEl.append(btn);
    }
  }

  optionsEl.addEventListener('change', (event) => {
    const box = event.target;
    if (box.type !== 'checkbox') return;
    if (box.checked) chosen.add(box.value);
    else chosen.delete(box.value);
    renderSelected();
    if (onChange) onChange([...chosen]);
  });
  selectedEl.addEventListener('click', (event) => {
    const btn = event.target.closest('.lang-token');
    if (!btn) return;
    const code = btn.dataset.code;
    chosen.delete(code);
    const box = optionsEl.querySelector(`input[value="${CSS.escape(code)}"]`);
    if (box) box.checked = false;
    renderSelected();
    search.focus();
    if (onChange) onChange([...chosen]);
  });
  search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    for (const row of optionsEl.children) row.hidden = q ? !row.dataset.search.includes(q) : false;
  });
  search.addEventListener('keydown', (event) => {
    // Enter picks the first visible match instead of submitting the form.
    if (event.key !== 'Enter') return;
    event.preventDefault();
    const first = Array.from(optionsEl.children).find((row) => !row.hidden);
    if (first) {
      const box = first.querySelector('input');
      box.checked = !box.checked;
      box.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });

  renderSelected();
  return {
    el,
    get value() {
      return [...chosen];
    },
  };
}

/** <option> markup for a single-language <select>. */
export function languageOptions(languages, selected, { includeAuto = false, autoLabel = 'Detect automatically' } = {}) {
  const opts = [];
  if (includeAuto) opts.push(html`<option value="auto" ${selected === 'auto' ? 'selected' : ''}>${autoLabel}</option>`);
  for (const lang of languages) {
    opts.push(html`<option value="${lang.code}" ${selected === lang.code ? 'selected' : ''}>${lang.name} (${lang.code})</option>`);
  }
  return opts;
}
