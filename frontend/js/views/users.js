// Benutzerverwaltung (nur Admins): anlegen, Rollen, Kontingente, Job-Limits, sperren, löschen
import { api } from '../api.js';
import { store } from '../app.js';
import { applyBars, busy, fmtBytes, fmtDate, html, icons, raw, toast } from '../util.js';

const numOrNull = (v) => (v === '' || v == null ? null : Number(v));

export class UsersView {
  constructor(el) {
    this.el = el;
    this.editing = null;
  }

  async render() {
    if (!store.me || !store.me.user || store.me.user.role !== 'admin') {
      this.el.innerHTML = String(html`<div class="notice notice--err">Nur für Administratoren.</div>`);
      return;
    }
    const { users, defaults } = await api('/api/users');
    this.users = users;
    this.el.innerHTML = String(html`
      <header class="page-head">
        <a class="btn btn--sm btn--ghost" href="#/more">${icons.back}<span>Mehr</span></a>
      </header>
      <header class="page-head"><div><h1>Benutzer</h1>
        <p>Jeder Benutzer hat eigene Bibliothek, eigene Aufträge und eigenen Podcast-Feed.</p></div></header>
      <div class="stack">
        <div class="card card--flush" id="list">${users.map((u) => this.userHtml(u))}</div>
        <details class="card" id="create-box">
          <summary class="row">${icons.plus}<b>Benutzer anlegen</b></summary>
          <form class="stack" id="create" autocomplete="off">
            <label class="field"><span>Benutzername</span>
              <input name="username" required minlength="2" maxlength="32" pattern="[a-zA-Z0-9][a-zA-Z0-9._\\-]{1,31}"
                     autocapitalize="none" spellcheck="false" placeholder="z. B. anna">
            </label>
            <p class="hint">2–32 Zeichen: a–z, 0–9, Punkt, Binde- und Unterstrich. Wird auch als Ordnername verwendet.</p>
            <label class="field"><span>Passwort (mind. 8 Zeichen)</span>
              <input name="password" type="password" required minlength="8" autocomplete="new-password"></label>
            <label class="field"><span>Rolle</span>
              <select name="role"><option value="user">Benutzer</option><option value="admin">Admin</option></select></label>
            <div class="row">
              <label class="field grow"><span>Speicherkontingent (MB, leer = unbegrenzt)</span>
                <input name="quota_mb" type="number" min="0" inputmode="numeric" value="${defaults.quota_mb ?? ''}"></label>
              <label class="field grow"><span>Max. parallele Aufträge (leer = unbegrenzt)</span>
                <input name="max_jobs" type="number" min="0" inputmode="numeric" value="${defaults.max_jobs ?? ''}"></label>
            </div>
            <button class="btn btn--primary" type="submit">Anlegen</button>
          </form>
        </details>
      </div>
    `);
    applyBars(this.el);
    this.el.querySelector('#list').addEventListener('click', (e) => this.onAction(e));
    this.el.querySelector('#list').addEventListener('submit', (e) => this.onSave(e));
    this.el.querySelector('#create').addEventListener('submit', (e) => this.onCreate(e));
  }

