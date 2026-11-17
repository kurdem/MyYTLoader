"""Eigene Feeds, Serien-Reihenfolge und automatische Abos."""
import uuid
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pytest
from fastapi.testclient import TestClient
from mutagen import File as MutagenFile

from conftest import ffmpeg, wait_for

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
PLAYLIST = "https://www.youtube.com/playlist?list=PLabcdefghij"
CHANNEL = "https://www.youtube.com/@kanalx"
H = {"X-Requested-With": "ytloader"}
NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}


def vid(n: int) -> str:
    return f"video{n:06d}"  # 11 Zeichen, gültige YouTube-ID-Form


def path_of(url: str) -> str:
    return url.split("testserver", 1)[1]


def wait_all(client, timeout=90):
    for j in client.get("/api/jobs", params={"status": "active"}).json():
        assert wait_for(client, j["id"], timeout)["status"] == "done"


def download(client, fmt="m4a") -> str:
    r = client.post("/api/jobs", json={"url": URL, "format": fmt})
    job = wait_for(client, r.json()["jobs"][0]["id"])
    assert job["status"] == "done", job
    return job["media_id"]


def rss(client, url) -> ET.Element:
    r = client.get(path_of(url))
    assert r.status_code == 200, r.text
    return ET.fromstring(r.content)


def login(app, username, password="passwort123") -> TestClient:
    c = TestClient(app)
    c.headers.update(H)
    assert c.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return c


# ------------------------------------------------------------------ eigene Feeds


def test_custom_feed_crud_and_order(authed):
    a, b, c = download(authed), download(authed, "mp3"), download(authed)
    r = authed.post("/api/feeds", json={"title": "Einschlafen", "description": "Ruhig", "media_ids": [a, b]})
    assert r.status_code == 200, r.text
    feed = r.json()
    assert [m["id"] for m in feed["items"]] == [a, b] and feed["type"] == "episodic"

    feed = authed.post(f"/api/feeds/{feed['id']}/items", json={"media_ids": [c, a]}).json()
    assert [m["id"] for m in feed["items"]] == [a, b, c]  # a bleibt, c ans Ende
    feed = authed.put(f"/api/feeds/{feed['id']}/items", json={"media_ids": [c, a]}).json()
    assert [m["id"] for m in feed["items"]] == [c, a]  # umsortiert, b entfernt
    feed = authed.delete(f"/api/feeds/{feed['id']}/items/{c}").json()
    assert [m["id"] for m in feed["items"]] == [a]

    feed = authed.patch(f"/api/feeds/{feed['id']}", json={"title": "Gute Nacht", "type": "serial"}).json()
    assert feed["title"] == "Gute Nacht" and feed["type"] == "serial"
    listed = authed.get("/api/feeds").json()
    assert listed[0]["item_count"] == 1 and listed[0]["url"].endswith(f"/f/{feed['id']}.xml")
    assert authed.get(f"/api/feeds/of-media/{a}").json() == [feed["id"]]
    assert any(f["id"] == feed["id"] for f in authed.get("/api/feed").json()["custom"])

    assert authed.delete(f"/api/feeds/{feed['id']}").status_code == 200
    assert authed.get(f"/api/feeds/{feed['id']}").status_code == 404
    assert authed.get(f"/api/media/{a}").status_code == 200  # Dateien bleiben


def test_rss_episodic_vs_serial(authed):
    ids = [download(authed) for _ in range(3)]
    feed = authed.post("/api/feeds", json={"title": "Hörbuch", "media_ids": ids}).json()
    root = rss(authed, feed["url"])
    assert root.find("./channel/itunes:type", NS).text == "episodic"
    assert root.find("./channel/title").text == "Hörbuch"
    assert root.find("./channel/itunes:block", NS).text == "Yes"
    assert root.find("./channel/item/itunes:episode", NS) is None

    authed.patch(f"/api/feeds/{feed['id']}", json={"type": "serial"})
    root = rss(authed, feed["url"])
    assert root.find("./channel/itunes:type", NS).text == "serial"
    items = root.findall("./channel/item")
    guids = [i.find("guid").text for i in items]
    assert guids == [f"ytloader-{m}" for m in ids]  # Reihenfolge = Feed-Reihenfolge
    assert [i.find("itunes:episode", NS).text for i in items] == ["1", "2", "3"]
    dates = [parsedate_to_datetime(i.find("pubDate").text) for i in items]
    assert dates == sorted(dates) and len(set(dates)) == 3  # auch nach Datum sortierende Apps passen


