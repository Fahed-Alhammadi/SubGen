// SubGen web UI bootstrap: shell wiring, theme, worker status and hash router.
import { api } from './api.js';
import { html, render, $, $$ } from './ui/dom.js';
import { icon } from './ui/icons.js';
import { confirmDialog } from './ui/dialog.js';
import { notify } from './ui/toast.js';
import { createPoller } from './ui/poller.js';
import { WORKER_LABELS } from './ui/format.js';
import { onWorker, publishDashboard, publishOffline, workerState } from './store.js';

/* ---------------- Routes ---------------- */

const ROUTES = [
  { pattern: /^\/?$/, nav: 'overview', load: () => import('./views/overview.js') },
  { pattern: /^\/studio\/?$/, nav: 'studio', load: () => import('./views/studio.js') },
  { pattern: /^\/libraries\/?$/, nav: 'libraries', load: () => import('./views/libraries.js') },
  { pattern: /^\/libraries\/(\d+)\/?$/, nav: 'libraries', keys: ['id'], load: () => import('./views/library-detail.js') },
  { pattern: /^\/queue\/?$/, nav: 'queue', load: () => import('./views/queue.js') },
  { pattern: /^\/editor\/(\d+)\/?$/, nav: 'queue', keys: ['jobId'], wide: true, load: () => import('./views/editor.js') },
  { pattern: /^\/models\/?$/, nav: 'models', load: () => import('./views/models.js') },
  { pattern: /^\/settings\/?$/, nav: 'settings', load: () => import('./views/settings.js') },
  { pattern: /^\/system\/?$/, nav: 'system', load: () => import('./views/system.js') },
];

const NAV_ICONS = {
  overview: 'overview',
  studio: 'studio',
  libraries: 'libraries',
  queue: 'queue',
  models: 'models',
  settings: 'settings',
  system: 'system',
};

const page = document.getElementById('page');
let current = null; // {cleanup, guard, hash}
let currentHash = null;
let routeToken = 0;

