// Mehr: Podcast-Feed, eigene Dateien, Wartung, System, iOS-Tipps
import { api, upload } from '../api.js';
import { appName, applyBranding, isAdmin, logout, store, upsertJob } from '../app.js';
import { applyBars, busy, fmtBytes, fmtDateTime, html, icons, isIOS, isStandalone, toast } from '../util.js';

export class MoreView {
  constructor(el) {
    this.el = el;
  }

  async render() {
    const [sys, me, brand] = await Promise.all([
      api('/api/system'), api('/api/auth/me'), api('/api/branding'),
    ]);
    this.brand = brand;
    store.system = sys;
    store.me = me;
    const v = sys.versions;
    const s = sys.settings;
    const acc = me.user;
    const admin = isAdmin();
    const share = acc.quota_bytes ? Math.min(1, acc.usage_bytes / acc.quota_bytes) : 0;
    this.el.innerHTML = String(html`
      <header class="page-head"><div><h1>Mehr</h1><p>Podcast-Feed, eigene Dateien und Verwaltung.</p></div></header>
      <div class="stack">
        ${isIOS && !isStandalone ? html`<section class="notice">Tipp: In Safari auf <b>Teilen</b> → <b>Zum Home-Bildschirm</b> tippen –
          dann startet ${appName()} wie eine App.</section>` : ''}

        <section class="card stack">
          <div class="row">${icons.user}<h2 class="grow ellipsis">${acc.username}</h2>
            <span class="badge ${admin ? 'badge--accent' : 'badge--soft'}">${admin ? 'Admin' : 'Benutzer'}</span>
            ${store.me.auth_enabled ? html`<button class="btn btn--sm btn--logout" type="button" id="logout">${icons.logout}<span>Abmelden</span></button>` : ''}</div>
          <div class="meter">
            <div class="row small"><span class="grow">Speicher</span>
              <b>${fmtBytes(acc.usage_bytes)}${acc.quota_bytes ? ` von ${fmtBytes(acc.quota_bytes)}` : ''}</b></div>
            ${acc.quota_bytes
              ? html`<div class="progress ${share >= 0.9 ? 'progress--warn' : ''}"><div class="progress__bar" data-progress="${share}"></div></div>
                ${share >= 0.9 ? html`<p class="hint">Fast voll – lösche alte Dateien, damit neue Downloads möglich sind.</p>` : ''}`
              : html`<p class="hint">Kein Speicherkontingent gesetzt.</p>`}
            ${acc.max_jobs ? html`<p class="hint">Bis zu ${acc.max_jobs} Aufträge laufen gleichzeitig, weitere warten.</p>` : ''}
          </div>
          ${admin ? html`<a class="btn" href="#/users">${icons.user}<span>Benutzer verwalten</span></a>` : ''}
          ${store.me.auth_enabled ? html`<details>
            <summary class="row small"><b>Passwort ändern</b></summary>
            <form class="stack" id="password" autocomplete="on">
              <input type="text" name="username" value="${acc.username}" autocomplete="username" hidden>
              <label class="field"><span>Aktuelles Passwort</span><input name="current" type="password" required autocomplete="current-password"></label>
              <label class="field"><span>Neues Passwort (mind. 8 Zeichen)</span><input name="new" type="password" required minlength="8" autocomplete="new-password"></label>
              <button class="btn btn--primary" type="submit">Passwort ändern</button>
              <p class="hint">Andere angemeldete Geräte werden abgemeldet.</p>
            </form>
          </details>` : ''}
        </section>

        <a class="card row link-card" href="#/help">${icons.help}
          <div class="grow"><b>Hilfe</b>
            <p class="faint">Einfach erklärt: Downloads, iPhone, Podcast-Feeds, Abos und häufige Probleme.</p></div>
          <span class="muted">›</span></a>

        <a class="card row link-card" href="#/feeds">${icons.rss}
          <div class="grow"><b>Podcast-Feeds &amp; Abos</b>
            <p class="faint">Feed-Links, eigene Feeds, Serien und automatische Abos findest du jetzt unter „Feeds“.</p></div>
          <span class="muted">›</span></a>

        <section class="card stack">
          <h2>Eigene Datei hinzufügen</h2>
          <p class="muted small">Eigene Aufnahmen oder Audiodateien in die Bibliothek (und damit in den Podcast-Feed) übernehmen.</p>
          <form class="stack" id="upload">
            <label class="field"><span>Datei (max. ${s.max_file_size_mb} MB)</span>
              <input type="file" name="file" accept="audio/*,video/*,.m4a,.mp3,.opus,.ogg,.flac,.wav,.aac,.mp4,.mkv,.webm,.mov" required></label>
            <label class="field"><span>Titel (optional)</span><input name="title" maxlength="300"></label>
            <label class="field"><span>Interpret / Autor (optional)</span><input name="artist" maxlength="200"></label>
            <label class="field"><span>Ordner (optional)</span><input name="collection" maxlength="200"></label>
            <label class="field"><span>Beschreibung (optional)</span><textarea name="description" maxlength="5000"></textarea></label>
            <label class="field"><span>Zielformat</span>
              <select name="format">
                <option value="m4a">M4A (AAC) – empfohlen</option><option value="mp3">MP3</option>
                <option value="opus">OPUS</option><option value="flac">FLAC</option>
                <option value="mp4">MP4 (Video)</option><option value="webm">WebM (Video)</option>
              </select></label>
            <label class="toggle"><span>Lautstärke normalisieren</span><input type="checkbox" name="normalize" ${s.default_normalize ? html`checked` : ''}></label>
            <label class="toggle"><span>Stille am Anfang/Ende entfernen</span><input type="checkbox" name="trim_silence" ${s.default_trim_silence ? html`checked` : ''}></label>
            <div class="progress" id="up-progress" hidden><div class="progress__bar" data-progress="0"></div></div>
            <button class="btn btn--primary" type="submit">Hochladen</button>
          </form>
        </section>

        <section class="card stack">
          <h2>Speicher &amp; Aufräumen</h2>
          <dl class="kv">
            <dt>${admin ? 'Alle Bibliotheken' : 'Deine Bibliothek'}</dt><dd>${sys.library.count} Dateien · ${fmtBytes(sys.library.size)}</dd>
            ${sys.disk.free != null ? html`<dt>Freier Speicher</dt><dd>${fmtBytes(sys.disk.free)} von ${fmtBytes(sys.disk.total)}</dd>` : ''}
            <dt>Automatisch löschen</dt><dd>${s.retention_days ? `nach ${s.retention_days} Tagen` : 'deaktiviert'}</dd>
          </dl>
          <form class="row" id="cleanup">
            <label class="field grow"><span>Dateien löschen, die älter sind als (Tage)</span>
              <input name="days" type="number" min="1" max="36500" value="30" inputmode="numeric" required></label>
            <button class="btn btn--danger" type="submit">Löschen</button>
          </form>
          ${admin ? html`<label class="toggle"><span>Für alle Benutzer<small>Sonst nur deine eigenen Dateien</small></span>
            <input type="checkbox" id="cleanup-all"></label>` : ''}
          <div class="row">
            <button class="btn btn--sm" id="clear-jobs">Auftragsverlauf leeren</button>
            ${admin ? html`<button class="btn btn--sm" id="clear-temp">Temporäre Dateien leeren</button>` : ''}
          </div>
        </section>

        ${admin ? html`<section class="card stack" id="branding">
          <h2>Erscheinungsbild</h2>
          <div class="row">
            <img class="brand-preview" src="${brand.icons['icon-512.png'].replace(/^\//, '')}" alt="Aktuelles Icon" width="72" height="72">
            <div class="grow">
              <b>${brand.name}</b>
              <p class="faint">${brand.custom_icon ? 'Eigenes Icon' : 'Standard-Icon'}</p>
            </div>
          </div>
          <form class="stack" id="brand-name">
            <label class="field"><span>Name der App</span>
              <input name="name" maxlength="40" required value="${brand.name}" placeholder="${brand.default_name}"></label>
            <div class="row">
              <button class="btn btn--primary" type="submit">Name speichern</button>
              ${brand.name !== brand.default_name ? html`<button class="btn btn--sm btn--ghost" type="button" id="brand-name-reset">Standard</button>` : ''}
            </div>
          </form>
          <form class="stack" id="brand-icon">
            <label class="field"><span>Neues Icon (PNG, JPG oder WebP, quadratisch, ideal 1024×1024 px)</span>
              <input type="file" name="file" accept="image/png,image/jpeg,image/webp" required></label>
            <p class="hint">Nicht quadratische Bilder werden mittig zugeschnitten. Daraus entstehen automatisch App-Icons,
              Home-Bildschirm-Icon und Podcast-Artwork.</p>
            <div class="row">
              <button class="btn btn--primary" type="submit">Icon hochladen</button>
              ${brand.custom_icon ? html`<button class="btn btn--sm btn--ghost" type="button" id="brand-icon-reset">Standard-Icon</button>` : ''}
            </div>
          </form>
          <p class="hint">Bereits auf dem Home-Bildschirm installierte Apps übernehmen Name und Icon erst,
            wenn sie entfernt und neu hinzugefügt werden (Einschränkung von iOS).</p>
        </section>` : ''}

        <section class="card stack">
          <h2>System</h2>
          <dl class="kv">
            <dt>Version</dt><dd>${v.app}</dd>
            <dt>yt-dlp</dt><dd>${v.yt_dlp || 'nicht gefunden'} (${sys.ytdlp.channel}${sys.ytdlp.auto_update ? ', Auto-Update' : ''})</dd>
            <dt>Letztes Update</dt><dd>${sys.ytdlp.last_update ? fmtDateTime(sys.ytdlp.last_update) : '–'}</dd>
            <dt>FFmpeg</dt><dd>${v.ffmpeg || 'nicht gefunden'}</dd>
            <dt>Deno (JS-Runtime)</dt><dd>${v.deno || 'nicht gefunden'}</dd>
            <dt>Gleichzeitige Downloads</dt><dd>${s.max_concurrent_downloads}</dd>
            <dt>Max. Dateigröße</dt><dd>${s.max_file_size_mb} MB</dd>
            <dt>Standard-Audio</dt><dd>${s.default_audio_format.toUpperCase()} · ${s.default_audio_bitrate}</dd>
            <dt>Erlaubte Quellen</dt><dd>${s.allowed_domains.join(', ')}</dd>
          </dl>
          ${admin ? html`<button class="btn" id="update-ytdlp">${icons.retry}<span>yt-dlp jetzt aktualisieren</span></button>
          <p class="hint">YouTube ändert sich häufig. Wenn Downloads plötzlich scheitern, hilft meist ein yt-dlp-Update.</p>`
          : html`<p class="hint">Wenn Downloads plötzlich scheitern, kann ein Admin yt-dlp aktualisieren.</p>`}
        </section>

        <section class="card stack">
          <h2>Rechtliches</h2>
          <p class="muted small">Lade nur Inhalte herunter, für die du die erforderlichen Rechte oder eine Erlaubnis hast.
            Diese App umgeht keine DRM- oder Zugriffsschutzmaßnahmen und speichert keine Zugangsdaten.</p>
        </section>

      </div>
    `);
    applyBars(this.el);
    this.bind();
  }