def test_playlist_track_numbers_and_feed_from_folder(authed):
    info = authed.post("/api/analyze", json={"url": PLAYLIST}).json()
    entries = [{**e, "index": i} for i, e in enumerate(info["entries"], start=1)]
    entries.reverse()  # Auswahl in anderer Reihenfolge abschicken -> Teil-Nr. bleibt Playlist-Position
    authed.post("/api/jobs", json={"url": PLAYLIST, "format": "m4a", "entries": entries, "collection": "Hobbit"})
    wait_all(authed)
    media = authed.get("/api/media", params={"collection": "Hobbit"}).json()
    assert sorted(m["track"] for m in media) == [1, 2]
    for m in media:
        f = MutagenFile(str(authed.app.state.services.settings.download_path / m["path"]))
        assert f.tags["trkn"][0][0] == m["track"]
    feed = authed.post("/api/feeds", json={"title": "Der Hobbit", "type": "serial", "collection": "Hobbit"}).json()
    assert [m["track"] for m in feed["items"]] == [1, 2]
    # Sortieren nach Titel und zurück nach Teil-Nr.
    feed = authed.post(f"/api/feeds/{feed['id']}/sort", json={"by": "title"}).json()
    feed = authed.post(f"/api/feeds/{feed['id']}/sort", json={"by": "track"}).json()
    assert [m["track"] for m in feed["items"]] == [1, 2]


def test_feed_cover(authed, tmp_path):
    a = download(authed)
    feed = authed.post("/api/feeds", json={"title": "Mit Cover", "media_ids": [a]}).json()
    img = tmp_path / "cover.png"
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=900x600:duration=1", "-frames:v", "1", str(img))
    with open(img, "rb") as fh:
        r = authed.post(f"/api/feeds/{feed['id']}/cover", files={"file": ("c.png", fh, "image/png")})
    assert r.status_code == 200 and r.json()["has_cover"] is True
    root = rss(authed, feed["url"])
    href = root.find("./channel/itunes:image", NS).get("href")
    assert f"/f/{feed['id']}/cover.jpg" in href
    cover = authed.get(path_of(href))
    assert cover.status_code == 200 and cover.content[:2] == b"\xff\xd8"
    with open(img, "rb") as fh:
        assert authed.post(f"/api/feeds/{feed['id']}/cover",
                           files={"file": ("x.png", b"kein bild", "image/png")}).status_code == 400
    assert authed.delete(f"/api/feeds/{feed['id']}/cover").json()["has_cover"] is False


def test_media_delete_removes_from_feed(authed):
    a, b = download(authed), download(authed)
    feed = authed.post("/api/feeds", json={"title": "X", "media_ids": [a, b]}).json()
    authed.delete(f"/api/media/{a}")
    assert [m["id"] for m in authed.get(f"/api/feeds/{feed['id']}").json()["items"]] == [b]


def test_feeds_are_private(authed):
    authed.post("/api/users", json={"username": "anna", "password": "passwort123"})
    anna = login(authed.app, "anna")
    admin_media = download(authed)
    feed = authed.post("/api/feeds", json={"title": "Admin", "media_ids": [admin_media]}).json()
    assert anna.get(f"/api/feeds/{feed['id']}").status_code == 404
    assert anna.patch(f"/api/feeds/{feed['id']}", json={"title": "x"}).status_code == 404
    assert anna.get("/api/feeds").json() == []
    own = anna.post("/api/feeds", json={"title": "Anna"}).json()
    assert anna.post(f"/api/feeds/{own['id']}/items", json={"media_ids": [admin_media]}).status_code == 404
    anna_token = path_of(anna.get("/api/feed").json()["media_base"]).split("/")[2]
    assert anna.get(f"/feed/{anna_token}/f/{feed['id']}.xml").status_code == 404
    # Benutzer löschen entfernt seine Feeds
    anna_id = next(u["id"] for u in authed.get("/api/users").json()["users"] if u["username"] == "anna")
    authed.delete(f"/api/users/{anna_id}")
    svc = authed.app.state.services
    assert svc.db.one("SELECT COUNT(*) AS n FROM feeds WHERE user_id=?", (anna_id,))["n"] == 0


