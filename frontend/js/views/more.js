// Mehr: Podcast-Feed, eigene Dateien, Wartung, System, iOS-Tipps
import { api, upload } from '../api.js';
import { logout, store, upsertJob } from '../app.js';
import { applyBars, busy, copyText, fmtBytes, fmtDateTime, html, icons, isIOS, isStandalone, toast } from '../util.js';

export class MoreView {
  constructor(el) {
    this.el = el;
  }

  async render() {
    const [sys, feed] = await Promise.all([api('/api/system'), api('/api/feed')]);
    store.system = sys;
    const v = sys.versions;
    const s = sys.settings;
    this.el.innerHTML = String(html`
      <header class="page-head"><div><h1>Mehr</h1><p>Podcast-Feed, eigene Dateien und Verwaltung.</p></div></header>
      <div class="stack">
        ${isIOS && !isStandalone ? html`<section class="notice">Tipp: In Safari auf <b>Teilen</b> → <b>Zum Home-Bildschirm</b> tippen –
          dann startet MyYTLoader wie eine App.</section>` : ''}

        <section class="card stack">
          <div class="row">${icons.rss}<h2 class="grow">Privater Podcast-Feed</h2></div>
          <p class="muted small">Abonniere deine Bibliothek in einer Podcast-App (z. B. Apple Podcasts über „Sendung per URL folgen“,
            Overcast, Pocket Casts). Der Feed ist nur über den geheimen Link erreichbar und nicht öffentlich gelistet.</p>
          ${!feed.https ? html`<div class="notice notice--warn">Der Server ist per HTTP erreichbar. Viele Podcast-Apps (u. a. Apple Podcasts)
            verlangen HTTPS – bitte über den Nginx Proxy Manager mit Zertifikat veröffentlichen oder <code>BASE_URL</code> setzen.</div>` : ''}
          ${feed.feeds.map((f) => html`<div class="stack">
            <b class="small">${f.name}</b>
            <div class="copy-field">
              <input class="input" readonly value="${f.url}" aria-label="Feed-URL ${f.name}">
              <button class="btn icon-btn" data-copy="${f.url}" title="Kopieren">${icons.copy}</button>
            </div>
            <div class="row">
              <a class="btn btn--sm" href="${f.url.replace(/^https?:\/\//, 'podcast://')}">Apple Podcasts</a>
              <a class="btn btn--sm" href="overcast://x-callback-url/add?url=${encodeURIComponent(f.url)}">Overcast</a>
              <a class="btn btn--sm" href="pktc://subscribe/${f.url.replace(/^https?:\/\//, '')}">Pocket Casts</a>
            </div>
          </div>`)}
          <div class="row row--end">
            <button class="btn btn--sm btn--danger" id="rotate">Feed-Link neu erzeugen</button>
          </div>
          <p class="hint">Neu erzeugen macht alle bisherigen Feed- und Direktlinks ungültig.</p>
        </section>

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
            <dt>Bibliothek</dt><dd>${sys.library.count} Dateien · ${fmtBytes(sys.library.size)}</dd>
            ${sys.disk.free != null ? html`<dt>Freier Speicher</dt><dd>${fmtBytes(sys.disk.free)} von ${fmtBytes(sys.disk.total)}</dd>` : ''}
            <dt>Automatisch löschen</dt><dd>${s.retention_days ? `nach ${s.retention_days} Tagen` : 'deaktiviert'}</dd>
          </dl>
          <form class="row" id="cleanup">
            <label class="field grow"><span>Dateien löschen, die älter sind als (Tage)</span>
              <input name="days" type="number" min="1" max="36500" value="30" inputmode="numeric" required></label>
            <button class="btn btn--danger" type="submit">Löschen</button>
          </form>
          <div class="row">
            <button class="btn btn--sm" id="clear-jobs">Auftragsverlauf leeren</button>
            <button class="btn btn--sm" id="clear-temp">Temporäre Dateien leeren</button>
          </div>
        </section>

        <section class="card stack">
          <h2>System</h2>
          <dl class="kv">
            <dt>MyYTLoader</dt><dd>${v.app}</dd>
            <dt>yt-dlp</dt><dd>${v.yt_dlp || 'nicht gefunden'} (${sys.ytdlp.channel}${sys.ytdlp.auto_update ? ', Auto-Update' : ''})</dd>
            <dt>Letztes Update</dt><dd>${sys.ytdlp.last_update ? fmtDateTime(sys.ytdlp.last_update) : '–'}</dd>
            <dt>FFmpeg</dt><dd>${v.ffmpeg || 'nicht gefunden'}</dd>
            <dt>Deno (JS-Runtime)</dt><dd>${v.deno || 'nicht gefunden'}</dd>
            <dt>Gleichzeitige Downloads</dt><dd>${s.max_concurrent_downloads}</dd>
            <dt>Max. Dateigröße</dt><dd>${s.max_file_size_mb} MB</dd>
            <dt>Standard-Audio</dt><dd>${s.default_audio_format.toUpperCase()} · ${s.default_audio_bitrate}</dd>
            <dt>Erlaubte Quellen</dt><dd>${s.allowed_domains.join(', ')}</dd>
          </dl>
          <button class="btn" id="update-ytdlp">${icons.retry}<span>yt-dlp jetzt aktualisieren</span></button>
          <p class="hint">YouTube ändert sich häufig. Wenn Downloads plötzlich scheitern, hilft meist ein yt-dlp-Update.</p>
        </section>

        <section class="card stack">
          <h2>Rechtliches</h2>
          <p class="muted small">Lade nur Inhalte herunter, für die du die erforderlichen Rechte oder eine Erlaubnis hast.
            MyYTLoader umgeht keine DRM- oder Zugriffsschutzmaßnahmen und speichert keine Zugangsdaten.</p>
        </section>

        ${store.me && store.me.auth_enabled ? html`<button class="btn btn--block" id="logout">Abmelden</button>` : ''}
      </div>
    `);
    this.bind();
  }

  bind() {
    const $ = (sel) => this.el.querySelector(sel);
    this.el.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', () => copyText(b.dataset.copy)));
    $('#rotate').addEventListener('click', async (e) => {
      if (!confirm('Neuen Feed-Link erzeugen? Bestehende Abos funktionieren danach nicht mehr.')) return;
      await busy(e.currentTarget, async () => {
        try { await api('/api/feed/rotate', { method: 'POST' }); toast('Neuer Feed-Link erzeugt.'); this.render(); } catch (err) { toast(err.message, 'err'); }
      });
    });
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
      if (!confirm(`Alle Dateien löschen, die älter als ${days} Tage sind?`)) return;
      await busy(e.currentTarget.querySelector('button'), async () => {
        try { const r = await api('/api/media/cleanup', { method: 'POST', body: { older_than_days: days } }); toast(`${r.deleted} Dateien gelöscht.`); this.render(); } catch (err) { toast(err.message, 'err'); }
      });
    });
    $('#clear-jobs').addEventListener('click', async (e) => {
      await busy(e.currentTarget, async () => {
        try { const r = await api('/api/jobs', { method: 'DELETE' }); toast(`${r.deleted} Einträge entfernt.`); } catch (err) { toast(err.message, 'err'); }
      });
    });
    $('#clear-temp').addEventListener('click', async (e) => {
      await busy(e.currentTarget, async () => {
        try { const r = await api('/api/system/cleanup-temp', { method: 'POST' }); toast(`${r.deleted} temporäre Einträge entfernt.`); } catch (err) { toast(err.message, 'err'); }
      });
    });
    $('#update-ytdlp').addEventListener('click', async (e) => {
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
}
