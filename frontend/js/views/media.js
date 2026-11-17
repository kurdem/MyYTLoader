// Detailseite: Abspielen, iOS-Download, Teilen, VLC, Metadaten bearbeiten, Löschen
import { api } from '../api.js';
import { myId } from '../app.js';
import { busy, fmtBytes, fmtDate, fmtDuration, html, icons, isIOS, toast } from '../util.js';

const MIME = { m4a: 'audio/mp4', mp3: 'audio/mpeg', opus: 'audio/ogg', flac: 'audio/flac', mp4: 'video/mp4', webm: 'video/webm' };
const SHARE_LIMIT = 300 * 1024 * 1024; // größere Dateien nicht in den Speicher laden

export class MediaView {
  constructor(el, params) {
    this.el = el;
    this.id = params[0];
    this.shareFile = null;
  }

  async render() {
    const [m, feed, myFeeds, inFeeds] = await Promise.all([
      api(`/api/media/${this.id}`),
      api('/api/feed').catch(() => null),
      api('/api/feeds').catch(() => []),
      api(`/api/feeds/of-media/${this.id}`).catch(() => []),
    ]);
    this.m = m;
    // Direktlinks laufen über den eigenen Feed-Token – nur für eigene Medien möglich
    const own = m.user_id === myId();
    this.feed = own ? feed : null;
    const fileUrl = `/api/media/${m.id}/file`;
    const downloadUrl = `/api/media/${m.id}/download`;
    const canShareFiles = !!(navigator.canShare && navigator.canShare({ files: [new File([''], `x.${m.format}`, { type: MIME[m.format] })] }));
    const iosHint = m.format === 'webm' || m.format === 'opus'
      ? 'Hinweis: Dieses Format spielen nicht alle iOS-Apps ab. VLC funktioniert zuverlässig.'
      : null;

    this.el.innerHTML = String(html`
      <header class="page-head">
        <a class="btn btn--sm btn--ghost" href="#/library">${icons.back}<span>Bibliothek</span></a>
      </header>
      <div class="detail">
        <div class="stack">
          ${m.kind === 'video'
            ? html`<video controls playsinline preload="metadata" src="${fileUrl}" ${m.has_cover ? html`poster="/api/media/${m.id}/cover"` : ''}></video>`
            : html`<div class="detail__art">${m.has_cover ? html`<img src="/api/media/${m.id}/cover" alt="">` : ''}</div>
                   <audio controls preload="metadata" src="${fileUrl}"></audio>`}
        </div>
        <div class="stack">
          <div>
            <h1>${m.title}</h1>
            <p class="muted">${m.artist || ''}${m.collection ? html` · ${m.collection}` : ''}</p>
            ${!own && m.owner ? html`<p class="owner">${icons.user}Gehört ${m.owner}</p>` : ''}
          </div>
          <div class="facts">
            <div class="fact"><span>Format</span><b>${m.format.toUpperCase()}</b></div>
            <div class="fact"><span>Dauer</span><b>${fmtDuration(m.duration)}</b></div>
            <div class="fact"><span>Größe</span><b>${fmtBytes(m.size)}</b></div>
            <div class="fact"><span>Hinzugefügt</span><b>${fmtDate(m.created_at)}</b></div>
          </div>
          <div class="actions">
            <a class="btn btn--primary btn--lg" href="${downloadUrl}" download="${m.filename}">${icons.download}<span>Herunterladen</span></a>
            ${canShareFiles && m.size <= SHARE_LIMIT ? html`<button class="btn" id="share">${icons.share}<span>Teilen …</span></button>` : ''}
            ${this.feed ? html`<a class="btn" id="vlc" href="#">${icons.vlc}<span>In VLC öffnen</span></a>` : ''}
            ${this.feed ? html`<button class="btn" id="copy-link">${icons.copy}<span>Direktlink kopieren</span></button>` : ''}
          </div>
          ${isIOS ? html`<div class="notice">Safari: Nach „Herunterladen“ landet die Datei in der <b>Dateien</b>-App unter „Downloads“.
            Von dort kannst du sie per „Teilen“ an VLC, Musik- oder Podcast-Player übergeben.</div>` : ''}
          ${iosHint ? html`<div class="notice notice--warn">${iosHint}</div>` : ''}
          ${m.source_url ? html`<p class="faint ellipsis">Quelle: <a href="${m.source_url}" target="_blank" rel="noopener noreferrer">${m.source_url}</a></p>` : ''}
          ${m.description ? html`<details class="desc"><summary>Beschreibung</summary><div class="pre">${m.description}</div></details>` : ''}

          ${own ? html`<section class="card stack" id="feeds-box">
            <div class="row">${icons.rss}<b class="grow">In eigenen Feeds</b><a class="small" href="#/feeds">Feeds verwalten</a></div>
            ${myFeeds.length ? html`<div class="chips chips--wrap">${myFeeds.map((f) => html`
              <button class="chip ${inFeeds.includes(f.id) ? 'is-active' : ''}" type="button" data-feed="${f.id}"
                aria-pressed="${inFeeds.includes(f.id) ? 'true' : 'false'}">${inFeeds.includes(f.id) ? '✓ ' : '+ '}${f.title}</button>`)}</div>
              <p class="hint">Antippen fügt die Datei dem Feed hinzu bzw. entfernt sie.</p>`
              : html`<p class="hint">Noch keine eigenen Feeds. Unter „Feeds“ kannst du z. B. einen Feed „Einschlafen“ anlegen.</p>`}
          </section>` : ''}

          <details class="card" id="edit-box">
            <summary class="row">${icons.edit}<b>Metadaten bearbeiten</b></summary>
            <form class="stack" id="edit">
              <label class="field"><span>Titel</span><input name="title" required maxlength="300" value="${m.title}"></label>
              <label class="field"><span>Interpret / Kanal</span><input name="artist" maxlength="200" value="${m.artist || ''}"></label>
              <label class="field"><span>Ordner</span><input name="collection" maxlength="200" value="${m.collection || ''}" placeholder="(kein Ordner)"></label>
              <label class="field"><span>Beschreibung</span><textarea name="description" maxlength="10000">${m.description || ''}</textarea></label>
              <p class="hint">Tags in der Datei und der Dateiname werden angepasst.</p>
              <button class="btn btn--primary" type="submit">Speichern</button>
            </form>
          </details>
          <button class="btn btn--danger" id="delete">${icons.trash}<span>Datei löschen</span></button>
        </div>
      </div>
    `);

    this.bind();
  }