@pytest.mark.parametrize("body", [{"title": ""}, {"title": "x" * 121}, {"title": "ok", "type": "random"}])
def test_invalid_feed(authed, body):
    assert authed.post("/api/feeds", json=body).status_code in (400, 422)


# ------------------------------------------------------------------ Abos


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/@kanal", ("https://www.youtube.com/@kanal/videos", "channel")),
    ("https://www.youtube.com/@kanal/featured", ("https://www.youtube.com/@kanal/videos", "channel")),
    ("https://www.youtube.com/@kanal/streams", ("https://www.youtube.com/@kanal/streams", "channel")),
    ("https://www.youtube.com/channel/UC123/", ("https://www.youtube.com/channel/UC123/videos", "channel")),
    ("https://www.youtube.com/playlist?list=PLabcdefghij", ("https://www.youtube.com/playlist?list=PLabcdefghij", "playlist")),
    ("https://www.youtube.com/watch?v=x&list=PLabcdefghij", ("https://www.youtube.com/playlist?list=PLabcdefghij", "playlist")),
])
def test_normalize_source(url, expected):
    from app.subscriptions import normalize_source

    assert normalize_source(url) == expected


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/"])
def test_normalize_source_rejects(url):
    from app.security import UserError
    from app.subscriptions import normalize_source

    with pytest.raises(UserError):
        normalize_source(url)


def test_channel_subscription_only_new(authed, monkeypatch):
    monkeypatch.setenv("FAKE_ENTRIES", ",".join([vid(3), vid(2), vid(1)]))  # Kanal: neueste zuerst
    r = authed.post("/api/subscriptions", json={"url": CHANNEL, "format": "m4a", "initial": "none",
                                                "feed_mode": "new"})
    assert r.status_code == 200, r.text
    sub = r.json()
    assert sub["kind"] == "channel" and sub["title"] == "Kanal X" and sub["collection"] == "Kanal X"
    assert sub["last_new_count"] == 0 and authed.get("/api/jobs").json() == []

    monkeypatch.setenv("FAKE_ENTRIES", ",".join([vid(5), vid(4), vid(3), vid(2), vid(1)]))
    sub = authed.post(f"/api/subscriptions/{sub['id']}/check").json()
    assert sub["last_new_count"] == 2 and sub["last_error"] is None
    jobs = authed.get("/api/jobs").json()
    assert sorted(j["url"][-11:] for j in jobs) == [vid(4), vid(5)]
    wait_all(authed)
    feed = authed.get(f"/api/feeds/{sub['feed_id']}").json()
    assert feed["title"] == "Kanal X" and feed["type"] == "episodic" and feed["item_count"] == 2
    # erneute Prüfung ohne Neues
    assert authed.post(f"/api/subscriptions/{sub['id']}/check").json()["last_new_count"] == 0


def test_channel_initial_latest(authed, monkeypatch):
    monkeypatch.setenv("FAKE_ENTRIES", ",".join([vid(3), vid(2), vid(1)]))
    sub = authed.post("/api/subscriptions", json={"url": CHANNEL, "format": "mp3", "initial": "latest",
                                                  "initial_count": 2}).json()
    assert sub["last_new_count"] == 2
    assert sorted(j["url"][-11:] for j in authed.get("/api/jobs").json()) == [vid(2), vid(3)]


def test_playlist_subscription_serial_feed(authed, monkeypatch):
    monkeypatch.setenv("FAKE_ENTRIES", ",".join([vid(1), vid(2)]))
    sub = authed.post("/api/subscriptions", json={"url": PLAYLIST, "format": "m4a", "initial": "all",
                                                  "feed_mode": "new", "collection": "Mein Hörbuch"}).json()
    assert sub["kind"] == "playlist" and sub["last_new_count"] == 2
    wait_all(authed)
    monkeypatch.setenv("FAKE_ENTRIES", ",".join([vid(1), vid(2), vid(3)]))  # neuer Teil am Ende
    authed.post(f"/api/subscriptions/{sub['id']}/check")
    wait_all(authed)
    feed = authed.get(f"/api/feeds/{sub['feed_id']}").json()
    assert feed["type"] == "serial"  # Playlist-Abo -> Serie als Vorgabe
    assert [m["track"] for m in feed["items"]] == [1, 2, 3]
    assert all(m["path"].startswith("admin/Mein Hörbuch/") for m in feed["items"])
    root = rss(authed, feed["url"])
    assert [i.find("itunes:episode", NS).text for i in root.findall("./channel/item")] == ["1", "2", "3"]


