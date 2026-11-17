// Download-Verwaltung: aktiv / fertig / fehlgeschlagen, Live-Fortschritt
import { api } from '../api.js';
import { isAdmin, myId, refreshJobs, store, upsertJob } from '../app.js';
import { applyBars, busy, fmtDateTime, fmtDuration, fmtEta, fmtSpeed, helpLink, html, icons, toast } from '../util.js';

const GROUPS = {
  active: ['queued', 'running', 'processing'],
  done: ['done'],
  failed: ['failed', 'cancelled'],
};
const LABELS = {
  queued: 'Wartet', running: 'Lädt', processing: 'Verarbeitet', done: 'Fertig', failed: 'Fehler', cancelled: 'Abgebrochen',
};
const FORMAT_LABEL = { m4a: 'M4A', mp3: 'MP3', opus: 'OPUS', flac: 'FLAC', mp4: 'MP4', webm: 'WebM' };

export class JobsView {
  constructor(el) {
    this.el = el;
    this.tab = sessionStorage.getItem('ytl:jobs-tab') || 'active';
  }

  async render() {
    this.el.innerHTML = String(html`
      <header class="page-head">
        <div><h1>Aufträge</h1><p>Downloads laufen auf dem Server weiter – auch wenn du die Seite schließt.</p></div>
        <div class="row"><button class="btn btn--sm" id="clear">${icons.trash}<span>Verlauf leeren</span></button>${helpLink('jobs')}</div>
      </header>
      <div class="tabs" role="tablist">
        <button class="tab" role="tab" data-tab="active">Aktiv<small data-count="active"></small></button>
        <button class="tab" role="tab" data-tab="done">Fertig<small data-count="done"></small></button>
        <button class="tab" role="tab" data-tab="failed">Fehler<small data-count="failed"></small></button>
      </div>
      <div id="list"></div>
    `);
    this.el.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
      this.tab = b.dataset.tab;
      sessionStorage.setItem('ytl:jobs-tab', this.tab);
      this.renderList();
    }));
    this.el.querySelector('#clear').addEventListener('click', async (e) => {
      const what = isAdmin() ? 'Fertige und fehlgeschlagene Aufträge aller Benutzer' : 'Fertige und fehlgeschlagene Aufträge';
      if (!confirm(`${what} aus dem Verlauf entfernen? Dateien in der Bibliothek bleiben erhalten.`)) return;
      await busy(e.currentTarget, async () => {
        try {
          const r = await api('/api/jobs', { method: 'DELETE' });
          await refreshJobs();
          toast(`${r.deleted} Einträge entfernt.`);
          this.renderList();
        } catch (err) { toast(err.message, 'err'); }
      });
    });
    this.el.querySelector('#list').addEventListener('click', (e) => this.onAction(e));
    await refreshJobs();
    this.renderList();
  }

  jobsIn(group) {
    return [...store.jobs.values()]
      .filter((j) => GROUPS[group].includes(j.status))
      .sort((a, b) => (group === 'active'
        ? a.created_at.localeCompare(b.created_at)
        : (b.finished_at || b.created_at).localeCompare(a.finished_at || a.created_at)));
  }

  renderList() {
    this.el.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('is-active', b.dataset.tab === this.tab));
    for (const g of Object.keys(GROUPS)) {
      const n = this.jobsIn(g).length;
      this.el.querySelector(`[data-count=${g}]`).textContent = n ? ` ${n}` : '';
    }
    const jobs = this.jobsIn(this.tab);
    const list = this.el.querySelector('#list');
    if (!jobs.length) {
      const text = { active: 'Keine laufenden Downloads.', done: 'Noch keine fertigen Downloads.', failed: 'Keine fehlgeschlagenen Downloads.' }[this.tab];
      list.innerHTML = String(html`<div class="empty">${icons.inbox}<p>${text}</p><a class="btn btn--primary" href="#/">Neuer Download</a></div>`);
      return;
    }
    list.innerHTML = String(html`<div class="card card--flush">${jobs.map((j) => this.jobHtml(j))}</div>`);
    applyBars(list);
  }

  jobHtml(j) {
    const active = GROUPS.active.includes(j.status);
    const indeterminate = j.status === 'queued' || (active && !j.progress);
    return html`<article class="job" data-job="${j.id}">
      ${j.thumbnail ? html`<img class="job__thumb" src="${j.thumbnail}" alt="" loading="lazy" referrerpolicy="no-referrer">` : html`<div class="job__thumb"></div>`}
      <div class="job__body">
        <div class="job__title clamp-2">${j.title || j.url}</div>
        <div class="job__meta">
          <span class="status status--${j.status}" data-f="status">${LABELS[j.status] || j.status}</span>
          <span class="badge badge--soft">${FORMAT_LABEL[j.format] || j.format}</span>
          ${isAdmin() && j.owner && j.user_id !== myId() ? html`<span class="owner">${icons.user}${j.owner}</span>` : ''}
          ${j.collection ? html`<span>${j.collection}</span>` : ''}
          ${j.duration ? html`<span>${fmtDuration(j.duration)}</span>` : ''}
          ${!active ? html`<span>${fmtDateTime(j.finished_at || j.created_at)}</span>` : ''}
        </div>
        ${active ? html`
          <div class="progress ${indeterminate ? 'progress--indeterminate' : ''}" data-f="bar-wrap"><div class="progress__bar" data-f="bar" data-progress="${j.progress || 0}"></div></div>
          <div class="job__meta" data-f="live">${this.liveText(j)}</div>` : ''}
        ${j.error && j.status !== 'cancelled' ? html`<div class="job__error">${j.error}</div>` : ''}
        <div class="job__actions">
          ${active ? html`<button class="btn btn--sm btn--danger" data-act="cancel">${icons.stop}<span>Abbrechen</span></button>` : ''}
          ${j.status === 'done' && j.media_id ? html`<a class="btn btn--sm btn--primary" href="#/media/${j.media_id}">${icons.play}<span>Öffnen</span></a>
            <a class="btn btn--sm" href="/api/media/${j.media_id}/download">${icons.download}<span>Laden</span></a>` : ''}
          ${['failed', 'cancelled'].includes(j.status) && j.kind === 'url' ? html`<button class="btn btn--sm" data-act="retry">${icons.retry}<span>Erneut</span></button>` : ''}
          ${!active ? html`<button class="btn btn--sm btn--ghost" data-act="delete" title="Aus Verlauf entfernen">${icons.trash}</button>` : ''}
        </div>
      </div>
    </article>`;
  }

  liveText(j) {
    const parts = [j.stage || ''];
    if (j.progress) parts.push(`${Math.round(j.progress * 100)} %`);
    if (j.speed) parts.push(fmtSpeed(j.speed));
    if (j.eta != null) parts.push(fmtEta(j.eta));
    return parts.filter(Boolean).join(' · ');
  }

  async onAction(e) {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const id = btn.closest('[data-job]').dataset.job;
    const act = btn.dataset.act;
    await busy(btn, async () => {
      try {
        if (act === 'cancel') upsertJob(await api(`/api/jobs/${id}/cancel`, { method: 'POST' }));
        if (act === 'retry') { upsertJob(await api(`/api/jobs/${id}/retry`, { method: 'POST' })); toast('Erneut eingereiht.'); }
        if (act === 'delete') { await api(`/api/jobs/${id}`, { method: 'DELETE' }); store.jobs.delete(id); }
        this.renderList();
      } catch (err) { toast(err.message, 'err'); }
    });
  }

  onEvent(evt) {
    if (evt.type !== 'job') { this.renderList(); return; }
    const j = evt.job;
    const node = this.el.querySelector(`[data-job="${j.id}"]`);
    const inTab = GROUPS[this.tab].includes(j.status);
    // Nur Fortschritt geändert -> Knoten direkt aktualisieren (kein Flackern)
    if (node && inTab && GROUPS.active.includes(j.status) && node.querySelector('[data-f=bar]')
        && node.querySelector('[data-f=status]').textContent === (LABELS[j.status] || j.status)) {
      const bar = node.querySelector('[data-f=bar]');
      bar.dataset.progress = j.progress || 0;
      node.querySelector('[data-f=bar-wrap]').classList.toggle('progress--indeterminate', !j.progress);
      node.querySelector('[data-f=live]').textContent = this.liveText(j);
      applyBars(node);
      return;
    }
    this.renderList();
  }
}
