// Medienbibliothek: Suche, Filter, Sortierung, Kartenansicht
import { api } from '../api.js';
import { isAdmin, myId } from '../app.js';
import { fmtBytes, fmtDate, fmtDuration, helpLink, html, icons } from '../util.js';

const PREF_KEY = 'ytl:library';

const DEFAULTS = { kind: 'all', sort: 'created:desc', collection: '', owner: 'me' };

function loadPrefs() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(PREF_KEY) || '{}') }; } catch { return { ...DEFAULTS }; }
}

export class LibraryView {
  constructor(el) {
    this.el = el;
    this.prefs = loadPrefs();
    this.q = '';
    this.timer = null;
    this.reqId = 0;
  }

  savePrefs() {
    try { localStorage.setItem(PREF_KEY, JSON.stringify(this.prefs)); } catch { /* ignore */ }
  }

  async render() {
    const p = this.prefs;
    this.el.innerHTML = String(html`
      <header class="page-head">
        <div><h1>Bibliothek</h1><p id="summary">&nbsp;</p></div>
        ${helpLink('library')}
      </header>
      <div class="toolbar">
        <div class="toolbar__row">
          <input class="input" id="q" type="search" placeholder="Suchen nach Titel, Kanal, Ordner …" autocomplete="off" aria-label="Suche">
          <select class="input" id="sort" aria-label="Sortierung">
            <option value="created:desc">Neueste zuerst</option>
            <option value="created:asc">Älteste zuerst</option>
            <option value="title:asc">Titel A–Z</option>
            <option value="artist:asc">Kanal A–Z</option>
            <option value="duration:desc">Längste zuerst</option>
            <option value="size:desc">Größte zuerst</option>
          </select>
        </div>
        <div class="toolbar__row">
          <div class="chips grow" role="group" aria-label="Typ">
            <button class="chip" data-kind="all">Alle</button>
            <button class="chip" data-kind="audio">Audio</button>
            <button class="chip" data-kind="video">Video</button>
          </div>
          <select class="input" id="collection" aria-label="Ordner"><option value="">Alle Ordner</option></select>
          <select class="input" id="owner" aria-label="Benutzer" hidden></select>
        </div>
      </div>
      <div id="items"></div>
    `);
    this.el.querySelector('#sort').value = p.sort;
    const q = this.el.querySelector('#q');
    q.addEventListener('input', () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => { this.q = q.value.trim(); this.load(); }, 250);
    });
    this.el.querySelector('#sort').addEventListener('change', (e) => { p.sort = e.target.value; this.savePrefs(); this.load(); });
    this.el.querySelector('#collection').addEventListener('change', (e) => { p.collection = e.target.value; this.savePrefs(); this.load(); });
    this.el.querySelectorAll('[data-kind]').forEach((b) => b.addEventListener('click', () => {
      p.kind = b.dataset.kind; this.savePrefs(); this.load();
    }));
    const ownerSel = this.el.querySelector('#owner');
    ownerSel.addEventListener('change', (e) => {
      p.owner = e.target.value; p.collection = ''; this.savePrefs();
      this.loadCollections().then(() => this.load());
    });
    if (!isAdmin()) p.owner = 'me';
    else await this.loadOwners();
    await Promise.all([this.loadCollections(), this.load()]);
  }

  // Admins: eigene Bibliothek, alle oder die eines bestimmten Benutzers anzeigen
  async loadOwners() {
    const { users } = await api('/api/users');
    const sel = this.el.querySelector('#owner');
    sel.innerHTML = String(html`<option value="me">Meine Bibliothek</option><option value="all">Alle Benutzer</option>
      ${users.filter((u) => u.id !== myId()).map((u) => html`<option value="${u.id}">${u.username}</option>`)}`);
    sel.hidden = users.length < 2;
    if (![...sel.options].some((o) => o.value === this.prefs.owner)) this.prefs.owner = 'me';
    sel.value = this.prefs.owner;
  }

  async loadCollections() {
    const cols = await api(`/api/media/collections?owner=${encodeURIComponent(this.prefs.owner)}`);
    const sel = this.el.querySelector('#collection');
    sel.innerHTML = String(html`<option value="">Alle Ordner</option>${cols.map((c) => html`<option value="${c.name}">${c.name} (${c.count})</option>`)}`);
    sel.hidden = cols.length === 0;
    if (cols.some((c) => c.name === this.prefs.collection)) sel.value = this.prefs.collection;
    else this.prefs.collection = '';
  }

  async load() {
    const p = this.prefs;
    this.el.querySelectorAll('[data-kind]').forEach((b) => b.classList.toggle('is-active', b.dataset.kind === p.kind));
    const [sort, order] = p.sort.split(':');
    const params = new URLSearchParams({ kind: p.kind, sort, order, owner: p.owner });
    if (this.q) params.set('q', this.q);
    if (p.collection) params.set('collection', p.collection);
    const id = ++this.reqId;
    const items = await api(`/api/media?${params}`);
    if (id !== this.reqId) return; // veraltete Antwort
    const total = items.reduce((s, m) => s + (m.size || 0), 0);
    this.el.querySelector('#summary').textContent = `${items.length} ${items.length === 1 ? 'Eintrag' : 'Einträge'} · ${fmtBytes(total)}`;
    const box = this.el.querySelector('#items');
    if (!items.length) {
      box.innerHTML = String(html`<div class="empty">${icons.music}<p>${this.q || p.collection || p.kind !== 'all' ? 'Keine Treffer.' : 'Die Bibliothek ist noch leer.'}</p>
        <a class="btn btn--primary" href="#/">Neuer Download</a></div>`);
      return;
    }
    box.innerHTML = String(html`<div class="grid">${items.map((m) => html`
      <a class="tile" href="#/media/${m.id}">
        <div class="tile__art ${m.kind === 'video' ? 'tile__art--video' : ''}">
          ${m.has_cover ? html`<img src="/api/media/${m.id}/cover" alt="" loading="lazy">` : html`<div class="placeholder">${m.kind === 'video' ? icons.film : icons.music}</div>`}
          <span class="badge">${m.format} · ${fmtDuration(m.duration)}</span>
        </div>
        <div>
          <div class="tile__title clamp-2">${m.title}</div>
          <div class="tile__sub ellipsis">${p.owner !== 'me' && m.owner ? html`<span class="owner">${icons.user}${m.owner}</span> · ` : ''}${m.artist || '–'}</div>
          <div class="tile__sub">${fmtBytes(m.size)} · ${fmtDate(m.created_at)}</div>
        </div>
      </a>`)}</div>`);
  }

  onEvent(evt) {
    if (['media', 'media_deleted', 'resync'].includes(evt.type)) {
      this.loadCollections().then(() => this.load()).catch(() => {});
    }
  }

  destroy() { clearTimeout(this.timer); }
}
