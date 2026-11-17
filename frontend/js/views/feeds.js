// Feeds: eigene Feeds, automatische Abos und Standard-Feeds
import { api } from '../api.js';
import { isAdmin, myId, refreshJobs } from '../app.js';
import {
  bindCopy, busy, fallbackImages, fmtDuration, fmtRelative, helpLink, html, icons, raw, subscribeBlock, toast,
} from '../util.js';

const INTERVALS = [[1, 'stündlich'], [3, 'alle 3 Std.'], [6, 'alle 6 Std.'], [12, 'alle 12 Std.'], [24, 'täglich'], [48, 'alle 2 Tage'], [168, 'wöchentlich']];
const FORMATS = [['m4a', 'M4A (AAC)'], ['mp3', 'MP3'], ['opus', 'OPUS'], ['mp4', 'MP4 (Video)'], ['webm', 'WebM (Video)']];
const TYPE_LABEL = { episodic: 'Episoden', serial: 'Serie' };
const intervalLabel = (h) => (INTERVALS.find(([v]) => v === h) || [h, `alle ${h} Std.`])[1];

export class FeedsView {
  constructor(el) {
    this.el = el;
  }

  async render() {
    const [feeds, subs, info, cols] = await Promise.all([
      api('/api/feeds'), api('/api/subscriptions'), api('/api/feed'), api('/api/media/collections'),
    ]);
    Object.assign(this, { feeds, subs, info, cols });
    this.el.innerHTML = String(html`
      <header class="page-head"><div><h1>Feeds</h1>
        <p>Eigene Podcast-Feeds, automatische Abos und Feed-Links für deine Podcast-App.</p></div>
        ${helpLink('feeds', 'Hilfe: Feeds in der Podcast-App abonnieren')}</header>
      ${!info.https ? html`<div class="notice notice--warn">Der Server ist per HTTP erreichbar. Viele Podcast-Apps (u. a. Apple Podcasts)
        verlangen HTTPS – bitte über den Nginx Proxy Manager mit Zertifikat veröffentlichen oder <code>BASE_URL</code> setzen.</div>` : ''}
      <div class="stack">
        <section class="stack">
          <div class="row"><h2 class="grow">Eigene Feeds</h2>${helpLink('customfeeds', 'Hilfe: Eigene Feeds und Serien')}</div>
          ${feeds.length ? html`<div class="grid">${feeds.map((f) => html`
            <a class="tile" href="#/feeds/${f.id}">
              <div class="tile__art">
                <img src="/api/feeds/${f.id}/cover?v=${f.updated_at}" alt="" loading="lazy" data-fallback>
                <span class="badge">${TYPE_LABEL[f.type]} · ${f.item_count}</span>
              </div>
              <div>
                <div class="tile__title clamp-2">${f.title}</div>
                <div class="tile__sub">${f.item_count} ${f.item_count === 1 ? 'Episode' : 'Episoden'}${f.total_duration ? ` · ${fmtDuration(f.total_duration)}` : ''}</div>
              </div>
            </a>`)}</div>` : html`<p class="muted small">Noch keine eigenen Feeds. Stelle z. B. einen Feed „Einschlafen“ aus ausgewählten Dateien zusammen
              oder mach aus einem Hörbuch-Ordner eine Serie.</p>`}
          <details class="card" id="new-feed-box">
            <summary class="row">${icons.plus}<b>Neuer Feed</b></summary>
            <form class="stack" id="new-feed">
              <label class="field"><span>Titel</span><input name="title" required maxlength="120" placeholder="z. B. Einschlafen"></label>
              <label class="field"><span>Beschreibung (optional)</span><textarea name="description" maxlength="4000"></textarea></label>
              ${this.typeChoice('episodic')}
              <label class="field"><span>Inhalt übernehmen (optional)</span>
                <select name="collection"><option value="">Leer starten – Dateien später hinzufügen</option>
                  ${cols.map((c) => html`<option value="${c.name}">Ordner „${c.name}“ (${c.count})</option>`)}</select></label>
              <button class="btn btn--primary" type="submit">Feed anlegen</button>
            </form>
          </details>
        </section>

        <section class="stack">
          <div class="row"><h2 class="grow">Automatische Abos</h2>${helpLink('subscriptions', 'Hilfe: Automatische Abos')}</div>
          <p class="muted small">Kanäle und Playlists werden regelmäßig geprüft. Neue Videos werden automatisch geladen
            und können direkt in einem eigenen Feed landen.</p>
          <div class="stack" id="subs">${subs.map((s) => this.subHtml(s))}</div>
          <details class="card" id="new-sub-box" ${raw(subs.length ? '' : 'open')}>
            <summary class="row">${icons.plus}<b>Neues Abo</b></summary>
            ${this.newSubForm()}
          </details>
        </section>

        <section class="card stack">
          <div class="row">${icons.rss}<h2 class="grow">Standard-Feeds</h2></div>
          <p class="muted small">Automatisch erzeugte Feeds über deine ganze Bibliothek und je Ordner.
            Abonniere sie in Apple Podcasts über „Sendung per URL folgen“, in Overcast oder Pocket Casts.</p>
          ${info.feeds.map((f) => subscribeBlock(f.url, f.name))}
          <div class="row row--end"><button class="btn btn--sm btn--danger" id="rotate">Feed-Links neu erzeugen</button></div>
          <p class="hint">Neu erzeugen macht alle deine bisherigen Feed- und Direktlinks ungültig – auch die der eigenen Feeds.</p>
        </section>
      </div>
    `);
    fallbackImages(this.el);
    bindCopy(this.el);
    this.bind();
  }

