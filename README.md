# MyYTLoader

Selbst gehosteter Media-Downloader und privater Podcast-Server fürs Homelab.
YouTube-Link einfügen, Format wählen, Download starten und die fertige Datei direkt auf iPhone oder iPad laden,
im Browser abspielen oder als privaten Podcast-Feed abonnieren.

> **Rechtlicher Hinweis:** Lade nur Inhalte herunter, für die du die erforderlichen Rechte bzw. eine Erlaubnis hast.
> MyYTLoader umgeht **keine** DRM- oder Zugriffsschutzmaßnahmen, unterstützt keine Premium-, Mitglieder- oder
> Altersprüfungs-Inhalte und speichert oder verlangt **keine** YouTube-Zugangsdaten.

## Funktionen

- **Download & Konvertierung** mit yt-dlp und FFmpeg
  - Audio: **M4A/AAC** (Standard, iOS-optimiert), **MP3**, **OPUS**, **FLAC** (nur bei echter verlustfreier Quelle, kein Hochkonvertieren)
  - Video: **MP4 (H.264/AAC)**, bei Bedarf iOS-kompatibel neu kodiert, `faststart`; **WebM**
  - einzelne Videos und **Playlists** (Auswahl einzelner Einträge, Ablage als Ordner)
- **Audio-Aufbereitung:** Lautheitsnormalisierung auf -16 LUFS (EBU-R128-Messung, Pegelanpassung + Limiter bei -1,5 dBFS), optionales Entfernen
  längerer Stille am Anfang/Ende, Cover (quadratisch) und ID3-/MP4-/Vorbis-Tags, saubere Dateinamen `Kanal - Titel.m4a`,
  Original optional behalten
- **Auftragsverwaltung:** Warteschlange, Live-Fortschritt per Server-Sent Events, Abbrechen, Wiederholen, Verlauf.
  Downloads laufen serverseitig weiter, auch wenn der Browser geschlossen wird, und werden nach einem Neustart fortgesetzt.
- **Bibliothek:** Kartenansicht mit Cover, Suche, Sortierung, Filter Audio/Video und Ordner, Wiedergabe im Browser,
  Download, Metadaten bearbeiten (Tags und Dateiname werden angepasst), Löschen, Aufräumen nach Alter
- **iOS-Integration:** Download direkt in die Dateien-App, Teilen-Menü (Web Share API), „In VLC öffnen“,
  installierbar als PWA (Home-Bildschirm), Range-Requests für die Wiedergabe
- **Privater Podcast-RSS-Feed:** gesamte Bibliothek, Videos oder einzelne Ordner abonnierbar, geschützt per geheimem Token,
  `itunes:block`, damit der Feed nie in einem öffentlichen Verzeichnis landet
- **Eigene Dateien hochladen**, z. B. eigene Aufnahmen für den privaten Podcast
- **Mehrere Benutzer:** getrennte Bibliotheken, Aufträge und Podcast-Feeds pro Benutzer, Rollen Admin/Benutzer,
  Speicherkontingent und maximale parallele Aufträge pro Benutzer, Benutzerverwaltung in der Oberfläche
- **Sicherheit:** Login, CSRF-Schutz, Domain-Allowlist, SSRF-Schutz (keine internen Adressen), Pfadvalidierung,
  Rate Limiting, Größenlimit, keine Shell-Aufrufe mit Benutzereingaben, strikte Content-Security-Policy

## Schnellstart (Docker Compose)

Voraussetzungen: Docker mit Compose-Plugin, Internetzugang des Hosts.

```bash
git clone https://github.com/kurdem/MyYTLoader.git
```

```bash
cd MyYTLoader
```

```bash
cp .env.example .env
```

In `.env` mindestens `ADMIN_PASSWORD` setzen (sonst wird beim ersten Start eines erzeugt, siehe unten) und `PUID`/`PGID`
auf den Benutzer setzen, dem die Dateien auf dem Host gehören sollen (`id -u`, `id -g`).

```bash
docker compose up -d --build
```

