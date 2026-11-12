// DOM helpers and a single escaping `html` tagged template.
//
// Every interpolated value is HTML-escaped unless it is a SafeHtml produced by
// `html` itself or explicitly wrapped with `raw()` (only for trusted, static
// markup such as icons). Arrays are flattened and each entry escaped.
// Never put `style="…"` in templates — the CSP blocks it. Use `data-value`
// (read by `hydrate`) or set properties from JS.

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' };

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"'`]/g, (ch) => ESCAPES[ch]);
}

class SafeHtml {
  constructor(value) {
    this.value = value;
  }
  toString() {
    return this.value;
  }
}

export function raw(value) {
  return new SafeHtml(String(value ?? ''));
}

function serialize(value) {
  if (value === null || value === undefined || value === false) return '';
  if (value instanceof SafeHtml) return value.value;
  if (Array.isArray(value)) return value.map(serialize).join('');
  return escapeHtml(value);
}

export function html(strings, ...values) {
  let out = strings[0];
  for (let i = 0; i < values.length; i += 1) {
    out += serialize(values[i]) + strings[i + 1];
  }
  return new SafeHtml(out);
}

/** Replace an element's content with SafeHtml and hydrate dynamic values. */
export function render(el, content) {
  if (!(content instanceof SafeHtml) && typeof content !== 'undefined' && content !== null) {
    throw new TypeError('render() expects html`` output');
  }
  el.innerHTML = content ? content.value : '';
  hydrate(el);
  return el;
}

/** Build a single element from SafeHtml. */
export function fragment(content) {
  const tpl = document.createElement('template');
  tpl.innerHTML = content.value;
  hydrate(tpl.content);
  return tpl.content;
}

export function element(content) {
  const frag = fragment(content);
  return frag.firstElementChild;
}

/**
 * Apply CSP-safe dynamic styles. Elements with `data-value="42"` receive the
 * custom property `--value: 42` (used by progress tracks and meters).
 */
export function hydrate(root) {
  root.querySelectorAll('[data-value]').forEach((el) => {
    const v = Number(el.dataset.value);
    el.style.setProperty('--value', String(Number.isFinite(v) ? Math.max(0, Math.min(100, v)) : 0));
  });
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

/**
 * Keyed list patching. Each item is rendered to SafeHtml; a row is only
 * replaced when its markup changed. Focus inside a replaced row is restored to
 * the element with the same data-action (and data-id) so polling never steals
 * keyboard focus.
 */
export function patchList(container, items, keyOf, renderItem) {
  const existing = new Map();
  for (const child of Array.from(container.children)) {
    if (child.dataset.key !== undefined) existing.set(child.dataset.key, child);
    else child.remove();
  }
  let previous = null;
  for (const item of items) {
    const key = String(keyOf(item));
    const markup = renderItem(item).value;
    let el = existing.get(key);
    if (el && el._markup !== markup) {
      const restore = focusDescriptor(el);
      const next = elementFromMarkup(markup, container);
      next.dataset.key = key;
      next._markup = markup;
      el.replaceWith(next);
      el = next;
      if (restore) restoreFocus(el, restore);
    } else if (!el) {
      el = elementFromMarkup(markup, container);
      el.dataset.key = key;
      el._markup = markup;
    }
    existing.delete(key);
    const expectedPosition = previous ? previous.nextElementSibling : container.firstElementChild;
    if (expectedPosition !== el) {
      if (previous) previous.after(el);
      else container.prepend(el);
    }
    previous = el;
  }
  existing.forEach((el) => el.remove());
}

function elementFromMarkup(markup, container) {
  // <tr> must be parsed in a table context.
  const tag = container.tagName;
  if (tag === 'TBODY' || tag === 'THEAD' || tag === 'TABLE') {
    const table = document.createElement('table');
    table.innerHTML = `<tbody>${markup}</tbody>`;
    const row = table.querySelector('tr');
    hydrate(table);
    return row;
  }
  const tpl = document.createElement('template');
  tpl.innerHTML = markup;
  hydrate(tpl.content);
  return tpl.content.firstElementChild;
}

function focusDescriptor(row) {
  const active = document.activeElement;
  if (!active || !row.contains(active)) return null;
  return { action: active.dataset.action, id: active.dataset.id, name: active.getAttribute('name') };
}

function restoreFocus(row, d) {
  let sel = null;
  if (d.action) sel = `[data-action="${CSS.escape(d.action)}"]${d.id ? `[data-id="${CSS.escape(d.id)}"]` : ''}`;
  else if (d.name) sel = `[name="${CSS.escape(d.name)}"]`;
  const target = sel ? row.querySelector(sel) : null;
  if (target && !target.disabled) target.focus({ preventScroll: true });
}

/** Delegate clicks on [data-action] within root. Handler map: action -> fn(el, event). */
export function delegate(root, eventName, handlers) {
  const listener = (event) => {
    const target = event.target.closest('[data-action]');
    if (!target || !root.contains(target)) return;
    const fn = handlers[target.dataset.action];
    if (!fn) return;
    if (target.tagName === 'A' && eventName === 'click') event.preventDefault();
    fn(target, event);
  };
  root.addEventListener(eventName, listener);
  return () => root.removeEventListener(eventName, listener);
}

export function debounce(fn, wait = 250) {
  let timer;
  const debounced = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
  debounced.cancel = () => clearTimeout(timer);
  return debounced;
}

/** Disable a button while an async action runs. */
export async function busy(button, fn) {
  if (!button) return fn();
  if (button.dataset.busy === '1') return undefined;
  button.dataset.busy = '1';
  button.setAttribute('aria-busy', 'true');
  const wasDisabled = button.disabled;
  button.disabled = true;
  try {
    return await fn();
  } finally {
    delete button.dataset.busy;
    button.removeAttribute('aria-busy');
    if (button.isConnected) button.disabled = wasDisabled;
  }
}
