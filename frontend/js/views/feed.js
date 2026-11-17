// Eigener Feed: Titel, Beschreibung, Typ, Cover, Episoden (hinzufügen, sortieren, entfernen)
import { api, upload } from '../api.js';
import {
  bindCopy, busy, fallbackImages, fmtDuration, html, icons, raw, subscribeBlock, toast,
} from '../util.js';

export class FeedView {
  constructor(el, params) {
    this.el = el;
    this.id = params[0];
    this.q = '';
  }

  async render() {
    this.feed = await api(`/api/feeds/${this.id}`);
    const f = this.feed;
    this.el.innerHTML = String(html`
      <header class="page-head">
        <a class="btn btn--sm btn--ghost" href="#/feeds">${icons.back}<span>Feeds</span></a>
      </header>
      <div class="detail">
        <div class="stack">
          <div class="detail__art" id="cover-box">
            <img src="/api/feeds/${f.id}/cover?v=${f.updated_at}" alt="" data-fallback>
          </div>
          <form class="row" id="cover-form">
            <label class="btn btn--sm grow file-btn">${icons.edit}<span>Cover wählen</span>
              <input type="file" name="file" accept="image/png,image/jpeg,image/webp" hidden></label>
            ${f.has_cover ? html`<button class="btn btn--sm btn--ghost" type="button" id="cover-reset">Entfernen</button>` : ''}
          </form>
          <p class="hint">${f.has_cover ? 'Eigenes Cover.' : 'Ohne eigenes Cover wird das Bild der ersten Episode verwendet.'} Ideal: quadratisch, 1400×1400 px.</p>
        </div>
        <div class="stack">
          <div>
            <h1>${f.title}</h1>
            <p class="muted">${f.type === 'serial' ? 'Serie' : 'Episoden'} · ${f.item_count} ${f.item_count === 1 ? 'Episode' : 'Episoden'}${f.total_duration ? ` · ${fmtDuration(f.total_duration)}` : ''}</p>
          </div>
          <section class="card stack">${subscribeBlock(f.url, 'In Podcast-App abonnieren')}</section>

          <details class="card" id="edit-box">
            <summary class="row">${icons.edit}<b>Titel, Beschreibung, Reihenfolge</b></summary>
            <form class="stack" id="edit">
              <label class="field"><span>Titel</span><input name="title" required maxlength="120" value="${f.title}"></label>
              <label class="field"><span>Beschreibung</span><textarea name="description" maxlength="4000">${f.description || ''}</textarea></label>
              <div class="field"><span>Reihenfolge</span>
                <div class="segmented">
                  <label class="choice"><input type="radio" name="type" value="episodic" ${raw(f.type === 'episodic' ? 'checked' : '')}>
                    <b>Episoden</b><small>Zuletzt hinzugefügte zuerst</small></label>
                  <label class="choice"><input type="radio" name="type" value="serial" ${raw(f.type === 'serial' ? 'checked' : '')}>
                    <b>Serie</b><small>Genau in der Reihenfolge unten (Teil 1, 2, 3 …)</small></label>
                </div></div>
              <button class="btn btn--primary" type="submit">Speichern</button>
            </form>
          </details>

          <section class="stack">
            <div class="row"><h2 class="grow">Episoden</h2>
              ${f.items.length > 1 ? html`<select class="input btn--sm" id="sort" aria-label="Sortieren">
                <option value="">Sortieren …</option><option value="track">nach Teil-Nr.</option>
                <option value="title">nach Titel</option><option value="published">nach Veröffentlichung</option>
                <option value="created">nach Download-Datum</option></select>` : ''}</div>
            ${f.items.length ? html`<ol class="card card--flush episodes" id="items">${f.items.map((m, i) => html`
              <li class="episode" data-id="${m.id}">
                <span class="episode__no">${i + 1}</span>
                <div class="grow">
                  <div class="clamp-2">${m.title}</div>
                  <div class="faint">${m.track ? `Teil ${m.track} · ` : ''}${fmtDuration(m.duration)} · ${m.format.toUpperCase()}</div>
                </div>
                <div class="episode__actions">
                  <button class="btn btn--sm btn--ghost icon-btn" data-act="up" title="Nach oben" ${raw(i === 0 ? 'disabled' : '')}>${icons.up}</button>
                  <button class="btn btn--sm btn--ghost icon-btn" data-act="down" title="Nach unten" ${raw(i === f.items.length - 1 ? 'disabled' : '')}>${icons.down}</button>
                  <button class="btn btn--sm btn--ghost icon-btn btn--danger" data-act="remove" title="Aus Feed entfernen">${icons.close}</button>
                </div>
              </li>`)}</ol>` : html`<p class="muted small">Noch keine Episoden. Füge unten Dateien aus deiner Bibliothek hinzu.</p>`}
          </section>

          <details class="card" id="add-box" ${raw(f.items.length ? '' : 'open')}>
            <summary class="row">${icons.plus}<b>Dateien hinzufügen</b></summary>
            <div class="stack">
              <input class="input" id="add-q" type="search" placeholder="Bibliothek durchsuchen …" autocomplete="off">
              <div class="entries" id="add-list"></div>
              <button class="btn btn--primary" id="add-btn" disabled>Ausgewählte hinzufügen</button>
            </div>
          </details>

          <button class="btn btn--danger" id="delete">${icons.trash}<span>Feed löschen</span></button>
          <p class="hint">Löschen entfernt nur den Feed – die Dateien bleiben in der Bibliothek.</p>
        </div>
      </div>
    `);
    fallbackImages(this.el);
    bindCopy(this.el);
    this.bind();
    this.loadCandidates();
  }

