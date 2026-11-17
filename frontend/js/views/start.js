// Startseite: URL analysieren, Format wählen, Download starten
import { api } from '../api.js';
import { store, upsertJob } from '../app.js';
import { busy, fmtDuration, html, icons, raw, toast } from '../util.js';

const AUDIO = [
  { id: 'm4a', label: 'M4A', note: 'AAC · empfohlen für iPhone/iPad' },
  { id: 'mp3', label: 'MP3', note: 'Podcast-kompatibel, universell' },
  { id: 'opus', label: 'OPUS', note: 'Sehr effizient, nicht jeder iOS-Player' },
  { id: 'flac', label: 'FLAC', note: 'Verlustfrei – nur bei verlustfreier Quelle' },
];
const VIDEO = [
  { id: 'mp4', label: 'MP4', note: 'H.264/AAC · iOS-kompatibel' },
  { id: 'webm', label: 'WebM', note: 'VP9/Opus · nicht für iOS-Fotos' },
];
const BITRATES = ['128k', '160k', '192k', '256k', '320k'];
const HEIGHTS = [2160, 1440, 1080, 720, 480, 360];
const DRAFT_KEY = 'ytl:last-format';

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}'); } catch { return {}; }
}
function savePrefs(p) {
  try { localStorage.setItem(DRAFT_KEY, JSON.stringify(p)); } catch { /* ignore */ }
}

export class StartView {
  constructor(el, _params, query) {
    this.el = el;
    this.query = query;
    this.info = null;
  }

  render() {
    this.el.innerHTML = String(html`
      <header class="page-head">
        <div>
          <h1>Neuer Download</h1>
          <p>YouTube-Link einfügen, Format wählen, fertig.</p>
        </div>
      </header>
      <form class="card stack" id="analyze-form" novalidate>
        <div class="url-bar">
          <input class="input" id="url" name="url" type="url" inputmode="url" autocomplete="off"
                 autocapitalize="none" spellcheck="false" placeholder="https://www.youtube.com/watch?v=…"
                 aria-label="YouTube-Link" required>
          <button class="btn" type="button" id="paste" title="Aus Zwischenablage einfügen">${icons.paste}<span>Einfügen</span></button>
          <button class="btn btn--primary" type="submit" id="analyze">Analysieren</button>
        </div>
        <p class="hint">Bitte nur Inhalte herunterladen, für die du die Rechte bzw. eine Erlaubnis hast.
          Geschützte Inhalte (DRM, Mitgliedschaften, Altersprüfung) werden nicht unterstützt.</p>
      </form>
      <div id="result" class="stack"></div>
    `);
    const form = this.el.querySelector('#analyze-form');
    const input = this.el.querySelector('#url');
    form.addEventListener('submit', (e) => { e.preventDefault(); this.analyze(input.value); });
    const pasteBtn = this.el.querySelector('#paste');
    if (!navigator.clipboard || !navigator.clipboard.readText) pasteBtn.hidden = true;
    pasteBtn.addEventListener('click', async () => {
      try {
        const text = (await navigator.clipboard.readText()).trim();
        if (text) { input.value = text; this.analyze(text); }
      } catch {
        toast('Zugriff auf die Zwischenablage nicht erlaubt.', 'err');
      }
    });
    // Unterstützt z. B. iOS-Kurzbefehle: …/#/?url=<link>
    const preset = this.query.get('url');
    if (preset) { input.value = preset; this.analyze(preset); }
  }

  async analyze(url, target = null) {
    const result = this.el.querySelector('#result');
    const btn = this.el.querySelector('#analyze');
    if (!url.trim()) { toast('Bitte einen Link eingeben.', 'err'); return; }
    result.innerHTML = '<div class="skeleton"></div>';
    await busy(target || btn, async () => {
      try {
        this.info = await api('/api/analyze', { method: 'POST', body: { url } });
        this.renderResult();
      } catch (err) {
        result.innerHTML = String(html`<div class="notice notice--err">${err.message}</div>`);
      }
    });
  }