def test_serial_insert_by_track(authed):
    """Fertig in falscher Reihenfolge (parallele Downloads) -> trotzdem nach Teil-Nr. im Feed."""
    svc = authed.app.state.services
    admin = svc.users.by_username("admin")
    ids = [download(authed) for _ in range(3)]
    for mid, track in zip(ids, (3, 1, 2)):
        svc.db.update("media", mid, {"track": track})
    feed = svc.feeds.create(admin, "Serie", type_="serial")
    for mid in ids:
        svc.feeds.insert(feed["id"], admin, svc.library.get(mid))
    assert [m["track"] for m in svc.feeds.items(feed["id"])] == [1, 2, 3]


def test_subscription_validation(authed, monkeypatch):
    monkeypatch.setenv("FAKE_ENTRIES", vid(1))
    assert authed.post("/api/subscriptions", json={"url": URL}).status_code == 400  # Einzelvideo
    assert authed.post("/api/subscriptions", json={"url": "https://example.com/@x"}).status_code == 400
    assert authed.post("/api/subscriptions", json={"url": CHANNEL, "interval_hours": 0}).status_code == 422
    assert authed.post("/api/subscriptions", json={"url": CHANNEL, "feed_mode": "existing"}).status_code == 400
    assert authed.post("/api/subscriptions", json={"url": CHANNEL}).status_code == 200
    assert authed.post("/api/subscriptions", json={"url": CHANNEL + "/videos"}).status_code == 409


def test_subscription_respects_quota(authed, monkeypatch):
    svc = authed.app.state.services
    authed.post("/api/users", json={"username": "anna", "password": "passwort123", "quota_mb": 1})
    anna = login(authed.app, "anna")
    anna_id = svc.users.by_username("anna")["id"]
    monkeypatch.setenv("FAKE_ENTRIES", vid(1))
    sub = anna.post("/api/subscriptions", json={"url": CHANNEL, "initial": "none"}).json()
    placeholder = uuid.uuid4().hex
    svc.db.insert("media", {"id": placeholder, "user_id": anna_id, "title": "voll", "kind": "audio",
                            "format": "m4a", "path": f"p-{placeholder}.m4a", "size": 1024 * 1024,
                            "created_at": "2024-01-01T00:00:00+00:00"})
    monkeypatch.setenv("FAKE_ENTRIES", ",".join([vid(2), vid(1)]))
    sub = anna.post(f"/api/subscriptions/{sub['id']}/check").json()
    assert sub["last_new_count"] == 0 and "Speicherkontingent" in sub["last_error"]
    svc.db.execute("DELETE FROM media WHERE id=?", (placeholder,))  # Platz frei -> beim nächsten Mal
    sub = anna.post(f"/api/subscriptions/{sub['id']}/check").json()
    assert sub["last_new_count"] == 1 and sub["last_error"] is None


def test_subscription_update_privacy_and_due(authed, monkeypatch):
    svc = authed.app.state.services
    monkeypatch.setenv("FAKE_ENTRIES", vid(1))
    sub = authed.post("/api/subscriptions", json={"url": CHANNEL, "interval_hours": 2}).json()
    assert sub["id"] not in svc.subscriptions.due()  # gerade geprüft
    svc.db.update("subscriptions", sub["id"], {"last_checked_at": "2020-01-01T00:00:00+00:00"})
    assert sub["id"] in svc.subscriptions.due()
    sub = authed.patch(f"/api/subscriptions/{sub['id']}", json={"enabled": False, "interval_hours": 24}).json()
    assert sub["enabled"] is False and sub["interval_hours"] == 24 and sub["next_check_at"] is None
    assert sub["id"] not in svc.subscriptions.due()

    authed.post("/api/users", json={"username": "anna", "password": "passwort123"})
    anna = login(authed.app, "anna")
    assert anna.get("/api/subscriptions").json() == []
    assert anna.post(f"/api/subscriptions/{sub['id']}/check").status_code == 404
    assert anna.delete(f"/api/subscriptions/{sub['id']}").status_code == 404
    assert authed.delete(f"/api/subscriptions/{sub['id']}").status_code == 200