  typeChoice(current) {
    return html`<div class="field"><span>Reihenfolge</span>
      <div class="segmented">
        <label class="choice"><input type="radio" name="type" value="episodic" ${raw(current === 'episodic' ? 'checked' : '')}>
          <b>Episoden</b><small>Neueste zuerst – für Sammlungen und Kanäle</small></label>
        <label class="choice"><input type="radio" name="type" value="serial" ${raw(current === 'serial' ? 'checked' : '')}>
          <b>Serie</b><small>Teil 1, 2, 3 … – für Hörbücher</small></label>
      </div></div>`;
  }

  newSubForm() {
    return html`<form class="stack" id="new-sub">
      <label class="field"><span>Kanal- oder Playlist-Link</span>
        <input name="url" type="url" inputmode="url" required autocapitalize="none" spellcheck="false"
               placeholder="https://www.youtube.com/@kanal oder …/playlist?list=…"></label>
      <div class="row">
        <label class="field grow"><span>Format</span><select name="format">${FORMATS.map(([v, l]) => html`<option value="${v}">${l}</option>`)}</select></label>
        <label class="field grow"><span>Prüfen</span><select name="interval_hours">${INTERVALS.map(([v, l]) => html`<option value="${v}" ${raw(v === 6 ? 'selected' : '')}>${l}</option>`)}</select></label>
      </div>
      <div class="field"><span>Beim Anlegen</span>
        <div class="segmented">
          <label class="choice"><input type="radio" name="initial" value="none" checked><b>Nur Neues</b><small>Ab jetzt erscheinende Videos</small></label>
          <label class="choice"><input type="radio" name="initial" value="latest"><b>Letzte</b><small>Die neuesten <input class="inline-num" name="initial_count" type="number" min="1" max="50" value="3"> Videos</small></label>
          <label class="choice"><input type="radio" name="initial" value="all"><b>Alle</b><small>Alles Vorhandene (z. B. ganzes Hörbuch)</small></label>
        </div></div>
      <label class="field"><span>Ordner in der Bibliothek (optional)</span><input name="collection" maxlength="120" placeholder="Name des Kanals bzw. der Playlist"></label>
      <label class="field"><span>Neue Videos zusätzlich in Feed legen</span>
        <select name="feed">
          <option value="none">Nein – nur Ordner</option>
          <option value="new:auto" selected>Neuen Feed anlegen (Playlist → Serie, Kanal → Episoden)</option>
          <option value="new:serial">Neuen Feed anlegen als Serie</option>
          <option value="new:episodic">Neuen Feed anlegen als Episoden</option>
          ${this.feeds.map((f) => html`<option value="existing:${f.id}">In „${f.title}“</option>`)}
        </select></label>
      <p class="hint">Downloads aus Abos zählen zu deinem Speicherkontingent. Je Prüfung werden höchstens so viele Videos betrachtet,
        wie Playlists maximal haben dürfen.</p>
      <button class="btn btn--primary" type="submit">Abo anlegen</button>
    </form>`;
  }

  subHtml(s) {
    const foreign = s.user_id !== myId();
    return html`<article class="card stack sub" data-sub="${s.id}">
      <div class="row">
        <div class="grow">
          <div class="row"><b class="clamp-2">${s.title}</b>
            <span class="badge badge--soft">${s.kind === 'channel' ? 'Kanal' : 'Playlist'}</span>
            ${!s.enabled ? html`<span class="badge badge--warn">Pausiert</span>` : ''}
            ${isAdmin() && foreign ? html`<span class="owner">${icons.user}${s.owner}</span>` : ''}</div>
          <div class="faint">${s.format.toUpperCase()} · ${intervalLabel(s.interval_hours)} · Ordner „${s.collection}“${s.feed_id ? ' · mit Feed' : ''}</div>
          <div class="faint">Zuletzt geprüft ${fmtRelative(s.last_checked_at)}${s.last_new_count ? ` · ${s.last_new_count} neu` : ''}${s.next_check_at ? ` · nächste Prüfung ${fmtRelative(s.next_check_at)}` : ''}</div>
        </div>
      </div>
      ${s.last_error ? html`<div class="job__error">${s.last_error}</div>` : ''}
      <div class="row">
        <button class="btn btn--sm" data-act="check">${icons.retry}<span>Jetzt prüfen</span></button>
        <button class="btn btn--sm" data-act="toggle">${s.enabled ? 'Pausieren' : 'Fortsetzen'}</button>
        <select class="input btn--sm sub-interval" data-act="interval" aria-label="Prüfintervall">
          ${INTERVALS.map(([v, l]) => html`<option value="${v}" ${raw(v === s.interval_hours ? 'selected' : '')}>${l}</option>`)}</select>
        ${s.feed_id && !foreign ? html`<a class="btn btn--sm" href="#/feeds/${s.feed_id}">${icons.rss}<span>Feed</span></a>` : ''}
        <button class="btn btn--sm btn--ghost btn--danger" data-act="delete" title="Abo löschen">${icons.trash}</button>
      </div>
    </article>`;
  }

