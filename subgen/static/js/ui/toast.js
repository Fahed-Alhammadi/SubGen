// Toast notifications. Visible toasts are not a live region themselves; the
// message is mirrored into a visually hidden polite region (or an assertive
// one for errors) so screen readers announce it exactly once.
import { html, element } from './dom.js';
import { icon } from './icons.js';

const TONE_ICON = { ok: 'check-circle', error: 'x-circle', info: 'info', warn: 'alert' };

function container() {
  // A modal dialog sits in the top layer, so toasts must render inside it to be visible.
  const inDialog = document.querySelectorAll('dialog[open] .dialog-toasts');
  return inDialog.length ? inDialog[inDialog.length - 1] : document.getElementById('toasts');
}

export function toast(message, { tone = 'ok', timeout } = {}) {
  const host = container();
  if (!host) return;
  const ms = timeout ?? (tone === 'error' ? 8000 : 4000);
  const el = element(html`
    <div class="toast" data-tone="${tone}">
      ${icon(TONE_ICON[tone] || 'info')}
      <div class="toast-body"></div>
      <button type="button" class="btn btn-ghost btn-icon btn-sm" aria-label="Dismiss notification">${icon('x')}</button>
    </div>`);
  el.querySelector('.toast-body').textContent = String(message);
  const remove = () => el.remove();
  el.querySelector('button').addEventListener('click', remove);
  host.append(el);
  while (host.children.length > 4) host.firstElementChild.remove();
  const region = document.getElementById(tone === 'error' ? 'alerts' : 'announcer');
  if (region) {
    region.textContent = '';
    setTimeout(() => {
      region.textContent = String(message);
    }, 50);
  }
  let timer = setTimeout(remove, ms);
  el.addEventListener('mouseenter', () => clearTimeout(timer));
  el.addEventListener('mouseleave', () => {
    timer = setTimeout(remove, 2000);
  });
}

export const notify = {
  ok: (m) => toast(m, { tone: 'ok' }),
  info: (m) => toast(m, { tone: 'info' }),
  warn: (m) => toast(m, { tone: 'warn' }),
  error: (m) => toast(m instanceof Error ? m.message : m, { tone: 'error' }),
};

/** Run an async action and surface errors as a toast. Returns undefined on failure. */
export async function attempt(fn) {
  try {
    return await fn();
  } catch (err) {
    if (err && err.name === 'AbortError') return undefined;
    if (err && err.status === 401) return undefined;
    notify.error(err);
    return undefined;
  }
}