  bind() {
    const $ = (sel) => this.el.querySelector(sel);
    $('#upload').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const data = new FormData();
      const el = f.elements;
      data.append('file', el.file.files[0]);
      for (const key of ['title', 'artist', 'collection', 'description', 'format']) if (el[key].value) data.append(key, el[key].value);
      data.append('normalize', el.normalize.checked ? 'true' : 'false');
      data.append('trim_silence', el.trim_silence.checked ? 'true' : 'false');
      const bar = $('#up-progress');
      bar.hidden = false;
      await busy(f.querySelector('button[type=submit]'), async () => {
        try {
          const res = await upload('/api/uploads', data, (p) => { bar.firstElementChild.dataset.progress = p; applyBars(bar); });
          res.jobs.forEach(upsertJob);
          toast('Hochgeladen – Verarbeitung läuft.');
          location.hash = '#/jobs';
        } catch (err) {
          toast(err.message, 'err', 6000);
        } finally {
          bar.hidden = true;
        }
      });
    });
    $('#cleanup').addEventListener('submit', async (e) => {
      e.preventDefault();
      const days = Number(e.currentTarget.elements.days.value);
      const all = !!(this.el.querySelector('#cleanup-all') || {}).checked;
      if (!confirm(`${all ? 'Bei ALLEN Benutzern alle' : 'Alle deine'} Dateien löschen, die älter als ${days} Tage sind?`)) return;
      await busy(e.currentTarget.querySelector('button'), async () => {
        try { const r = await api('/api/media/cleanup', { method: 'POST', body: { older_than_days: days, all_users: all } }); toast(`${r.deleted} Dateien gelöscht.`); this.render(); } catch (err) { toast(err.message, 'err'); }
      });
    });
    $('#clear-jobs').addEventListener('click', async (e) => {
      await busy(e.currentTarget, async () => {
        try { const r = await api('/api/jobs', { method: 'DELETE' }); toast(`${r.deleted} Einträge entfernt.`); } catch (err) { toast(err.message, 'err'); }
      });
    });
    const pw = $('#password');
    if (pw) {
      pw.addEventListener('submit', async (e) => {
        e.preventDefault();
        const el = e.currentTarget.elements;
        await busy(e.currentTarget.querySelector('button'), async () => {
          try {
            await api('/api/auth/password', { method: 'POST', body: { current: el.current.value, new: el.new.value } });
            toast('Passwort geändert.');
            el.current.value = '';
            el.new.value = '';
          } catch (err) { toast(err.message, 'err'); }
        });
      });
    }
    this.bindBranding($);
    const temp = $('#clear-temp');
    if (temp) temp.addEventListener('click', async (e) => {
      await busy(e.currentTarget, async () => {
        try { const r = await api('/api/system/cleanup-temp', { method: 'POST' }); toast(`${r.deleted} temporäre Einträge entfernt.`); } catch (err) { toast(err.message, 'err'); }
      });
    });
    const upd = $('#update-ytdlp');
    if (upd) upd.addEventListener('click', async (e) => {
      await busy(e.currentTarget, async () => {
        try {
          const r = await api('/api/system/ytdlp/update', { method: 'POST' });
          toast(r.updated ? `yt-dlp aktualisiert: ${r.before} → ${r.after}` : `yt-dlp ist aktuell (${r.after}).`);
          this.render();
        } catch (err) { toast(err.message, 'err', 6000); }
      });
    });
    const lo = $('#logout');
    if (lo) lo.addEventListener('click', logout);
  }

  bindBranding($) {
    const nameForm = $('#brand-name');
    if (!nameForm) return;
    const done = (info, msg) => { applyBranding(info); toast(msg); this.render(); };
    nameForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      await busy(form.querySelector('button[type=submit]'), async () => {
        try {
          done(await api('/api/branding', { method: 'PUT', body: { name: form.elements.name.value } }), 'Name gespeichert.');
        } catch (err) { toast(err.message, 'err'); }
      });
    });
    const nameReset = $('#brand-name-reset');
    if (nameReset) {
      nameReset.addEventListener('click', async () => {
        await busy(nameReset, async () => {
          try { done(await api('/api/branding', { method: 'PUT', body: { name: null } }), 'Standardname wiederhergestellt.'); } catch (err) { toast(err.message, 'err'); }
        });
      });
    }
    $('#brand-icon').addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const data = new FormData();
      data.append('file', form.elements.file.files[0]);
      await busy(form.querySelector('button[type=submit]'), async () => {
        try { done(await upload('/api/branding/icon', data, () => {}), 'Icon gespeichert.'); } catch (err) { toast(err.message, 'err', 6000); }
      });
    });
    const iconReset = $('#brand-icon-reset');
    if (iconReset) {
      iconReset.addEventListener('click', async () => {
        if (!confirm('Eigenes Icon entfernen und Standard-Icon verwenden?')) return;
        await busy(iconReset, async () => {
          try { done(await api('/api/branding/icon', { method: 'DELETE' }), 'Standard-Icon wiederhergestellt.'); } catch (err) { toast(err.message, 'err'); }
        });
      });
    }
  }
}
