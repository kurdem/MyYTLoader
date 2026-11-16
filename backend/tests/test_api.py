"""End-to-End über die HTTP-API (mit Fake-yt-dlp, echtem FFmpeg)."""
import xml.etree.ElementTree as ET
from pathlib import Path

from conftest import wait_for

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_health_is_public(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "MyYTLoader" in r.text
    assert "Content-Security-Policy" in r.headers


def test_auth_required(client):
    assert client.get("/api/jobs").status_code == 401
    assert client.post("/api/analyze", json={"url": URL}).status_code == 401
    assert client.get("/api/auth/me").json()["authenticated"] is False


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "falsch"})
    assert r.status_code == 401
    assert "falsch" in r.json()["detail"]


def test_csrf_header_required(authed):
    r = authed.post("/api/analyze", json={"url": URL}, headers={"X-Requested-With": ""})
    assert r.status_code == 403


def test_login_rate_limited(client):
    codes = [client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code for _ in range(12)]
    assert 429 in codes


def test_analyze_video(authed):
    r = authed.post("/api/analyze", json={"url": URL})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["type"] == "video"
    assert data["channel"] == "Testkanal"
    assert data["audio"]["lossless"] is False
    assert data["video_heights"] == [1080]


def test_analyze_rejects_foreign_domain(authed):
    r = authed.post("/api/analyze", json={"url": "https://example.com/video"})
    assert r.status_code == 400
    assert "nicht unterstützt" in r.json()["detail"]


def test_analyze_friendly_errors(authed):
    r = authed.post("/api/analyze", json={"url": "https://www.youtube.com/watch?v=fail-bot"})
    assert r.status_code == 422
    assert "Bot-Prüfung" in r.json()["detail"]
    r = authed.post("/api/analyze", json={"url": "https://www.youtube.com/watch?v=fail-drm"})
    assert "DRM" in r.json()["detail"]