  replaceSub(sub) {
    const i = this.subs.findIndex((s) => s.id === sub.id);
    if (i >= 0) this.subs[i] = sub;
    const node = this.el.querySelector(`[data-sub="${sub.id}"]`);
    if (node) node.outerHTML = String(this.subHtml(sub));
  }

  bind() {
    const $ = (sel) => this.el.querySelector(sel);
    $('#new-feed').addEventListener('submit', async (e) => {
      e.preventDefault();
      const el = e.currentTarget.elements;
      await busy(e.currentTarget.querySelector('button[type=submit]'), async () => {
        try {
          const feed = await api('/api/feeds', { method: 'POST', body: {
            title: el.title.value, description: el.description.value || null, type: el.type.value,
            collection: el.collection.value || null,
          } });
          location.hash = `#/feeds/${feed.id}`;
        } catch (err) { toast(err.message, 'err'); }
      });
    });

    $('#new-sub').addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const el = form.elements;
      const [mode, arg] = el.feed.value.split(':');
      const body = {
        url: el.url.value.trim(), format: el.format.value, interval_hours: Number(el.interval_hours.value),
        initial: el.initial.value, initial_count: Number(el.initial_count.value) || 3,
        collection: el.collection.value.trim() || null, feed_mode: mode,
      };
      if (mode === 'existing') body.feed_id = arg;
      if (mode === 'new' && arg !== 'auto') body.feed_type = arg;
      await busy(form.querySelector('button[type=submit]'), async () => {
        try {
          const sub = await api('/api/subscriptions', { method: 'POST', body });
          toast(sub.last_new_count ? `Abo angelegt – ${sub.last_new_count} Downloads gestartet.` : 'Abo angelegt. Neue Videos werden automatisch geladen.');
          await refreshJobs().catch(() => {});
          this.render();
        } catch (err) { toast(err.message, 'err', 7000); }
      });
    });

    const subs = $('#subs');
    subs.addEventListener('click', (e) => this.onSubAction(e));
    subs.addEventListener('change', (e) => this.onSubAction(e));

    $('#rotate').addEventListener('click', async (e) => {
      if (!confirm('Neue Feed-Links erzeugen? Bestehende Abos in Podcast-Apps funktionieren danach nicht mehr.')) return;
      await busy(e.currentTarget, async () => {
        try { await api('/api/feed/rotate', { method: 'POST' }); toast('Neue Feed-Links erzeugt.'); this.render(); } catch (err) { toast(err.message, 'err'); }
      });
    });
  }

  async onSubAction(e) {
    const ctrl = e.target.closest('[data-act]');
    if (!ctrl) return;
    const act = ctrl.dataset.act;
    if ((act === 'interval') !== (e.type === 'change')) return;
    const id = ctrl.closest('[data-sub]').dataset.sub;
    const sub = this.subs.find((s) => s.id === id);
    try {
      if (act === 'check') {
        await busy(ctrl, async () => {
          const res = await api(`/api/subscriptions/${id}/check`, { method: 'POST' });
          toast(res.last_error ? res.last_error : (res.last_new_count ? `${res.last_new_count} neue Videos eingereiht.` : 'Keine neuen Videos.'), res.last_error ? 'err' : 'ok');
          await refreshJobs().catch(() => {});
          this.replaceSub(res);
        });
      } else if (act === 'toggle') {
        this.replaceSub(await api(`/api/subscriptions/${id}`, { method: 'PATCH', body: { enabled: !sub.enabled } }));
      } else if (act === 'interval') {
        this.replaceSub(await api(`/api/subscriptions/${id}`, { method: 'PATCH', body: { interval_hours: Number(ctrl.value) } }));
        toast('Prüfintervall gespeichert.');
      } else if (act === 'delete') {
        if (!confirm(`Abo „${sub.title}“ löschen? Bereits geladene Dateien bleiben erhalten.`)) return;
        await api(`/api/subscriptions/${id}`, { method: 'DELETE' });
        this.subs = this.subs.filter((s) => s.id !== id);
        this.el.querySelector(`[data-sub="${id}"]`).remove();
      }
    } catch (err) { toast(err.message, 'err', 6000); }
  }
}