  renderResult() {
    const info = this.info;
    const result = this.el.querySelector('#result');
    const prefs = loadPrefs();
    const sys = (store.system && store.system.settings) || {};
    const defFormat = prefs.format || sys.default_audio_format || 'm4a';
    const isPlaylist = info.type === 'playlist';
    const lossless = !isPlaylist && info.audio && info.audio.lossless;
    const heights = isPlaylist ? HEIGHTS : HEIGHTS.filter((h) => !info.video_heights.length || info.video_heights.some((v) => v >= h * 0.9));

    const choice = (f, kind) => {
      const disabled = f.id === 'flac' && !lossless;
      const checked = f.id === defFormat && !disabled;
      return html`<label class="choice">
        <input type="radio" name="format" value="${f.id}" data-kind="${kind}" ${raw(checked ? 'checked' : '')} ${raw(disabled ? 'disabled' : '')}>
        <b>${f.label}</b><small>${disabled ? 'Quelle bietet kein verlustfreies Audio' : f.note}</small>
      </label>`;
    };

    const entries = isPlaylist ? html`
      <section class="card stack">
        <div class="row">
          <h3 class="grow">${info.entries.length} Videos</h3>
          <label class="row small"><input type="checkbox" id="select-all" checked> Alle</label>
        </div>
        ${info.truncated ? html`<div class="notice notice--warn">Es werden nur die ersten ${info.max_items} Einträge angezeigt.</div>` : ''}
        <div class="entries" id="entries">
          ${info.entries.map((e, i) => html`<label class="entry">
            <input type="checkbox" data-idx="${i}" checked>
            ${e.thumbnail ? html`<img src="${e.thumbnail}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}
            <span class="grow"><span class="clamp-2">${e.title}</span><span class="faint">${fmtDuration(e.duration)}</span></span>
          </label>`)}
        </div>
        <label class="field"><span>Ordner in der Bibliothek</span>
          <input id="collection" value="${info.title}" maxlength="200"></label>
      </section>` : '';

    result.innerHTML = String(html`
      <section class="card">
        <div class="preview">
          <div class="preview__thumb">
            ${info.thumbnail ? html`<img src="${info.thumbnail}" alt="" referrerpolicy="no-referrer">` : ''}
            ${!isPlaylist && info.duration ? html`<span class="badge">${fmtDuration(info.duration)}</span>` : ''}
            ${isPlaylist ? html`<span class="badge">Playlist</span>` : ''}
          </div>
          <div class="preview__meta">
            <h2>${info.title}</h2>
            <p class="muted">${info.channel || ''}</p>
            ${!isPlaylist && info.audio && info.audio.best_abr ? html`<p class="faint">Beste Audiospur: ~${info.audio.best_abr} kbit/s (${info.audio.codecs.join(', ')})</p>` : ''}
            ${info.description ? html`<details class="desc"><summary>Beschreibung</summary><div class="pre">${info.description}</div></details>` : ''}
            ${info.playlist_url ? html`<div class="notice">Dieser Link gehört zu einer Playlist.
              <button class="btn btn--sm" type="button" id="load-playlist">Ganze Playlist laden</button></div>` : ''}
          </div>
        </div>
      </section>
      ${entries}
      <form class="card stack" id="job-form">
        <div>
          <p class="section-title">Audio</p>
          <div class="segmented">${AUDIO.map((f) => choice(f, 'audio'))}</div>
        </div>
        <div>
          <p class="section-title">Video</p>
          <div class="segmented">${VIDEO.map((f) => choice(f, 'video'))}</div>
        </div>
        <hr class="divider">
        <div class="stack" id="audio-opts">
          <label class="field"><span>Bitrate</span>
            <select id="bitrate">${BITRATES.map((b) => html`<option value="${b}" ${raw(b === (prefs.bitrate || sys.default_audio_bitrate || '192k') ? 'selected' : '')}>${b.replace('k', '')} kbit/s${b === '192k' ? ' (empfohlen)' : ''}</option>`)}</select>
          </label>
          <label class="toggle"><span>Lautstärke normalisieren<small>Einheitlich -16 LUFS (Podcast-Standard)</small></span>
            <input type="checkbox" id="normalize" ${raw((prefs.normalize ?? sys.default_normalize ?? true) ? 'checked' : '')}></label>
          <label class="toggle"><span>Stille am Anfang/Ende entfernen<small>Nur längere Pausen (&gt; 2 s)</small></span>
            <input type="checkbox" id="trim" ${raw((prefs.trim_silence ?? sys.default_trim_silence ?? false) ? 'checked' : '')}></label>
        </div>
        <div class="stack" id="video-opts" hidden>
          <label class="field"><span>Maximale Auflösung</span>
            <select id="height"><option value="">Beste verfügbare</option>
              ${heights.map((h) => html`<option value="${h}" ${raw(String(h) === String(prefs.max_height || '1080') ? 'selected' : '')}>${h}p</option>`)}
            </select></label>
          <p class="hint">MP4 wird bei Bedarf nach H.264 umgewandelt, damit es auf iPhone/iPad abspielbar ist.</p>
        </div>
        <label class="toggle"><span>Originaldatei behalten<small>Zusätzlich die unbearbeitete Quelle speichern</small></span>
          <input type="checkbox" id="keep" ${raw((prefs.keep_original ?? sys.keep_original ?? false) ? 'checked' : '')}></label>
        <button class="btn btn--primary btn--lg btn--block" type="submit" id="start">${icons.download}<span>Download starten</span></button>
      </form>
    `);

    const form = result.querySelector('#job-form');
    const syncKind = () => {
      const sel = form.querySelector('input[name=format]:checked');
      const kind = sel ? sel.dataset.kind : 'audio';
      form.querySelector('#audio-opts').hidden = kind !== 'audio';
      form.querySelector('#video-opts').hidden = kind !== 'video';
    };
    form.addEventListener('change', syncKind);
    if (!form.querySelector('input[name=format]:checked')) form.querySelector('input[value=m4a]').checked = true;
    syncKind();

    const selectAll = result.querySelector('#select-all');
    if (selectAll) {
      selectAll.addEventListener('change', () => {
        result.querySelectorAll('#entries input[type=checkbox]').forEach((c) => { c.checked = selectAll.checked; });
      });
    }
    const loadPl = result.querySelector('#load-playlist');
    if (loadPl) {
      loadPl.addEventListener('click', () => {
        this.el.querySelector('#url').value = info.playlist_url;
        this.analyze(info.playlist_url, loadPl);
      });
    }
    form.addEventListener('submit', (e) => { e.preventDefault(); this.submit(form); });
  }

  async submit(form) {
    const info = this.info;
    const fmt = form.querySelector('input[name=format]:checked').value;
    const options = {
      bitrate: form.querySelector('#bitrate').value,
      normalize: form.querySelector('#normalize').checked,
      trim_silence: form.querySelector('#trim').checked,
      keep_original: form.querySelector('#keep').checked,
      max_height: form.querySelector('#height').value ? Number(form.querySelector('#height').value) : null,
    };
    savePrefs({ format: fmt, ...options });
    if (['m4a', 'mp3', 'opus', 'flac'].includes(fmt)) options.max_height = null;
    const body = { url: info.url, format: fmt, options };
    if (info.type === 'playlist') {
      const picked = [...this.el.querySelectorAll('#entries input[type=checkbox]')]
        .filter((c) => c.checked)
        .map((c) => ({ ...info.entries[Number(c.dataset.idx)], index: Number(c.dataset.idx) + 1 }));
      if (!picked.length) { toast('Bitte mindestens ein Video auswählen.', 'err'); return; }
      body.entries = picked;
      body.collection = this.el.querySelector('#collection').value.trim() || info.title;
    } else {
      Object.assign(body, { title: info.title, channel: info.channel, thumbnail: info.thumbnail, duration: info.duration });
    }
    await busy(form.querySelector('#start'), async () => {
      try {
        const res = await api('/api/jobs', { method: 'POST', body });
        res.jobs.forEach(upsertJob);
        toast(res.jobs.length > 1 ? `${res.jobs.length} Downloads eingereiht.` : 'Download gestartet.');
        location.hash = '#/jobs';
      } catch (err) {
        toast(err.message, 'err', 6000);
      }
    });
  }
}
