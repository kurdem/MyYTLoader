// Hilfsfunktionen: Escaping, Formatierung, Toasts, Icons

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

/** Tagged Template: alle Werte werden HTML-escaped, außer sie sind mit raw() markiert. */
export function html(strings, ...values) {
  let out = '';
  strings.forEach((s, i) => {
    out += s;
    if (i < values.length) {
      const v = values[i];
      if (v && v.__raw) out += v.value;
      else if (Array.isArray(v)) out += v.map((x) => (x && x.__raw ? x.value : esc(x))).join('');
      else out += esc(v);
    }
  });
  return raw(out);
}

export function raw(value) {
  return { __raw: true, value: String(value ?? ''), toString() { return this.value; } };
}

export function fmtBytes(bytes) {
  if (!bytes && bytes !== 0) return '–';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let n = Number(bytes);
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toLocaleString('de-DE', { maximumFractionDigits: i >= 2 ? 1 : 0 })} ${units[i]}`;
}

export function fmtDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '–';
  const s = Math.round(Number(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (x) => String(x).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

export function fmtDate(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

export function fmtDateTime(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function fmtSpeed(bps) {
  return bps ? `${fmtBytes(bps)}/s` : '';
}

export function fmtEta(sec) {
  if (sec == null) return '';
  if (sec < 60) return `noch ${sec}s`;
  return `noch ${Math.ceil(sec / 60)} min`;
}

export function toast(message, kind = 'ok', ms = 4000) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast toast--${kind}`;
  el.setAttribute('role', kind === 'err' ? 'alert' : 'status');
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export async function busy(button, fn) {
  if (!button) return fn();
  button.disabled = true;
  button.classList.add('is-busy');
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.classList.remove('is-busy');
  }
}

/** Fortschrittsbalken: CSP verbietet Inline-Styles im Markup, daher per CSSOM setzen. */
export function applyBars(root = document) {
  root.querySelectorAll('[data-progress]').forEach((el) => {
    el.style.width = `${Math.max(0, Math.min(100, Number(el.dataset.progress) * 100)).toFixed(1)}%`;
  });
}

export const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

export const isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('In die Zwischenablage kopiert.');
  } catch {
    toast('Kopieren nicht möglich – bitte manuell markieren.', 'err');
  }
}

export const icons = {
  download: raw('<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14"/></svg>'),
  share: raw('<svg viewBox="0 0 24 24"><path d="M12 3v12M8 7l4-4 4 4M6 11H5v9h14v-9h-1"/></svg>'),
  trash: raw('<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>'),
  retry: raw('<svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2.3-5.6M4 4v4h4"/></svg>'),
  stop: raw('<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'),
  edit: raw('<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16zM14 6l4 4"/></svg>'),
  play: raw('<svg viewBox="0 0 24 24"><path d="M7 4v16l13-8z"/></svg>'),
  music: raw('<svg viewBox="0 0 24 24"><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg>'),
  film: raw('<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18M3 15h18M8 5v14M16 5v14"/></svg>'),
  paste: raw('<svg viewBox="0 0 24 24"><path d="M9 4h6v3H9zM7 5H5v15h14V5h-2"/></svg>'),
  copy: raw('<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/></svg>'),
  back: raw('<svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg>'),
  rss: raw('<svg viewBox="0 0 24 24"><path d="M5 19h.01M5 12a7 7 0 0 1 7 7M5 5a14 14 0 0 1 14 14"/></svg>'),
  inbox: raw('<svg viewBox="0 0 24 24"><path d="M4 13h4l2 3h4l2-3h4M4 13l3-8h10l3 8v6H4z"/></svg>'),
  plus: raw('<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>'),
  up: raw('<svg viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></svg>'),
  down: raw('<svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>'),
  close: raw('<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>'),
  user: raw('<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>'),
  logout: raw('<svg viewBox="0 0 24 24"><path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10"/></svg>'),
  vlc: raw('<svg viewBox="0 0 24 24"><path d="M12 3 5 20h14zM8 13h8"/></svg>'),
};

/** Abo-Buttons für Podcast-Apps (Apple Podcasts, Overcast, Pocket Casts) plus Kopieren. */
export function subscribeBlock(url, label) {
  return html`<div class="stack">
    ${label ? html`<b class="small">${label}</b>` : ''}
    <div class="copy-field">
      <input class="input" readonly value="${url}" aria-label="Feed-URL ${label || ''}">
      <button class="btn icon-btn" type="button" data-copy="${url}" title="Kopieren">${icons.copy}</button>
    </div>
    <div class="row">
      <a class="btn btn--sm" href="${url.replace(/^https?:\/\//, 'podcast://')}">Apple Podcasts</a>
      <a class="btn btn--sm" href="overcast://x-callback-url/add?url=${encodeURIComponent(url)}">Overcast</a>
      <a class="btn btn--sm" href="pktc://subscribe/${url.replace(/^https?:\/\//, '')}">Pocket Casts</a>
    </div>
  </div>`;
}

export function bindCopy(root) {
  root.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', () => copyText(b.dataset.copy)));
}

/** Bilder, die nicht laden (z. B. Feed ohne Cover), durch Platzhalter ersetzen – ohne Inline-Handler (CSP). */
export function fallbackImages(root) {
  root.querySelectorAll('img[data-fallback]').forEach((img) => {
    const swap = () => { img.replaceWith(Object.assign(document.createElement('div'), { className: 'placeholder', innerHTML: icons.rss.value })); };
    if (img.complete && img.naturalWidth === 0) swap(); else img.addEventListener('error', swap, { once: true });
  });
}

export function fmtRelative(iso) {
  if (!iso) return '–';
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const unit = abs < 3600 ? [Math.round(abs / 60), 'Min.'] : abs < 172800 ? [Math.round(abs / 3600), 'Std.'] : [Math.round(abs / 86400), 'Tagen'];
  if (abs < 60) return diff < 0 ? 'gerade eben' : 'gleich';
  return diff < 0 ? `vor ${unit[0]} ${unit[1]}` : `in ${unit[0]} ${unit[1]}`;
}

