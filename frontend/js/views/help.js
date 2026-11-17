// Hilfe für Endbenutzer: einfache Sprache, Suche, aufklappbare Themen, Direktlinks (#/help?topic=…)
import { appName, isAdmin } from '../app.js';
import { esc, html, icons, raw } from '../util.js';

// Kleine Bausteine für gut lesbare Texte
const steps = (...items) => raw(`<ol class="help-steps">${items.map((i) => `<li>${i}</li>`).join('')}</ol>`);
const list = (...items) => raw(`<ul class="help-list">${items.map((i) => `<li>${i}</li>`).join('')}</ul>`);
const tip = (text) => raw(`<p class="notice help-tip">💡 ${text}</p>`);
const warn = (text) => raw(`<p class="notice notice--warn help-tip">⚠️ ${text}</p>`);
const go = (hash, label) => `<a href="${hash}">${label}</a>`;

function topics(name) {
  const n = esc(name); // vom Admin frei wählbar -> escapen
  return [
    {
      id: 'start', short: 'Schnellstart', title: 'Schnellstart in 3 Schritten', body: [
        `<p>Mit ${n} lädst du Videos oder nur den Ton herunter und hast die Dateien danach auf iPhone, iPad oder Computer –
          auch ohne Internet.</p>`,
        steps(
          `Kopiere den Link eines YouTube-Videos (in der YouTube-App: <b>Teilen → Link kopieren</b>).`,
          `Öffne ${go('#/', '<b>Neu</b>')}, füge den Link ein (Feld antippen → <b>Einfügen</b>) und tippe auf <b>Analysieren</b>.`,
          `Wähle ein Format (für Hörbücher und Podcasts einfach <b>M4A</b> lassen) und tippe auf <b>Download starten</b>.`,
        ),
        `<p>Den Fortschritt siehst du unter ${go('#/jobs', '<b>Aufträge</b>')}. Ist der Download fertig, findest du die Datei in der
          ${go('#/library', '<b>Bibliothek</b>')}.</p>`,
        tip('Du musst nicht warten: Downloads laufen auf dem Server weiter, auch wenn du die Seite schließt oder das Handy sperrst.'),
      ],
    },
    {
      id: 'download', short: 'Download', title: 'Einen Download starten', body: [
        steps(
          `Gehe auf ${go('#/', '<b>Neu</b>')}.`,
          `Füge den YouTube-Link ein und tippe auf <b>Analysieren</b>. Du siehst dann Titel, Bild, Länge und Kanal.`,
          `Wähle unter <b>Audio</b> oder <b>Video</b> das gewünschte Format.`,
          `Prüfe die Einstellungen (siehe unten) und tippe auf <b>Download starten</b>.`,
        ),
        `<p><b>Die Einstellungen kurz erklärt:</b></p>`,
        list(
          `<b>Bitrate</b> – die Tonqualität. 192 kbit/s ist sehr gut und spart Platz. Höher lohnt sich selten.`,
          `<b>Lautstärke normalisieren</b> – macht alle Dateien gleich laut. So musst du nicht ständig nachregeln. Empfohlen: an.`,
          `<b>Stille am Anfang/Ende entfernen</b> – schneidet lange Pausen (mehr als 2 Sekunden) am Anfang und Ende weg.`,
          `<b>Originaldatei behalten</b> – speichert zusätzlich die unbearbeitete Datei. Braucht mehr Speicher; meist nicht nötig.`,
          `<b>Maximale Auflösung</b> (nur Video) – 1080p ist für Handy und Tablet mehr als genug. Kleinere Werte sparen Platz.`,
        ),
        tip('Gehört der Link zu einer Playlist, erscheint der Knopf <b>Ganze Playlist laden</b>. Damit holst du alle Teile auf einmal.'),
        warn('Lade nur Inhalte herunter, für die du die Rechte bzw. eine Erlaubnis hast. Geschützte Inhalte (z. B. Mitgliedschaften, Altersprüfung, DRM) funktionieren bewusst nicht.'),
      ],
    },
    {
      id: 'formats', short: 'Formate', title: 'Welches Format soll ich nehmen?', body: [
        `<table class="help-table"><thead><tr><th>Format</th><th>Wofür?</th></tr></thead><tbody>
          <tr><td><b>M4A</b></td><td>Die beste Wahl für iPhone und iPad. Für Hörbücher, Podcasts und Musik.</td></tr>
          <tr><td><b>MP3</b></td><td>Läuft wirklich überall, auch auf alten Geräten und im Auto.</td></tr>
          <tr><td><b>OPUS</b></td><td>Sehr klein bei guter Qualität. Nicht jede iPhone-App kann es abspielen (VLC schon).</td></tr>
          <tr><td><b>FLAC</b></td><td>Verlustfrei. Nur möglich, wenn die Quelle das anbietet – bei YouTube praktisch nie.</td></tr>
          <tr><td><b>MP4</b></td><td>Video für iPhone, iPad, Fernseher und Computer.</td></tr>
          <tr><td><b>WebM</b></td><td>Video für Computer. Auf dem iPhone besser MP4 nehmen.</td></tr>
        </tbody></table>`,
        tip('Im Zweifel: <b>M4A</b> für Ton, <b>MP4</b> für Video.'),
      ],
    },
    {
      id: 'playlist', short: 'Playlists', title: 'Playlists und Hörbücher mit mehreren Teilen', body: [
        steps(
          `Füge den Link der Playlist ein (oder einen Video-Link aus der Playlist und tippe dann auf <b>Ganze Playlist laden</b>).`,
          `Du siehst alle Videos mit Haken. Entferne die Haken bei Teilen, die du nicht willst.`,
          `Unter <b>Ordner in der Bibliothek</b> steht der Name der Playlist. Du kannst ihn ändern, z. B. in „Der Hobbit“.`,
          `Tippe auf <b>Download starten</b>.`,
        ),
        `<p>Alle Teile landen im selben Ordner. ${n} merkt sich dabei die Reihenfolge als <b>Teil-Nummer</b> (Teil 1, Teil 2 …).</p>`,
        tip(`Möchtest du das Hörbuch in einer Podcast-App in der richtigen Reihenfolge hören? Lege daraus einen eigenen Feed als
          <b>Serie</b> an – siehe ${go('#/help?topic=customfeeds', 'Eigene Feeds und Serien')}.`),
      ],
    },
    {
      id: 'jobs', short: 'Aufträge', title: 'Aufträge: Was passiert gerade?', body: [
        `<p>Unter ${go('#/jobs', '<b>Aufträge</b>')} siehst du alle Downloads. Die Zahl am Symbol zeigt, wie viele gerade laufen.</p>`,
        list(
          `<b>Wartet</b> – der Auftrag ist in der Warteschlange und startet gleich.`,
          `<b>Lädt</b> – die Datei wird heruntergeladen (mit Geschwindigkeit und Restzeit).`,
          `<b>Verarbeitet</b> – die Datei wird umgewandelt, die Lautstärke angepasst und das Cover eingefügt.
            Bei langen Hörbüchern kann das einige Minuten dauern.`,
          `<b>Fertig</b> – tippe auf <b>Öffnen</b> oder <b>Laden</b>.`,
          `<b>Fehler</b> – die Meldung sagt dir, was los ist. Mit <b>Erneut</b> versuchst du es noch einmal.`,
        ),
        `<p><b>Abbrechen</b> stoppt einen laufenden Download. <b>Verlauf leeren</b> räumt nur die Liste auf – deine Dateien bleiben erhalten.</p>`,
      ],
    },
    {
      id: 'library', short: 'Bibliothek', title: 'Bibliothek: Dateien finden, abspielen, bearbeiten', body: [
        list(
          `<b>Suchen</b>: oben nach Titel, Kanal oder Ordner suchen.`,
          `<b>Filtern</b>: Alle, nur Audio oder nur Video – und nach Ordner.`,
          `<b>Sortieren</b>: neueste, älteste, nach Titel, Länge oder Größe.`,
          `<b>Abspielen</b>: Datei antippen – der Player startet direkt im Browser.`,
          `<b>Bearbeiten</b>: in der Datei auf <b>Metadaten bearbeiten</b>. Du kannst Titel, Interpret, Ordner und Beschreibung ändern.
            Der Dateiname wird automatisch angepasst.`,
          `<b>Löschen</b>: in der Datei ganz unten auf <b>Datei löschen</b>. Das kann nicht rückgängig gemacht werden.`,
        ),
      ],
    },
    {
      id: 'ios', short: 'iPhone & iPad', title: 'Dateien aufs iPhone oder iPad holen', body: [
        `<p><b>Weg 1 – in die Dateien-App (am einfachsten):</b></p>`,
        steps(
          `Öffne die Datei in der ${go('#/library', 'Bibliothek')} und tippe auf <b>Herunterladen</b>.`,
          `Safari lädt die Datei. Du findest sie danach in der App <b>Dateien → Downloads</b>.`,
          `Von dort kannst du sie mit <b>Teilen</b> an eine andere App schicken, z. B. VLC oder einen Audioplayer.`,
        ),
        `<p><b>Weg 2 – Teilen:</b> Tippe in der Datei auf <b>Teilen …</b>. Beim ersten Tippen wird die Datei vorbereitet,
          beim zweiten Tippen öffnet sich das iPhone-Teilen-Menü (z. B. AirDrop, Nachrichten, „In Dateien sichern“).</p>`,
        `<p><b>Weg 3 – VLC:</b> Ist die App <b>VLC</b> installiert, tippe auf <b>In VLC öffnen</b>. VLC lädt die Datei selbst herunter.</p>`,
        tip(`Am bequemsten für Hörbücher und Podcasts ist eine Podcast-App: Die lädt neue Folgen automatisch und merkt sich, wo du
          aufgehört hast. Siehe ${go('#/help?topic=feeds', 'Podcast-Feeds abonnieren')}.`),
      ],
    },
    {
      id: 'homescreen', short: 'Home-Bildschirm', title: `${name} wie eine App auf den Home-Bildschirm legen`, body: [
        steps(
          `Öffne ${n} in <b>Safari</b>.`,
          `Tippe unten auf das <b>Teilen</b>-Symbol (Quadrat mit Pfeil nach oben).`,
          `Wähle <b>Zum Home-Bildschirm</b> und tippe auf <b>Hinzufügen</b>.`,
        ),
        `<p>Jetzt startet ${n} wie eine normale App – ohne Adressleiste.</p>`,
        tip('Wurden Name oder Symbol der App geändert, entferne das Symbol vom Home-Bildschirm und füge es neu hinzu. Erst dann zeigt das iPhone die neue Version.'),
      ],
    },
    {
      id: 'feeds', short: 'Podcast-Feeds', title: 'Podcast-Feeds: Deine Bibliothek in der Podcast-App', body: [
        `<p>Ein <b>Feed</b> ist ein privater Link, den du in einer Podcast-App abonnierst. Die App zeigt dann deine Dateien wie
          Podcast-Folgen an, lädt neue automatisch und merkt sich, wo du stehst.</p>`,
        `<p>Deine Feed-Links findest du unter ${go('#/feeds', '<b>Feeds</b>')}:</p>`,
        list(
          `<b>Alle Audiodateien</b> – alles, was du als Ton geladen hast.`,
          `<b>Alle Videos</b> – alle Videos.`,
          `<b>Ein Feed pro Ordner</b> – z. B. nur ein Hörbuch.`,
          `<b>Eigene Feeds</b> – die du selbst zusammenstellst (siehe nächstes Thema).`,
        ),
        `<p><b>So abonnierst du einen Feed:</b></p>`,
        list(
          `<b>Apple Podcasts:</b> Tippe beim Feed auf <b>Apple Podcasts</b>. Klappt das nicht: Link mit dem Kopieren-Knopf kopieren,
            in Apple Podcasts auf <b>Mediathek → ⋯ (oben rechts) → Sendung per URL folgen</b> tippen und den Link einfügen.`,
          `<b>Overcast</b> oder <b>Pocket Casts:</b> den passenden Knopf antippen – die App öffnet sich und fragt nach.`,
          `<b>Andere Apps:</b> Link kopieren und in der App unter „Podcast per URL hinzufügen“ einfügen.`,
        ),
        warn('Der Feed-Link ist wie ein Passwort: Wer ihn hat, kann deine Dateien hören. Gib ihn nicht weiter. Mit <b>Feed-Links neu erzeugen</b> machst du alle alten Links ungültig – dann musst du in der Podcast-App neu abonnieren.'),
        tip('Deine Feeds sind privat und tauchen nicht im öffentlichen Podcast-Verzeichnis auf.'),
      ],
    },
    {
      id: 'customfeeds', short: 'Eigene Feeds', title: 'Eigene Feeds und Serien (z. B. „Einschlafen“ oder ein Hörbuch)', body: [
        `<p>Mit eigenen Feeds stellst du selbst zusammen, was in deiner Podcast-App erscheint – mit eigenem Namen und Bild.</p>`,
        `<p><b>Feed anlegen:</b></p>`,
        steps(
          `Gehe auf ${go('#/feeds', '<b>Feeds</b>')} und tippe auf <b>Neuer Feed</b>.`,
          `Gib einen Titel ein, z. B. „Einschlafen“.`,
          `Wähle <b>Episoden</b> oder <b>Serie</b> (Erklärung unten).`,
          `Optional: Unter <b>Inhalt übernehmen</b> einen ganzen Ordner wählen, z. B. ein Hörbuch.`,
          `Tippe auf <b>Feed anlegen</b>.`,
        ),
        `<p><b>Dateien hinzufügen:</b> im Feed auf <b>Dateien hinzufügen</b>, Dateien anhaken und hinzufügen. Oder in der Bibliothek
          eine Datei öffnen und unter <b>In eigenen Feeds</b> den Feed antippen.</p>`,
        `<p><b>Reihenfolge ändern:</b> mit den Pfeilen ↑ ↓ – oder über <b>Sortieren</b> (z. B. nach Teil-Nummer). Mit ✕ entfernst du eine
          Folge aus dem Feed (die Datei selbst bleibt).</p>`,
        `<p><b>Bild:</b> Tippe auf <b>Cover wählen</b> und such ein Foto aus. Am besten quadratisch. Ohne eigenes Bild nimmt der Feed
          das Bild der ersten Folge.</p>`,
        `<table class="help-table"><thead><tr><th></th><th>Episoden</th><th>Serie</th></tr></thead><tbody>
          <tr><td>Reihenfolge</td><td>Neueste zuerst</td><td>Teil 1, 2, 3 … genau wie in deiner Liste</td></tr>
          <tr><td>Gut für</td><td>Sammlungen, Kanäle, „Einschlafen“</td><td>Hörbücher, Kurse, Geschichten mit Fortsetzung</td></tr>
        </tbody></table>`,
        tip('Abonniere den eigenen Feed wie oben beschrieben: Knopf <b>Apple Podcasts</b>, <b>Overcast</b> oder <b>Pocket Casts</b> im Feed antippen.'),
      ],
    },
    {
      id: 'subscriptions', short: 'Abos', title: 'Automatische Abos: Neue Videos von selbst laden', body: [
        `<p>Ein Abo schaut regelmäßig bei einem YouTube-Kanal oder einer Playlist nach. Gibt es etwas Neues, lädt ${n} es
          automatisch – du musst nichts tun.</p>`,
        steps(
          `Gehe auf ${go('#/feeds', '<b>Feeds</b>')} und öffne <b>Neues Abo</b>.`,
          `Füge den Link des Kanals ein (z. B. <code>youtube.com/@kanalname</code>) oder den Link einer Playlist.`,
          `Wähle das Format und wie oft geprüft werden soll (z. B. alle 6 Stunden).`,
          `Wähle, was beim Anlegen passieren soll: <b>Nur Neues</b> (ab jetzt), <b>Letzte</b> (die neuesten paar Videos) oder
            <b>Alle</b> (alles, was schon da ist – z. B. ein ganzes Hörbuch).`,
          `Wähle, ob die neuen Videos auch in einen <b>Feed</b> sollen. Dann tauchen sie direkt in deiner Podcast-App auf.`,
          `Tippe auf <b>Abo anlegen</b>.`,
        ),
        list(
          `<b>Jetzt prüfen</b> – sofort nachsehen, ohne zu warten.`,
          `<b>Pausieren</b> – das Abo ruht, bis du <b>Fortsetzen</b> tippst.`,
          `Papierkorb – Abo löschen. Bereits geladene Dateien bleiben erhalten.`,
        ),
        tip('Abos verbrauchen Speicher. Nimm bei großen Kanälen lieber „Nur Neues“ statt „Alle“.'),
      ],
    },
    {
      id: 'upload', short: 'Hochladen', title: 'Eigene Dateien hochladen', body: [
        `<p>Du kannst auch eigene Aufnahmen oder Audiodateien hinzufügen – z. B. um sie über deinen Podcast-Feed zu hören.</p>`,
        steps(
          `Gehe auf ${go('#/more', '<b>Mehr</b>')} → <b>Eigene Datei hinzufügen</b>.`,
          `Wähle die Datei aus (auf dem iPhone z. B. aus der Dateien-App oder den Sprachmemos).`,
          `Optional: Titel, Autor, Ordner und Beschreibung eintragen.`,
          `Tippe auf <b>Hochladen</b>. Die Datei wird umgewandelt und erscheint danach in der Bibliothek.`,
        ),
      ],
    },
    {
      id: 'account', short: 'Konto', title: 'Dein Konto: Speicher, Passwort, Abmelden', body: [
        `<p>Alles zu deinem Konto findest du ganz oben unter ${go('#/more', '<b>Mehr</b>')}.</p>`,
        list(
          `<b>Speicher</b> – wie viel Platz deine Dateien belegen. Hat dein Konto eine Grenze, zeigt ein Balken, wie voll es ist.
            Ist er voll, lösche alte Dateien oder frag den Admin.`,
          `<b>Passwort ändern</b> – aktuelles und neues Passwort (mindestens 8 Zeichen) eingeben. Andere Geräte werden dabei abgemeldet.`,
          `<b>Abmelden</b> – roter Knopf neben deinem Namen (am Computer auch unten links).`,
          `<b>Aufräumen</b> – unter <b>Speicher &amp; Aufräumen</b> löschst du auf einen Schlag alle Dateien, die älter als X Tage sind.`,
        ),
        tip('Passwort vergessen? Bitte den Admin, es dir neu zu setzen.'),
      ],
    },
    {
      id: 'problems', short: 'Probleme', title: 'Häufige Probleme und Lösungen', body: [
        `<dl class="help-faq">
          <dt>„YouTube verlangt eine Anmeldung bzw. Bot-Prüfung“</dt>
          <dd>YouTube blockiert den Server gerade. Warte eine Weile und tippe dann in den Aufträgen auf <b>Erneut</b>.
            Hilft das nicht, bitte den Admin, yt-dlp zu aktualisieren.</dd>
          <dt>„Dieses Video ist nicht verfügbar / privat / altersbeschränkt / nur für Mitglieder“</dt>
          <dd>Solche Videos können bewusst nicht geladen werden. Da lässt sich nichts einstellen.</dd>
          <dt>Der Fortschritt bleibt lange bei 85 % stehen</dt>
          <dd>Der Download ist fertig, jetzt wird die Datei aufbereitet. Bei Hörbüchern mit mehreren Stunden dauert das ein paar Minuten.
            Einfach warten – du kannst die Seite auch schließen.</dd>
          <dt>„Dein Speicherkontingent ist erschöpft“</dt>
          <dd>Dein Speicher ist voll. Lösche alte Dateien in der Bibliothek oder bitte den Admin um mehr Platz.</dd>
          <dt>„Das gewünschte Format ist nicht verfügbar“ (bei FLAC)</dt>
          <dd>Die Quelle bietet keinen verlustfreien Ton. Nimm M4A.</dd>
          <dt>Die Podcast-App kann den Feed nicht laden</dt>
          <dd>Prüfe, ob der Link vollständig kopiert wurde. Steht bei den Feeds ein gelber Hinweis zu „HTTP“, braucht der Server
            eine sichere Verbindung (HTTPS) – das richtet der Admin ein.</dd>
          <dt>Ein neuer Teil erscheint nicht in der Podcast-App</dt>
          <dd>Podcast-Apps schauen nur ab und zu nach. Zieh in der App die Liste nach unten, um sie zu aktualisieren.</dd>
          <dt>Die Datei spielt auf dem iPhone nicht ab</dt>
          <dd>OPUS und WebM können nicht alle iPhone-Apps abspielen. Nimm M4A bzw. MP4 oder öffne die Datei in VLC.</dd>
          <dt>Die Seite zeigt noch die alte Version</dt>
          <dd>Schließe die App bzw. den Tab ganz und öffne sie neu.</dd>
        </dl>`,
      ],
    },
    {
      id: 'admin', admin: true, short: 'Admin', title: 'Für Admins: Benutzer, Aussehen, Wartung', body: [
        list(
          `<b>Benutzer verwalten</b> (${go('#/users', 'Mehr → Benutzer verwalten')}): Konten anlegen, Rolle (Admin/Benutzer),
            Speicherkontingent und maximale parallele Aufträge festlegen, Passwort neu setzen, sperren oder löschen.
            Jeder Benutzer sieht nur seine eigenen Dateien, Aufträge und Feeds.`,
          `<b>Alles im Blick</b>: Als Admin siehst du unter Aufträge die Warteschlange aller Benutzer. In der Bibliothek kannst du oben
            zwischen „Meine Bibliothek“, „Alle Benutzer“ und einzelnen Benutzern wechseln.`,
          `<b>Erscheinungsbild</b> (Mehr → Erscheinungsbild): Name der App und Symbol ändern. Wirkt sofort für alle.`,
          `<b>yt-dlp aktualisieren</b> (Mehr → System): Wenn Downloads plötzlich bei allen scheitern, hilft meist ein Update.
            Normalerweise passiert das täglich automatisch.`,
          `<b>Aufräumen</b>: Temporäre Dateien leeren und – mit dem Schalter „Für alle Benutzer“ – alte Dateien aller Benutzer löschen.`,
        ),
        tip('Technische Einstellungen (Ports, Grenzen, HTTPS, Updates) stehen in der README des Projekts und in der Datei .env auf dem Server.'),
      ],
    },
    {
      id: 'legal', short: 'Rechtliches', title: 'Rechtliches und Datenschutz', body: [
        list(
          `Lade nur Inhalte herunter, für die du die nötigen Rechte oder eine Erlaubnis hast.`,
          `${n} umgeht keine Kopierschutz- oder Zugriffssperren und fragt nie nach deinem YouTube-Konto.`,
          `Deine Dateien liegen nur auf diesem Server. Andere Benutzer sehen sie nicht.`,
          `Feed-Links sind privat. Gib sie nur an Menschen weiter, denen du vertraust.`,
        ),
      ],
    },
  ];
}