def test_full_audio_flow(authed, env):
    r = authed.post("/api/jobs", json={"url": URL, "format": "m4a",
                                       "options": {"bitrate": "128k", "normalize": True, "trim_silence": True}})
    assert r.status_code == 200, r.text
    job = wait_for(authed, r.json()["jobs"][0]["id"])
    assert job["status"] == "done", job
    media = authed.get(f"/api/media/{job['media_id']}").json()
    assert media["filename"] == "Testkanal - Test - Titel - mit Sonderzeichen.m4a"
    assert media["has_cover"] and media["kind"] == "audio"
    assert (env / "downloads" / media["path"]).is_file()

    # Download als Anhang (Safari -> Dateien-App)
    r = authed.get(f"/api/media/{media['id']}/download")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-type"] == "audio/mp4"
    # Range-Requests (iOS-Player)
    r = authed.get(f"/api/media/{media['id']}/file", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100

    # Bibliothek: Suche/Filter
    assert len(authed.get("/api/media", params={"q": "Sonderzeichen"}).json()) == 1
    assert authed.get("/api/media", params={"kind": "video"}).json() == []

    # Metadaten bearbeiten -> Datei wird umbenannt
    r = authed.patch(f"/api/media/{media['id']}", json={"title": "Neuer Titel", "collection": "Podcasts"})
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["path"] == "Podcasts/Testkanal - Neuer Titel.m4a"
    assert (env / "downloads" / edited["path"]).is_file()
    assert not (env / "downloads" / media["path"]).exists()

    # Feed
    feed = authed.get("/api/feed").json()
    feed_url = feed["feeds"][0]["url"]
    path = feed_url.split("testserver", 1)[1]
    r = authed.get(path)
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    items = root.findall("./channel/item")
    assert len(items) == 1
    enclosure = items[0].find("enclosure")
    assert enclosure.get("type") == "audio/mp4"
    assert int(enclosure.get("length")) == edited["size"]
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    assert root.find("./channel/itunes:block", ns).text == "Yes"
    enc_path = enclosure.get("url").split("testserver", 1)[1]
    r = authed.get(enc_path, headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    # Feed ist ohne Cookie über Token erreichbar, falsches Token -> 404
    authed.cookies.clear()
    assert authed.get(path).status_code == 200
    assert authed.get(path.replace(feed["media_base"].split("/feed/")[1].split("/")[0], "x" * 32)).status_code == 404

    # Löschen
    authed.post("/api/auth/login", json={"username": "admin", "password": "geheim"})
    assert authed.delete(f"/api/media/{media['id']}").status_code == 200
    assert not (env / "downloads" / edited["path"]).exists()
    assert not (env / "downloads" / "Podcasts").exists()


def test_video_flow_mp4(authed):
    r = authed.post("/api/jobs", json={"url": URL, "format": "mp4", "options": {"max_height": 720}})
    job = wait_for(authed, r.json()["jobs"][0]["id"])
    assert job["status"] == "done", job
    media = authed.get(f"/api/media/{job['media_id']}").json()
    assert media["kind"] == "video" and media["format"] == "mp4"


def test_flac_requires_lossless_source(authed):
    r = authed.post("/api/jobs", json={"url": URL, "format": "flac"})
    job = wait_for(authed, r.json()["jobs"][0]["id"])
    assert job["status"] == "failed"
    assert "nicht verfügbar" in job["error"]


def test_playlist_creates_jobs_in_collection(authed):
    info = authed.post("/api/analyze", json={"url": "https://www.youtube.com/playlist?list=PL1"}).json()
    assert info["type"] == "playlist" and len(info["entries"]) == 2
    r = authed.post("/api/jobs", json={"url": info["url"], "format": "mp3", "entries": info["entries"],
                                       "collection": "../Meine Liste"})
    jobs = r.json()["jobs"]
    assert len(jobs) == 2
    assert all(j["collection"] == "Meine Liste" for j in jobs)
    assert jobs[0]["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    for j in jobs:
        assert wait_for(authed, j["id"])["status"] == "done"
    cols = authed.get("/api/media/collections").json()
    assert cols[0]["name"] == "Meine Liste" and cols[0]["count"] == 2


def test_playlist_entry_ids_validated(authed):
    r = authed.post("/api/jobs", json={"url": URL, "format": "mp3", "entries": [{"id": "--exec=rm"}]})
    assert r.status_code == 400


def test_invalid_options_rejected(authed):
    assert authed.post("/api/jobs", json={"url": URL, "format": "exe"}).status_code == 400
    assert authed.post("/api/jobs", json={"url": URL, "format": "mp3", "options": {"bitrate": "9999k"}}).status_code == 400


def test_max_file_size(authed, monkeypatch):
    from app.config import get_settings

    svc = authed.app.state.services
    object.__setattr__(svc.settings, "max_file_size_mb", 0)  # -> 0 Bytes erlaubt
    try:
        r = authed.post("/api/jobs", json={"url": URL, "format": "m4a"})
        job = wait_for(authed, r.json()["jobs"][0]["id"])
    finally:
        object.__setattr__(svc.settings, "max_file_size_mb", get_settings().max_file_size_mb)
    assert job["status"] == "failed"
    assert "Dateigröße" in job["error"]


def test_cancel_and_retry(authed, monkeypatch):
    monkeypatch.setenv("FAKE_DELAY", "2")
    r = authed.post("/api/jobs", json={"url": URL, "format": "m4a"})
    job_id = r.json()["jobs"][0]["id"]
    assert authed.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    job = wait_for(authed, job_id)
    assert job["status"] == "cancelled"
    monkeypatch.setenv("FAKE_DELAY", "0.01")
    assert authed.post(f"/api/jobs/{job_id}/retry").status_code == 200
    assert wait_for(authed, job_id)["status"] == "done"
    # Verlauf leeren
    assert authed.delete("/api/jobs").json()["deleted"] >= 1


def test_upload_own_audio(authed, fixtures):
    with open(fixtures["audio"], "rb") as fh:
        r = authed.post("/api/uploads", files={"file": ("Meine Aufnahme.webm", fh, "audio/webm")},
                        data={"format": "mp3", "title": "Episode 1", "artist": "Ich", "collection": "Mein Podcast",
                              "normalize": "true"})
    assert r.status_code == 200, r.text
    job = wait_for(authed, r.json()["jobs"][0]["id"])
    assert job["status"] == "done", job
    media = authed.get(f"/api/media/{job['media_id']}").json()
    assert media["path"] == "Mein Podcast/Ich - Episode 1.mp3"
    assert media["source_url"] is None


def test_upload_rejects_non_media(authed, env):
    r = authed.post("/api/uploads", files={"file": ("x.mp3", b"garbage", "audio/mpeg")}, data={"format": "mp3"})
    assert r.status_code == 400
    assert list((env / "temp" / "uploads").iterdir()) == []


def test_system_info(authed):
    data = authed.get("/api/system").json()
    assert data["versions"]["yt_dlp"] == "2099.01.01"
    assert data["versions"]["ffmpeg"]


def test_events_stream_requires_auth(client):
    assert client.get("/api/events").status_code == 401


def test_jobs_resume_after_restart(env, fixtures):
    """Ein beim Neustart 'running' Job wird wieder eingereiht und abgeschlossen."""
    import json

    from fastapi.testclient import TestClient

    from app.db import Database, utcnow
    from app.main import create_app

    db = Database(Path(env / "database") / "ytloader.db")
    db.insert("jobs", {"id": "f" * 32, "kind": "url", "url": URL, "format": "m4a", "options": json.dumps({}),
                       "status": "running", "progress": 0.5, "created_at": utcnow()})
    db.close()
    with TestClient(create_app()) as c:
        c.headers.update({"X-Requested-With": "ytloader"})
        c.post("/api/auth/login", json={"username": "admin", "password": "geheim"})
        assert wait_for(c, "f" * 32)["status"] == "done"
