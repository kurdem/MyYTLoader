// Einstieg: Login, Router, globaler Zustand, Live-Updates
import { api, connectEvents, setUnauthorizedHandler } from './api.js';
import { toast } from './util.js';
import { StartView } from './views/start.js';
import { JobsView } from './views/jobs.js';
import { LibraryView } from './views/library.js';
import { MediaView } from './views/media.js';
import { MoreView } from './views/more.js';
import { UsersView } from './views/users.js';

export const isAdmin = () => !!(store.me && store.me.user && store.me.user.role === 'admin');
export const myId = () => (store.me && store.me.user ? store.me.user.id : null);

export const store = {
  me: null,
  system: null,
  jobs: new Map(),
  listeners: new Set(),
};

const routes = [
  { pattern: /^\/?$/, nav: 'start', view: StartView },
  { pattern: /^\/jobs$/, nav: 'jobs', view: JobsView },
  { pattern: /^\/library$/, nav: 'library', view: LibraryView },
  { pattern: /^\/media\/([a-f0-9]{32})$/, nav: 'library', view: MediaView },
  { pattern: /^\/more$/, nav: 'more', view: MoreView },
  { pattern: /^\/users$/, nav: 'more', view: UsersView },
];

let current = null;
let disconnect = null;
let routerBound = false;

function updateBadge() {
  const active = [...store.jobs.values()].filter((j) => ['queued', 'running', 'processing'].includes(j.status)).length;
  const badge = document.getElementById('active-badge');
  badge.textContent = String(active);
  badge.hidden = active === 0;
}

export function upsertJob(job) {
  store.jobs.set(job.id, job);
  updateBadge();
}

export async function refreshJobs() {
  const jobs = await api('/api/jobs?status=all');
  store.jobs = new Map(jobs.map((j) => [j.id, j]));
  updateBadge();
}

function handleEvent(evt) {
  if (evt.type === 'job') {
    const prev = store.jobs.get(evt.job.id);
    upsertJob(evt.job);
    if (prev && prev.status !== 'done' && evt.job.status === 'done') toast(`Fertig: ${evt.job.title || 'Download'}`);
    if (prev && prev.status !== 'failed' && evt.job.status === 'failed') toast(`Fehlgeschlagen: ${evt.job.title || 'Download'}`, 'err');
  } else if (evt.type === 'job_deleted') {
    store.jobs.delete(evt.id);
    updateBadge();
  } else if (evt.type === 'jobs_cleared') {
    refreshJobs().then(() => current && current.onEvent && current.onEvent({ type: 'resync' })).catch(() => {});
    return;
  }
  if (current && current.onEvent) current.onEvent(evt);
}

async function route() {
  const hash = location.hash.replace(/^#/, '') || '/';
  const [path] = hash.split('?');
  const match = routes.find((r) => r.pattern.test(path)) || routes[0];
  const params = path.match(match.pattern) || [];
  document.querySelectorAll('[data-nav]').forEach((a) => a.classList.toggle('is-active', a.dataset.nav === match.nav));
  if (current && current.destroy) current.destroy();
  const el = document.getElementById('view');
  el.innerHTML = '';
  current = new match.view(el, params.slice(1), new URLSearchParams(hash.split('?')[1] || ''));
  try {
    await current.render();
  } catch (err) {
    el.innerHTML = '';
    const p = document.createElement('div');
    p.className = 'notice notice--err';
    p.textContent = err.message || 'Fehler beim Laden.';
    el.appendChild(p);
  }
  el.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

function showLogin() {
  if (disconnect) { disconnect(); disconnect = null; }
  document.getElementById('app').hidden = true;
  document.getElementById('login').hidden = false;
  document.querySelector('#login-form input[name=username]').focus();
}

async function startApp() {
  document.getElementById('login').hidden = true;
  document.getElementById('app').hidden = false;
  try {
    store.system = await api('/api/system');
  } catch {
    store.system = null;
  }
  await refreshJobs().catch(() => {});
  disconnect = connectEvents({
    onEvent: handleEvent,
    onOpen: () => refreshJobs().then(() => current && current.onEvent && current.onEvent({ type: 'resync' })).catch(() => {}),
  });
  if (!routerBound) {
    window.addEventListener('hashchange', route);
    routerBound = true;
  }
  route();
}

// Geteilte Links (PWA share_target, iOS-Kurzbefehl: /?url=…) auf die Startseite umleiten
function adoptSharedUrl() {
  const q = new URLSearchParams(location.search);
  const candidate = q.get('url') || ((q.get('text') || '').match(/https?:\/\/\S+/) || [])[0];
  if (candidate) {
    history.replaceState(null, '', `${location.pathname}#/?url=${encodeURIComponent(candidate)}`);
  }
}

async function boot() {
  adoptSharedUrl();
  setUnauthorizedHandler(showLogin);
  document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const btn = form.querySelector('button');
    const err = document.getElementById('login-error');
    err.textContent = '';
    btn.disabled = true;
    try {
      await api('/api/auth/login', { method: 'POST', body: { username: form.username.value, password: form.password.value } });
      form.password.value = '';
      store.me = await api('/api/auth/me');
      startApp();
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      btn.disabled = false;
    }
  });

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }

  try {
    store.me = await api('/api/auth/me');
  } catch {
    store.me = { authenticated: false, auth_enabled: true };
  }
  if (store.me.authenticated) startApp();
  else showLogin();
}

export async function logout() {
  await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
  location.hash = '#/';
  showLogin();
}

boot();