  userHtml(u) {
    const me = store.me.user.id === u.id;
    const quota = u.quota_mb ? u.quota_mb * 1024 * 1024 : null;
    const share = quota ? Math.min(1, u.usage_bytes / quota) : 0;
    const editing = this.editing === u.id;
    return html`<article class="user-row" data-user="${u.id}">
      <div class="row">
        <div class="grow">
          <div class="row">
            <b>${u.username}</b>
            ${u.role === 'admin' ? html`<span class="badge badge--accent">Admin</span>` : ''}
            ${u.disabled ? html`<span class="badge badge--warn">Gesperrt</span>` : ''}
            ${me ? html`<span class="badge badge--soft">Du</span>` : ''}
          </div>
          <div class="faint">${u.media_count} ${u.media_count === 1 ? 'Datei' : 'Dateien'} · ${fmtBytes(u.usage_bytes)}${quota ? ` von ${fmtBytes(quota)}` : ' · unbegrenzt'}
            · ${u.max_jobs ? `max. ${u.max_jobs} parallel` : 'Aufträge unbegrenzt'} · seit ${fmtDate(u.created_at)}</div>
        </div>
        <button class="btn btn--sm" data-act="edit">${icons.edit}<span>${editing ? 'Schließen' : 'Bearbeiten'}</span></button>
      </div>
      ${quota ? html`<div class="progress ${share >= 0.9 ? 'progress--warn' : ''}"><div class="progress__bar" data-progress="${share}"></div></div>` : ''}
      ${editing ? html`<form class="stack user-edit" data-form="${u.id}">
        <div class="row">
          <label class="field grow"><span>Rolle</span>
            <select name="role" ${raw(me ? 'disabled' : '')}>
              <option value="user" ${raw(u.role === 'user' ? 'selected' : '')}>Benutzer</option>
              <option value="admin" ${raw(u.role === 'admin' ? 'selected' : '')}>Admin</option>
            </select></label>
          <label class="field grow"><span>Kontingent (MB)</span>
            <input name="quota_mb" type="number" min="0" inputmode="numeric" value="${u.quota_mb ?? ''}" placeholder="unbegrenzt"></label>
          <label class="field grow"><span>Max. parallel</span>
            <input name="max_jobs" type="number" min="0" inputmode="numeric" value="${u.max_jobs ?? ''}" placeholder="unbegrenzt"></label>
        </div>
        <label class="field"><span>Neues Passwort (optional)</span>
          <input name="password" type="password" minlength="8" autocomplete="new-password" placeholder="unverändert"></label>
        ${me ? '' : html`<label class="toggle"><span>Konto gesperrt<small>Beendet Sitzungen und bricht laufende Aufträge ab</small></span>
          <input type="checkbox" name="disabled" ${raw(u.disabled ? 'checked' : '')}></label>`}
        <div class="row">
          <button class="btn btn--primary" type="submit">Speichern</button>
          ${me ? '' : html`<button class="btn btn--danger" type="button" data-act="delete">${icons.trash}<span>Löschen</span></button>`}
        </div>
      </form>` : ''}
    </article>`;
  }

  rerenderList() {
    const list = this.el.querySelector('#list');
    list.innerHTML = String(html`${this.users.map((u) => this.userHtml(u))}`);
    applyBars(list);
  }

  async reload() {
    this.users = (await api('/api/users')).users;
    this.rerenderList();
  }

  async onAction(e) {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const id = btn.closest('[data-user]').dataset.user;
    const user = this.users.find((u) => u.id === id);
    if (btn.dataset.act === 'edit') {
      this.editing = this.editing === id ? null : id;
      this.rerenderList();
      return;
    }
    if (btn.dataset.act === 'delete') {
      const answer = prompt(`Benutzer „${user.username}“ mit allen ${user.media_count} Dateien endgültig löschen?\n\nZum Bestätigen den Benutzernamen eingeben:`);
      if (answer === null) return;
      if (answer.trim().toLowerCase() !== user.username) { toast('Benutzername stimmt nicht überein – nichts gelöscht.', 'err'); return; }
      await busy(btn, async () => {
        try {
          const r = await api(`/api/users/${id}`, { method: 'DELETE' });
          toast(`Gelöscht (${r.deleted_media} Dateien).`);
          this.editing = null;
          await this.reload();
        } catch (err) { toast(err.message, 'err', 6000); }
      });
    }
  }

  async onSave(e) {
    e.preventDefault();
    const form = e.target;
    const id = form.dataset.form;
    const el = form.elements;
    const body = { quota_mb: numOrNull(el.quota_mb.value) ?? 0, max_jobs: numOrNull(el.max_jobs.value) ?? 0 };
    if (!el.role.disabled) body.role = el.role.value;
    if (el.disabled) body.disabled = el.disabled.checked;
    if (el.password.value) body.password = el.password.value;
    await busy(form.querySelector('button[type=submit]'), async () => {
      try {
        await api(`/api/users/${id}`, { method: 'PATCH', body });
        toast('Gespeichert.');
        this.editing = null;
        await this.reload();
      } catch (err) { toast(err.message, 'err', 6000); }
    });
  }

  async onCreate(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const el = form.elements;
    const body = {
      username: el.username.value.trim().toLowerCase(),
      password: el.password.value,
      role: el.role.value,
      quota_mb: numOrNull(el.quota_mb.value) ?? 0,
      max_jobs: numOrNull(el.max_jobs.value) ?? 0,
    };
    await busy(form.querySelector('button[type=submit]'), async () => {
      try {
        await api('/api/users', { method: 'POST', body });
        toast(`Benutzer „${body.username}“ angelegt.`);
        form.reset();
        this.el.querySelector('#create-box').open = false;
        await this.reload();
      } catch (err) { toast(err.message, 'err', 6000); }
    });
  }
}