  directUrl() {
    if (!this.feed || !this.feed.media_base) return null;
    return `${this.feed.media_base}/${this.m.id}/${encodeURIComponent(this.m.filename)}`;
  }

  bind() {
    const feedsBox = this.el.querySelector('#feeds-box');
    if (feedsBox) {
      feedsBox.addEventListener('click', async (e) => {
        const chip = e.target.closest('[data-feed]');
        if (!chip) return;
        const on = chip.getAttribute('aria-pressed') === 'true';
        chip.disabled = true;
        try {
          if (on) await api(`/api/feeds/${chip.dataset.feed}/items/${this.m.id}`, { method: 'DELETE' });
          else await api(`/api/feeds/${chip.dataset.feed}/items`, { method: 'POST', body: { media_ids: [this.m.id] } });
          const title = chip.textContent.replace(/^[✓+]\s*/, '');
          chip.setAttribute('aria-pressed', on ? 'false' : 'true');
          chip.classList.toggle('is-active', !on);
          chip.textContent = `${on ? '+ ' : '✓ '}${title}`;
          toast(on ? `Aus „${title}“ entfernt.` : `Zu „${title}“ hinzugefügt.`);
        } catch (err) { toast(err.message, 'err'); } finally { chip.disabled = false; }
      });
    }
    const m = this.m;
    const share = this.el.querySelector('#share');
    if (share) {
      // iOS verlangt, dass share() direkt aus einem Tap heraus aufgerufen wird.
      // Daher: 1. Tap lädt die Datei, 2. Tap öffnet das Teilen-Menü.
      share.addEventListener('click', async () => {
        if (this.shareFile) {
          try {
            await navigator.share({ files: [this.shareFile], title: m.title });
          } catch (err) {
            if (err.name !== 'AbortError') toast('Teilen nicht möglich.', 'err');
          }
          return;
        }
        await busy(share, async () => {
          try {
            const res = await fetch(`/api/media/${m.id}/file`, { credentials: 'same-origin' });
            if (!res.ok) throw new Error();
            const blob = await res.blob();
            this.shareFile = new File([blob], m.filename, { type: MIME[m.format] });
          } catch {
            toast('Datei konnte nicht geladen werden.', 'err');
          }
        });
        if (this.shareFile) share.querySelector('span').textContent = 'Jetzt teilen';
      });
    }
    const vlc = this.el.querySelector('#vlc');
    if (vlc) {
      vlc.addEventListener('click', (e) => {
        e.preventDefault();
        const url = this.directUrl();
        if (!url) return;
        location.href = `vlc-x-callback://x-callback-url/download?url=${encodeURIComponent(url)}&filename=${encodeURIComponent(m.filename)}`;
      });
    }
    const copy = this.el.querySelector('#copy-link');
    if (copy) {
      copy.addEventListener('click', async () => {
        const url = this.directUrl();
        try { await navigator.clipboard.writeText(url); toast('Direktlink kopiert. Er funktioniert ohne Login – nur mit Vertrauten teilen.'); } catch { prompt('Direktlink:', url); }
      });
    }
    this.el.querySelector('#edit').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const el = f.elements;
      await busy(f.querySelector('button'), async () => {
        try {
          await api(`/api/media/${m.id}`, {
            method: 'PATCH',
            body: { title: el.title.value, artist: el.artist.value, collection: el.collection.value, description: el.description.value },
          });
          toast('Gespeichert.');
          this.render();
        } catch (err) { toast(err.message, 'err'); }
      });
    });
    this.el.querySelector('#delete').addEventListener('click', async (e) => {
      if (!confirm(`„${m.title}“ endgültig löschen?`)) return;
      await busy(e.currentTarget, async () => {
        try {
          await api(`/api/media/${m.id}`, { method: 'DELETE' });
          toast('Gelöscht.');
          location.hash = '#/library';
        } catch (err) { toast(err.message, 'err'); }
      });
    });
  }

  onEvent(evt) {
    if (evt.type === 'media_deleted' && evt.id === this.id) location.hash = '#/library';
  }
}