Danach ist die Oberfläche unter `http://<host>:8080` erreichbar.

Ohne gesetztes `ADMIN_PASSWORD` steht das erzeugte Passwort im Log und in `data/config/admin_password.txt`:

```bash
docker compose logs myytloader | grep Passwort
```

### Datenablage auf dem Host

| Host (Standard)        | Container    | Inhalt                                                 |
|------------------------|--------------|--------------------------------------------------------|
| `./data/downloads`     | `/downloads` | fertige Dateien: `<benutzer>/<ordner>/Kanal - Titel.m4a`, `.covers/` |
| `./data/database`      | `/database`  | SQLite-Datenbank                                       |
| `./data/config`        | `/config`    | Secret, generiertes Passwort, aktualisiertes yt-dlp    |
| `./data/temp`          | `/temp`      | Zwischendateien (werden automatisch bereinigt)         |

Den Download-Ordner kannst du per `HOST_DOWNLOAD_PATH` z. B. direkt auf ein NAS-Share legen.

## Konfiguration (`.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `APP_PORT` | `8080` | Port im Container und auf dem Host |
| `BASE_URL` | leer | Öffentliche Adresse, z. B. `https://ytl.example.de`. Wichtig für Feed-Links. |
| `PUID` / `PGID` | `1000` | Benutzer/Gruppe, unter dem die App läuft |
| `HOST_DOWNLOAD_PATH` | `./data/downloads` | Ablage der fertigen Dateien auf dem Host |
| `AUTH_ENABLED` | `true` | Login aktivieren (dringend empfohlen) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / leer | erster Admin, wird **nur beim ersten Start** angelegt |
| `ADMIN_PASSWORD_RESET` | `false` | `true` setzt beim Start das Passwort von `ADMIN_USERNAME` auf `ADMIN_PASSWORD` (Notfall) |
| `DEFAULT_USER_QUOTA_MB` | `0` | Vorgabe-Kontingent für neue Benutzer (0 = unbegrenzt) |
| `DEFAULT_USER_MAX_JOBS` | `0` | Vorgabe für parallele Aufträge neuer Benutzer (0 = unbegrenzt) |
| `SECRET_KEY` | leer | Signaturschlüssel für Sitzungen (leer = automatisch erzeugt) |
| `SESSION_MAX_AGE_DAYS` | `30` | Gültigkeit des Logins |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | parallele Aufträge |
| `MAX_FILE_SIZE_MB` | `2048` | maximale Größe pro Download/Upload |
| `PLAYLIST_MAX_ITEMS` | `200` | maximale Einträge pro Playlist |
| `ALLOWED_DOMAINS` | `youtube.com,youtu.be,youtube-nocookie.com` | erlaubte Quellen (inkl. Subdomains) |
| `RATE_LIMIT_PER_MINUTE` | `30` | Analyse/Download/Upload pro Minute und Client |
| `DEFAULT_AUDIO_FORMAT` | `m4a` | `m4a`, `mp3`, `opus`, `flac` |
| `DEFAULT_AUDIO_BITRATE` | `192k` | 96k–320k |
| `DEFAULT_NORMALIZE` | `true` | Lautheitsnormalisierung als Vorgabe |
| `DEFAULT_TRIM_SILENCE` | `false` | Stille entfernen als Vorgabe |
| `KEEP_ORIGINAL` | `false` | Originaldatei zusätzlich behalten (`/downloads/.originals`) |
| `TEMP_MAX_AGE_HOURS` | `12` | Temp-Dateien danach löschen |
| `RETENTION_DAYS` | `0` | Medien nach X Tagen automatisch löschen (0 = nie) |
| `YTDLP_AUTO_UPDATE` | `true` | yt-dlp beim Start und täglich aktualisieren |
| `YTDLP_CHANNEL` | `stable` | `stable` oder `nightly` |
| `YTDLP_SLEEP_INTERVAL` | `3` | Pause in Sekunden zwischen Playlist-Einträgen |
| `FORWARDED_ALLOW_IPS` | `*` | vertrauenswürdige Proxies für `X-Forwarded-*` |
| `LOG_LEVEL` | `INFO` | Log-Level |

