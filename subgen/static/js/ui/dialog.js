// Native <dialog> helpers: modal dialogs with focus restore, and an
// accessible confirm() replacement.
import { html, render, fragment } from './dom.js';
import { icon } from './icons.js';

let counter = 0;

/**
 * openDialog({title, body, footer, size, onClose, initialFocus}) →
 *   {el, body, foot, close(value), closed: Promise<value>}
 * `body` / `footer` may be SafeHtml or DOM nodes. A form inside the dialog is
 * the caller's responsibility.
 */
export function openDialog({ title, body, footer, size = '', labelledBy, initialFocus } = {}) {
  counter += 1;
  const id = `dlg-${counter}`;
  const opener = document.activeElement;
  const el = document.createElement('dialog');
  el.className = `dialog ${size}`.trim();
  el.setAttribute('aria-labelledby', labelledBy || `${id}-title`);
  render(
    el,
    html`
      <div class="dialog-head">
        <h2 id="${id}-title"></h2>
        <button type="button" class="btn btn-ghost btn-icon" data-dialog-close aria-label="Close dialog">${icon('x')}</button>
      </div>
      <div class="dialog-body"></div>
      <div class="dialog-foot" hidden></div>
      <div class="toasts dialog-toasts"></div>
    `,
  );
  el.querySelector('h2').textContent = title || '';
  const bodyEl = el.querySelector('.dialog-body');
  const footEl = el.querySelector('.dialog-foot');
  place(bodyEl, body);
  if (footer) {
    place(footEl, footer);
    footEl.hidden = false;
  }

  let resolveClosed;
  const closed = new Promise((resolve) => {
    resolveClosed = resolve;
  });
  let result;
  const close = (value) => {
    result = value;
    if (el.open) el.close();
  };
  el.addEventListener('click', (event) => {
    if (event.target.closest('[data-dialog-close]')) close(undefined);
  });
  el.addEventListener('close', () => {
    el.remove();
    if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus({ preventScroll: true });
    resolveClosed(result);
  });

  document.body.append(el);
  el.showModal();
  const focusTarget =
    (initialFocus && el.querySelector(initialFocus)) ||
    el.querySelector('[autofocus]') ||
    bodyEl.querySelector('input:not([type=hidden]):not([disabled]), select, textarea, button:not([disabled])');
  if (focusTarget) focusTarget.focus();
  return { el, body: bodyEl, foot: footEl, close, closed };
}

function place(target, content) {
  if (!content) return;
  if (content instanceof Node) target.append(content);
  else target.append(fragment(content));
}

/**
 * confirmDialog({title, message, confirmLabel, cancelLabel, danger}) → Promise<boolean>
 */
export function confirmDialog({ title = 'Are you sure?', message = '', detail = '', confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false } = {}) {
  const dlg = openDialog({
    title,
    size: 'narrow',
    body: html`<p class="confirm-message"></p>${detail ? html`<p class="confirm-detail muted small"></p>` : ''}`,
    footer: html`
      <button type="button" class="btn" data-dialog-close>${cancelLabel}</button>
      <button type="button" class="btn ${danger ? 'btn-danger-solid' : 'btn-primary'}" data-confirm>${confirmLabel}</button>
    `,
    initialFocus: danger ? '[data-dialog-close].btn:not(.btn-icon)' : '[data-confirm]',
  });
  dlg.body.querySelector('.confirm-message').textContent = message;
  if (detail) dlg.body.querySelector('.confirm-detail').textContent = detail;
  dlg.foot.querySelector('[data-confirm]').addEventListener('click', () => dlg.close(true));
  return dlg.closed.then((v) => v === true);
}