export class HelpView {
  constructor(el, _params, query) {
    this.el = el;
    this.topic = query.get('topic');
  }

  render() {
    const name = appName();
    const all = topics(name).filter((t) => !t.admin || isAdmin());
    this.el.innerHTML = String(html`
      <header class="page-head"><div><h1>Hilfe</h1>
        <p>Alles Wichtige zu ${name} – einfach erklärt. Tippe auf ein Thema, um es aufzuklappen.</p></div></header>
      <div class="stack">
        <input class="input" id="help-q" type="search" placeholder="Hilfe durchsuchen, z. B. „iPhone“ oder „Hörbuch“" autocomplete="off" aria-label="Hilfe durchsuchen">
        <nav class="chips chips--wrap" aria-label="Themen">
          ${all.map((t) => html`<a class="chip" href="#/help?topic=${t.id}">${t.short}</a>`)}
        </nav>
        <div class="stack" id="help-topics">
          ${all.map((t) => html`<details class="card help-topic" id="help-${t.id}" data-topic="${t.id}">
            <summary class="row"><b class="grow">${t.title}</b><span class="help-chevron" aria-hidden="true">${icons.down}</span></summary>
            <div class="help-body">${raw(t.body.map((b) => String(b)).join(''))}</div>
          </details>`)}
        </div>
        <p class="muted small" id="help-empty" hidden>Nichts gefunden. Versuch es mit einem anderen Wort.</p>
      </div>
    `);
    const q = this.el.querySelector('#help-q');
    q.addEventListener('input', () => this.filter(q.value));
    if (this.topic) this.open(this.topic);
  }

  open(id) {
    const node = this.el.querySelector(`#help-${CSS.escape(id)}`);
    if (!node) return;
    node.open = true;
    requestAnimationFrame(() => node.scrollIntoView({ block: 'start', behavior: 'smooth' }));
  }

  filter(text) {
    const words = text.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    this.el.querySelectorAll('.help-topic').forEach((d) => {
      const hit = words.every((w) => d.textContent.toLowerCase().includes(w));
      d.hidden = !hit;
      if (hit) shown += 1;
      d.open = words.length > 0 && hit && shown <= 3; // bei Suche die besten Treffer aufklappen
    });
    this.el.querySelector('#help-empty').hidden = shown > 0;
  }
}