## Hinter dem Nginx Proxy Manager

1. Proxy Host anlegen: Domain z. B. `ytl.example.de`, Ziel `http://<docker-host>:8080`
   (oder `myytloader:8080`, wenn der Container im NPM-Netzwerk hängt, siehe Kommentar in `docker-compose.yml`).
2. SSL-Zertifikat (Let’s Encrypt) aktivieren und „Force SSL“ einschalten. Viele Podcast-Apps verlangen HTTPS.
3. Unter **Advanced** eintragen (große Uploads, lange Live-Verbindungen):

   ```nginx
   client_max_body_size 2048m;
   proxy_read_timeout 3600s;
   proxy_buffering off;
   ```

4. In `.env` `BASE_URL=https://ytl.example.de` setzen und den Container neu starten.

WebSockets müssen nicht aktiviert werden; der Live-Fortschritt nutzt Server-Sent Events.

> Die App ins Internet freizugeben ist möglich (Login, Rate Limiting, CSRF-Schutz), empfohlen ist aber der Zugriff
> über VPN (z. B. WireGuard/Tailscale) oder eine Zugriffsliste im NPM.

## Nutzung auf iPhone und iPad

- **Als App installieren:** In Safari die Seite öffnen, dann **Teilen → Zum Home-Bildschirm**.
- **Datei laden:** In der Detailansicht auf **Herunterladen** tippen. Safari legt die Datei in der **Dateien**-App unter
  „Downloads“ ab. Von dort per „Teilen“ an VLC, Musik- oder Podcast-Player übergeben.
- **Teilen:** Der Button „Teilen …“ lädt die Datei und öffnet beim zweiten Tippen das iOS-Teilen-Menü
  (bis 300 MB; iOS verlangt, dass das Menü direkt durch einen Tipp geöffnet wird).
- **VLC:** „In VLC öffnen“ übergibt einen tokenbasierten Direktlink an VLC für iOS.
- **Formate:** M4A und MP4 laufen überall. OPUS und WebM spielen nicht alle iOS-Apps ab, VLC schon.
- **Kurzbefehl:** Links lassen sich direkt übergeben: `https://ytl.example.de/?url=<YouTube-Link>`
  (z. B. per iOS-Kurzbefehl „URL öffnen“ aus dem Teilen-Menü von YouTube).

Heruntergeladene Dateien erscheinen **nicht** automatisch als abonnierter Podcast. Dafür ist ausschließlich der
private Feed da, den du bewusst abonnierst.

## Mehrere Benutzer

Der erste Admin entsteht beim ersten Start aus `ADMIN_USERNAME`/`ADMIN_PASSWORD`. Weitere Konten legt ein Admin unter
**Mehr → Benutzer verwalten** an.

| | Benutzer | Admin |
|---|---|---|
| Eigene Downloads, Bibliothek, Podcast-Feed | ✓ | ✓ |
| Passwort ändern, eigene Dateien aufräumen | ✓ | ✓ |
| Aufträge und Bibliotheken aller Benutzer sehen und verwalten | – | ✓ |
| Benutzer anlegen, Rollen, Kontingente, Sperren, Löschen | – | ✓ |
| yt-dlp aktualisieren, temporäre Dateien leeren | – | ✓ |

- **Getrennte Daten:** Jeder Benutzer sieht nur seine eigenen Aufträge und Medien. Fremde Medien sind auch per
  direkter URL oder Feed-Token nicht abrufbar. Dateien liegen auf dem Host unter `downloads/<benutzer>/`.
- **Speicherkontingent (MB):** Bei vollem Kontingent werden neue Aufträge abgelehnt. Der Download ist auf den freien
  Platz begrenzt, und nach der Konvertierung wird die endgültige Größe noch einmal geprüft. Behaltene Originale zählen mit.
- **Max. parallele Aufträge:** Zusätzlich zu `MAX_CONCURRENT_DOWNLOADS`. Die Warteschlange nimmt immer den ältesten
  Auftrag, dessen Besitzer sein Limit noch nicht erreicht hat. So blockiert eine große Playlist nicht alle anderen.