function parseHash() {
  const rawHash = window.location.hash.replace(/^#/, '') || '/';
  const [path, query = ''] = rawHash.split('?');
  return { path: path.startsWith('/') ? path : `/${path}`, query: new URLSearchParams(query), raw: rawHash };
}

function matchRoute(path) {
  for (const route of ROUTES) {
    const m = route.pattern.exec(path);
    if (m) {
      const params = {};
      (route.keys || []).forEach((key, i) => {
        params[key] = decodeURIComponent(m[i + 1]);
      });
      return { route, params };
    }
  }
  return null;
}

function setNav(nav) {
  $$('[data-nav]').forEach((link) => {
    if (link.dataset.nav === nav) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}

async function guardAllows() {
  if (!current || !current.guard) return true;
  const message = current.guard();
  if (!message) return true;
  return confirmDialog({
    title: 'Leave without saving?',
    message,
    confirmLabel: 'Discard changes',
    cancelLabel: 'Keep editing',
    danger: true,
  });
}

async function onHashChange() {
  const next = parseHash();
  const prevPath = currentHash ? currentHash.split('?')[0] : null;
  // Query-only changes inside the same view are handled by the view itself.
  if (current && prevPath === next.path && current.onQuery) {
    currentHash = next.raw;
    current.onQuery(next.query);
    return;
  }
  if (!(await guardAllows())) {
    // Restore the previous URL without triggering another navigation.
    history.replaceState(null, '', `#${currentHash}`);
    return;
  }
  await mount(next, { focus: true });
}

async function mount(location, { focus = false } = {}) {
  routeToken += 1;
  const token = routeToken;
  if (current && current.cleanup) {
    try {
      current.cleanup();
    } catch (err) {
      console.error(err);
    }
  }
  current = null;
  currentHash = location.raw;

  const found = matchRoute(location.path);
  if (!found) {
    setNav(null);
    page.classList.remove('page-wide');
    render(
      page,
      html`<div class="empty">
        <span class="empty-icon">${icon('alert')}</span>
        <h1>Page not found</h1>
        <p>There is nothing at this address.</p>
        <a class="btn btn-primary" href="#/">Go to overview</a>
      </div>`,
    );
    document.title = 'Not found – SubGen';
    return;
  }

  setNav(found.route.nav);
  page.classList.toggle('page-wide', Boolean(found.route.wide));
  let mod;
  try {
    mod = await found.route.load();
  } catch (err) {
    console.error(err);
    render(page, html`<div class="empty" data-tone="error"><span class="empty-icon">${icon('alert')}</span><h1>This page failed to load</h1><p>Reload the browser tab to try again.</p></div>`);
    return;
  }
  if (token !== routeToken) return;

  page.textContent = '';
  const ctx = {
    root: page,
    params: found.params,
    query: location.query,
    setTitle(title) {
      document.title = title ? `${title} – SubGen` : 'SubGen';
    },
    setGuard(fn) {
      if (current) current.guard = fn;
    },
    onQuery(fn) {
      if (current) current.onQuery = fn;
    },
    /** Update the query string without re-mounting the view. */
    replaceQuery(params) {
      const search = new URLSearchParams();
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '') search.set(k, String(v));
      });
      const s = search.toString();
      const nextRaw = `${location.path}${s ? `?${s}` : ''}`;
      currentHash = nextRaw;
      history.replaceState(null, '', `#${nextRaw}`);
    },
    isCurrent: () => token === routeToken,
    /** Re-mount the current view (used by "Try again" buttons). */
    reload: () => mount(parseHash()),
  };
  current = { cleanup: null, guard: null, onQuery: null };
  try {
    const cleanup = await mod.mount(ctx);
    if (token !== routeToken) {
      if (typeof cleanup === 'function') cleanup();
      return;
    }
    current.cleanup = typeof cleanup === 'function' ? cleanup : null;
  } catch (err) {
    console.error(err);
    notify.error(err);
  }
  if (focus) page.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

/** Navigate programmatically (respects the leave guard). */
export function navigate(hash) {
  const target = hash.startsWith('#') ? hash : `#${hash}`;
  if (window.location.hash === target) return;
  window.location.hash = target;
}

/* ---------------- Theme ---------------- */

function effectiveTheme() {
  const explicit = document.documentElement.getAttribute('data-theme');
  if (explicit) return explicit;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function paintThemeButtons() {
  const theme = effectiveTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  $$('[data-theme-toggle]').forEach((btn) => {
    render(btn, html`${icon(theme === 'dark' ? 'sun' : 'moon')}`);
    btn.setAttribute('aria-label', `Switch to ${next} theme`);
    btn.title = `Switch to ${next} theme`;
  });
}

function toggleTheme() {
  const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try {
    window.localStorage.setItem('subgen-theme', next);
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
  paintThemeButtons();
}

/* ---------------- Worker status ---------------- */

const WORKER_ICON = { idle: 'check', loading: 'hourglass', processing: 'loader', error: 'alert', offline: 'alert', unknown: 'circle' };

function paintWorker(state) {
  const w = state.worker;
  const s = state.offline ? 'offline' : w ? w.state : 'unknown';
  let label = WORKER_LABELS[s] || 'Checking…';
  if (s === 'processing' && state.jobs) label = `Processing, ${state.jobs.queued || 0} queued`;
  if (s === 'error' && w && w.message) label = 'Worker error';
  $$('[data-worker-pill]').forEach((pill) => {
    pill.dataset.state = s;
    render(pill, html`${icon(WORKER_ICON[s] || 'circle', { cls: s === 'processing' ? 'spin' : '' })}<span class="label"></span>`);
    pill.querySelector('.label').textContent = label;
    pill.title = w && w.model ? `Worker: ${label}. Model ${w.model} on ${w.device || 'unknown device'}` : `Worker: ${label}`;
    pill.setAttribute('aria-label', `Worker status: ${label}`);
  });
  const count = state.jobs ? (state.jobs.queued || 0) + (state.jobs.processing || 0) : 0;
  const badge = $('[data-queue-count]');
  if (badge) {
    badge.hidden = !count;
    badge.textContent = String(count);
    badge.setAttribute('aria-label', `${count} active jobs`);
  }
}

const shellPoller = createPoller(async () => {
  // Skip when a view refreshed the dashboard recently.
  if (Date.now() - workerState.at < 8000) return;
  try {
    publishDashboard(await api.get('/api/dashboard'));
  } catch (err) {
    if (err.status !== 401) publishOffline();
  }
}, 10000);

/* ---------------- Boot ---------------- */

function paintNavIcons() {
  $$('[data-nav]').forEach((link) => {
    const placeholder = link.querySelector('svg.icon');
    if (!placeholder) return;
    const tpl = document.createElement('template');
    tpl.innerHTML = icon(NAV_ICONS[link.dataset.nav]).value;
    placeholder.replaceWith(tpl.content);
  });
  $$('[data-logout]').forEach((btn) => render(btn, html`${icon('logout')}`));
}

async function logout(button) {
  if (!(await guardAllows())) return;
  if (current) current.guard = null; // already confirmed; don't prompt again on unload
  button.disabled = true;
  try {
    await api.post('/api/logout');
  } catch (err) {
    if (err.status !== 401) {
      notify.error(err);
      button.disabled = false;
      return;
    }
  }
  window.location.assign('/login');
}

async function loadSession() {
  try {
    const session = await api.get('/api/session');
    const user = $('[data-user]');
    if (user && session.user) {
      user.textContent = session.user;
      user.title = `Signed in as ${session.user}`;
    }
    const version = $('[data-version]');
    if (version && session.version) version.textContent = `v${session.version}`;
  } catch {
    /* api() already redirects on 401 */
  }
}

function boot() {
  paintNavIcons();
  paintThemeButtons();
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', paintThemeButtons);
  $$('[data-theme-toggle]').forEach((btn) => btn.addEventListener('click', toggleTheme));
  $$('[data-logout]').forEach((btn) => btn.addEventListener('click', () => logout(btn)));
  // The skip link must not change the hash: the router would treat '#page' as a route.
  $$('.skip-link').forEach((link) => link.addEventListener('click', (event) => {
    event.preventDefault();
    const target = $('#page');
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    target.focus();
  }));

  window.addEventListener('beforeunload', (event) => {
    if (current && current.guard && current.guard()) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  window.addEventListener('hashchange', onHashChange);

  onWorker(paintWorker);
  paintWorker(workerState);
  shellPoller.start();
  loadSession();
  mount(parseHash());
}

boot();