  async loadCandidates() {
    const params = new URLSearchParams({ sort: 'created', order: 'desc', limit: '300' });
    if (this.q) params.set('q', this.q);
    const items = await api(`/api/media?${params}`);
    const inFeed = new Set(this.feed.items.map((m) => m.id));
    const list = this.el.querySelector('#add-list');
    if (!list) return;
    const free = items.filter((m) => !inFeed.has(m.id));
    list.innerHTML = free.length ? String(html`${free.map((m) => html`<label class="entry">
        <input type="checkbox" value="${m.id}">
        ${m.has_cover ? html`<img src="/api/media/${m.id}/cover" alt="" loading="lazy">` : ''}
        <span class="grow"><span class="clamp-2">${m.title}</span>
          <span class="faint">${m.collection ? `${m.collection} · ` : ''}${m.track ? `Teil ${m.track} · ` : ''}${fmtDuration(m.duration)}</span></span>
      </label>`)}`) : '<p class="muted small entry">Keine weiteren Dateien gefunden.</p>';
    this.updateAddButton();
  }

  updateAddButton() {
    const n = this.el.querySelectorAll('#add-list input:checked').length;
    const btn = this.el.querySelector('#add-btn');
    btn.disabled = n === 0;
    btn.textContent = n ? `${n} hinzufügen` : 'Ausgewählte hinzufügen';
  }

  async save(promise, msg) {
    try {
      this.feed = await promise;
      if (msg) toast(msg);
      const open = ['edit-box', 'add-box'].filter((id) => this.el.querySelector(`#${id}`)?.open);
      await this.render();
      open.forEach((id) => { const d = this.el.querySelector(`#${id}`); if (d) d.open = true; });
    } catch (err) { toast(err.message, 'err', 6000); }
  }

  bind() {
    const $ = (sel) => this.el.querySelector(sel);
    const f = this.feed;
    $('#edit').addEventListener('submit', async (e) => {
      e.preventDefault();
      const el = e.currentTarget.elements;
      await busy(e.currentTarget.querySelector('button'), () => this.save(api(`/api/feeds/${f.id}`, {
        method: 'PATCH', body: { title: el.title.value, description: el.description.value || null, type: el.type.value },
      }), 'Gespeichert.'));
    });
    const sort = $('#sort');
    if (sort) {
      sort.addEventListener('change', () => {
        if (sort.value) this.save(api(`/api/feeds/${f.id}/sort`, { method: 'POST', body: { by: sort.value } }), 'Neu sortiert.');
      });
    }
    const items = $('#items');
    if (items) {
      items.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-act]');
        if (!btn) return;
        const ids = f.items.map((m) => m.id);
        const id = btn.closest('[data-id]').dataset.id;
        const i = ids.indexOf(id);
        if (btn.dataset.act === 'remove') {
          this.save(api(`/api/feeds/${f.id}/items/${id}`, { method: 'DELETE' }));
          return;
        }
        const j = btn.dataset.act === 'up' ? i - 1 : i + 1;
        if (j < 0 || j >= ids.length) return;
        [ids[i], ids[j]] = [ids[j], ids[i]];
        this.save(api(`/api/feeds/${f.id}/items`, { method: 'PUT', body: { media_ids: ids } }));
      });
    }
    let timer = null;
    $('#add-q').addEventListener('input', (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { this.q = e.target.value.trim(); this.loadCandidates(); }, 250);
    });
    $('#add-list').addEventListener('change', () => this.updateAddButton());
    $('#add-btn').addEventListener('click', async (e) => {
      const ids = [...this.el.querySelectorAll('#add-list input:checked')].map((c) => c.value);
      await busy(e.currentTarget, () => this.save(api(`/api/feeds/${f.id}/items`, { method: 'POST', body: { media_ids: ids } }),
        `${ids.length} hinzugefügt.`));
    });
    $('#cover-form input[type=file]').addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const data = new FormData();
      data.append('file', file);
      await this.save(upload(`/api/feeds/${f.id}/cover`, data, () => {}), 'Cover gespeichert.');
    });
    const reset = $('#cover-reset');
    if (reset) reset.addEventListener('click', () => this.save(api(`/api/feeds/${f.id}/cover`, { method: 'DELETE' }), 'Cover entfernt.'));
    $('#delete').addEventListener('click', async (e) => {
      if (!confirm(`Feed „${f.title}“ löschen? Abos in Podcast-Apps funktionieren danach nicht mehr.`)) return;
      await busy(e.currentTarget, async () => {
        try { await api(`/api/feeds/${f.id}`, { method: 'DELETE' }); toast('Feed gelöscht.'); location.hash = '#/feeds'; } catch (err) { toast(err.message, 'err'); }
      });
    });
  }

  onEvent(evt) {
    if (evt.type === 'media' || evt.type === 'media_deleted') {
      api(`/api/feeds/${this.id}`).then((feed) => {
        if (feed.item_count !== this.feed.item_count) this.save(Promise.resolve(feed));
      }).catch(() => {});
    }
  }
}