- **Sperren** beendet alle Sitzungen und bricht laufende Aufträge ab. **Löschen** entfernt das Konto mit allen Dateien
  (Bestätigung durch Eingabe des Benutzernamens).
- **Passwortwechsel** meldet alle anderen Geräte ab.

**Umstieg von Version 1.0:** Beim ersten Start wird die Datenbank automatisch migriert. Vorhandene Medien, Aufträge
und der bisherige Feed-Token gehen an den Admin, bestehende Podcast-Abos funktionieren weiter. Alte Dateien bleiben
an ihrem Platz und wandern erst beim Bearbeiten der Metadaten in den Admin-Ordner.

## Privater Podcast-Feed

Jeder Benutzer hat einen eigenen Feed mit eigenem Token. Unter **Mehr → Privater Podcast-Feed** stehen die Adressen:

- **Alle Audiodateien** der Bibliothek
- **Alle Videos**
- **je Ordner** (z. B. eine Playlist oder „Mein Podcast“ für eigene Uploads)

Abonnieren:
- **Apple Podcasts:** Mediathek → „…“ → **Sendung per URL folgen** → Feed-URL einfügen (HTTPS erforderlich)
- **Overcast / Pocket Casts:** Buttons auf der Seite oder URL manuell hinzufügen

Der Feed ist durch einen geheimen Token in der URL geschützt, weil Podcast-Apps keine Anmeldung per Cookie können.
Behandle die URL wie ein Passwort. Mit **Feed-Link neu erzeugen** werden alle alten Feed- und Direktlinks ungültig.

## yt-dlp aktuell halten

YouTube ändert seine Seiten regelmäßig, und ältere yt-dlp-Versionen scheitern dann. Daher:

- yt-dlp läuft in einem **eigenen venv** und wird als **separater Prozess** gestartet. Ein Update greift sofort,
  ohne App-Neustart oder Image-Rebuild.
- `YTDLP_AUTO_UPDATE=true` aktualisiert beim Start und danach täglich nach `/config/yt-dlp`.
  Es wird immer die neueste funktionierende Version (Image oder `/config`) verwendet.
- Manuelles Update: **Mehr → System → yt-dlp jetzt aktualisieren**
- YouTube benötigt eine JavaScript-Runtime: **Deno** wird über `yt-dlp[default,deno]` mitinstalliert und mit aktualisiert.
- Wenn YouTube vom Server eine „Bot-Prüfung“ verlangt, erscheint eine verständliche Meldung. Anmeldedaten oder
  Cookies werden bewusst nicht unterstützt.

## Update der Anwendung

```bash
cd MyYTLoader
```

```bash
git pull
```

```bash
docker compose build --pull
```

```bash
docker compose up -d
```

Datenbankmigrationen laufen beim Start automatisch. Daten in `./data` bleiben erhalten.

## Backup

Wichtig sind `data/database` (Bibliothek/Aufträge), `data/config` (Secret, Feed-Token liegt in der DB) und
`data/downloads`. Für ein konsistentes Datenbank-Backup den Container kurz stoppen:

```bash
docker compose stop && tar czf myytloader-backup.tgz data/database data/config && docker compose start
```

## Fehlerbehebung

| Symptom | Lösung |
|---|---|
| „Bot-Prüfung“ / HTTP 403 / plötzlich scheitern alle Downloads | yt-dlp aktualisieren (Mehr → System); später erneut versuchen |
| „Host konnte nicht aufgelöst werden“ | DNS des Docker-Hosts prüfen |
| Fortschritt aktualisiert sich nicht hinter dem Proxy | `proxy_buffering off;` im NPM setzen |
| Upload bricht ab | `client_max_body_size` im NPM erhöhen, `MAX_FILE_SIZE_MB` prüfen |
| Apple Podcasts kann den Feed nicht laden | HTTPS verwenden und `BASE_URL` korrekt setzen |
| Rechteprobleme in `./data` | `PUID`/`PGID` passend setzen, Container neu starten |
| Benutzer hat Passwort vergessen | Admin: Mehr → Benutzer verwalten → Bearbeiten → neues Passwort |
| Admin-Passwort vergessen | In `.env` `ADMIN_PASSWORD=<neu>` und `ADMIN_PASSWORD_RESET=true`, `docker compose up -d`, danach `ADMIN_PASSWORD_RESET=false` |
| „Speicherkontingent erschöpft“ | eigene Dateien löschen oder Kontingent durch Admin erhöhen |

Logs:

```bash
docker compose logs -f myytloader
```

## Architektur

```
frontend/               Vanilla-JS-PWA (ES-Module, kein Build-Schritt), Dark Mode, Mobile First
backend/app/
  main.py               App-Factory, Routen, Sicherheitsheader, statisches Frontend
  config.py             Konfiguration aus Umgebungsvariablen
  db.py                 SQLite (WAL) + Migrationen (PRAGMA user_version)
  security.py           URL-/SSRF-Prüfung, Dateinamen, Pfade, Rate Limiter
  auth.py               Session-Cookie (signiert, mit Session-Version), Rollen, CSRF-Header
  users.py              Benutzer, scrypt-Hashes, Kontingente, Feed-Tokens, Migration aus v1
  ytdlp.py              yt-dlp als Subprozess: Analyse, Download, Fortschritt, Fehlertexte, Updates
  media.py              FFmpeg: Konvertierung, Loudnorm, Stille, Cover; Tagging mit mutagen
  jobs.py               Warteschlange mit fairem Scheduler (Limit pro Benutzer), Event-Bus für SSE (pro Benutzer gefiltert)
  library.py            Bibliothek: Abfragen, Bearbeiten, Löschen
  feed.py               RSS-Feed mit iTunes-Namespace, Feed-Token
  maintenance.py        Temp-Bereinigung, Aufbewahrungsfrist, yt-dlp-Auto-Update
  routes/               HTTP-Endpunkte (auth, users, jobs, library, feed, system)
docker/entrypoint.sh    Volumes an PUID/PGID anpassen, Rechte abgeben, uvicorn starten
```

Ablauf eines Auftrags: `queued → running (yt-dlp) → processing (FFmpeg, Tags, Cover) → done | failed | cancelled`.
Die App läuft bewusst mit **einem** uvicorn-Worker, weil Warteschlange und Live-Events im Prozess gehalten werden.

**Erweiterbarkeit:** Die Bausteine sind so getrennt, dass sich weitere Funktionen ohne Umbau ergänzen lassen:
NAS-Sync bzw. Import
lokaler Dateien (Upload-Pfad im `JobManager` mit `kind=upload` wiederverwenden), Wiedergabelisten (neue Tabelle),
automatisierte Verarbeitung neuer Dateien (Watch-Task analog `maintenance.py`) und Home Assistant (REST-API bzw. Webhook
aus dem Event-Bus).

## Entwicklung & Tests

Voraussetzungen: Python 3.12+, FFmpeg.

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt "yt-dlp[default,deno]"
```

```bash
python -m pytest -q
```

Die Tests nutzen echtes FFmpeg (Konvertierung, Loudness, Stille, Tags für alle Formate) und ein Fake-yt-dlp mit
demselben CLI-Vertrag für die API-End-to-End-Tests (Auth, CSRF, SSRF, Playlist, Abbruch, Größenlimit, Feed, Upload,
Mehrbenutzer: Trennung, Rollen, Kontingente, Job-Limits, Migration aus v1).

Lokal starten:

```bash
DOWNLOAD_PATH=./data/downloads TEMP_PATH=./data/temp DATABASE_PATH=./data/database CONFIG_PATH=./data/config \
ADMIN_PASSWORD=dev YTDLP_AUTO_UPDATE=false uvicorn app.main:create_app --factory --reload --port 8080
```

Icons neu erzeugen (nur bei Designänderungen): `pip install pillow && python tools/make_icons.py`
