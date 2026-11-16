"""Mehrere Benutzer: Rollen, Trennung, Feeds, Kontingente, Job-Limits, Migration."""
import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import wait_for

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
H = {"X-Requested-With": "ytloader"}


def login(app, username, password) -> TestClient:
    c = TestClient(app)
    c.headers.update(H)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def make_user(admin, name="anna", **extra):
    r = admin.post("/api/users", json={"username": name, "password": "passwort123", **extra})
    assert r.status_code == 200, r.text
    return r.json()


def download(client, fmt="m4a") -> dict:
    r = client.post("/api/jobs", json={"url": URL, "format": fmt})
    assert r.status_code == 200, r.text
    return wait_for(client, r.json()["jobs"][0]["id"])


# ------------------------------------------------------------------ Rollen


def test_user_management_admin_only(authed):
    user = make_user(authed)
    assert user["role"] == "user" and "password_hash" not in user and "feed_token" not in user
    anna = login(authed.app, "anna", "passwort123")
    assert anna.get("/api/users").status_code == 403
    assert anna.post("/api/users", json={"username": "x", "password": "passwort123"}).status_code == 403
    assert anna.post("/api/system/ytdlp/update").status_code == 403
    assert anna.post("/api/system/cleanup-temp").status_code == 403
    me = anna.get("/api/auth/me").json()
    assert me["user"]["username"] == "anna" and me["user"]["role"] == "user"
    assert anna.get("/api/system").json()["is_admin"] is False


@pytest.mark.parametrize("name", ["A", "x", "über", "a b", "../etc", ".hidden", "a" * 40])
def test_username_validation(authed, name):
    r = authed.post("/api/users", json={"username": name, "password": "passwort123"})
    assert r.status_code in (400, 422)


def test_password_rules_and_duplicates(authed):
    assert authed.post("/api/users", json={"username": "kurz", "password": "123"}).status_code == 400
    make_user(authed, "bert")
    # Benutzernamen sind nicht case-sensitiv ("Bert" == "bert")
    assert authed.post("/api/users", json={"username": "Bert", "password": "passwort123"}).status_code == 409
    login(authed.app, "BERT", "passwort123")


def test_last_admin_protected(authed):
    admin_id = authed.get("/api/auth/me").json()["user"]["id"]
    assert authed.patch(f"/api/users/{admin_id}", json={"role": "user"}).status_code == 409
    assert authed.patch(f"/api/users/{admin_id}", json={"disabled": True}).status_code == 409
    assert authed.delete(f"/api/users/{admin_id}").status_code == 409


# ---------------------------------------------------------------- Trennung


def test_libraries_are_isolated(authed, env):
    make_user(authed, "anna")
    anna = login(authed.app, "anna", "passwort123")
    admin_job = download(authed)
    anna_job = download(anna, "mp3")
    assert admin_job["status"] == anna_job["status"] == "done"
    admin_media, anna_media = admin_job["media_id"], anna_job["media_id"]

    # Ablage pro Benutzer
    assert anna.get(f"/api/media/{anna_media}").json()["path"].startswith("anna/")
    # Anna sieht nur eigene Medien und Aufträge
    assert [m["id"] for m in anna.get("/api/media").json()] == [anna_media]
    assert {j["id"] for j in anna.get("/api/jobs").json()} == {anna_job["id"]}
    for path in (f"/api/media/{admin_media}", f"/api/media/{admin_media}/file",
                 f"/api/media/{admin_media}/download", f"/api/media/{admin_media}/cover"):
        assert anna.get(path).status_code == 404, path
    assert anna.patch(f"/api/media/{admin_media}", json={"title": "x"}).status_code == 404
    assert anna.delete(f"/api/media/{admin_media}").status_code == 404
    assert anna.post(f"/api/jobs/{admin_job['id']}/retry").status_code == 404
    assert anna.delete(f"/api/jobs/{admin_job['id']}").status_code == 404
    # Verlauf leeren betrifft nur die eigenen Aufträge
    anna.delete("/api/jobs")
    assert authed.get(f"/api/media/{admin_media}").status_code == 200
    assert any(j["id"] == admin_job["id"] for j in authed.get("/api/jobs").json())

    # Admin: standardmäßig eigene Bibliothek, optional alle oder ein bestimmter Benutzer
    assert [m["id"] for m in authed.get("/api/media").json()] == [admin_media]
    everyone = authed.get("/api/media", params={"owner": "all"}).json()
    assert {m["id"] for m in everyone} == {admin_media, anna_media}
    assert {m["owner"] for m in everyone} == {"admin", "anna"}
    # Admin darf fremde Medien verwalten
    assert authed.get(f"/api/media/{anna_media}/download").status_code == 200


def test_feeds_are_per_user(authed):
    make_user(authed, "anna")
    anna = login(authed.app, "anna", "passwort123")
    admin_media = download(authed)["media_id"]
    download(anna)
    admin_feed = authed.get("/api/feed").json()
    anna_feed = anna.get("/api/feed").json()
    assert admin_feed["media_base"] != anna_feed["media_base"]

    def path(url):
        return url.split("testserver", 1)[1]

    r = anna.get(path(anna_feed["feeds"][0]["url"]))
    assert r.status_code == 200 and r.text.count("<item>") == 1
    # Annas Token öffnet keine Admin-Medien
    anna_base = path(anna_feed["media_base"])
    assert anna.get(f"{anna_base}/{admin_media}/x.m4a").status_code == 404
    # Token rotieren -> alter Feed ungültig
    anna.post("/api/feed/rotate")
    assert anna.get(path(anna_feed["feeds"][0]["url"])).status_code == 404


def test_events_filtered_by_owner():
    from app.jobs import EventBus

    async def run():
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        admin_q = bus.subscribe({"id": "a", "role": "admin"})
        anna_q = bus.subscribe({"id": "anna", "role": "user"})
        bert_q = bus.subscribe({"id": "bert", "role": "user"})
        bus.publish({"type": "job", "user_id": "anna"})
        bus.publish({"type": "media", "user_id": "bert"})
        bus.publish({"type": "jobs_cleared"})  # ohne Besitzer -> nur Admins
        await asyncio.sleep(0.05)
        return admin_q.qsize(), anna_q.qsize(), bert_q.qsize()

    assert asyncio.run(run()) == (3, 1, 1)


# ------------------------------------------------------- Sitzungen/Konto


def test_disable_ends_session(authed):
    user = make_user(authed, "anna")
    anna = login(authed.app, "anna", "passwort123")
    assert anna.get("/api/jobs").status_code == 200
    assert authed.patch(f"/api/users/{user['id']}", json={"disabled": True}).status_code == 200
    assert anna.get("/api/jobs").status_code == 401
    r = anna.post("/api/auth/login", json={"username": "anna", "password": "passwort123"})
    assert r.status_code == 401


def test_change_password(authed):
    make_user(authed, "anna")
    first = login(authed.app, "anna", "passwort123")
    second = login(authed.app, "anna", "passwort123")
    assert first.post("/api/auth/password", json={"current": "falsch!!", "new": "neuespasswort"}).status_code == 403
    assert first.post("/api/auth/password", json={"current": "passwort123", "new": "kurz"}).status_code == 400
    assert first.post("/api/auth/password", json={"current": "passwort123", "new": "neuespasswort"}).status_code == 200
    assert first.get("/api/jobs").status_code == 200       # aktuelle Sitzung bleibt
    assert second.get("/api/jobs").status_code == 401      # andere Sitzungen enden
    login(authed.app, "anna", "neuespasswort")


def test_admin_resets_password(authed):
    user = make_user(authed, "anna")
    assert authed.patch(f"/api/users/{user['id']}", json={"password": "zurueckgesetzt"}).status_code == 200
    login(authed.app, "anna", "zurueckgesetzt")


def test_delete_user_removes_media(authed, env):
    user = make_user(authed, "anna")
    anna = login(authed.app, "anna", "passwort123")
    job = download(anna)
    path = env / "downloads" / anna.get(f"/api/media/{job['media_id']}").json()["path"]
    assert path.is_file()
    r = authed.delete(f"/api/users/{user['id']}")
    assert r.status_code == 200 and r.json()["deleted_media"] == 1
    assert not path.exists()
    assert not (env / "downloads" / "anna").exists()
    assert anna.get("/api/jobs").status_code == 401
    assert all(u["username"] != "anna" for u in authed.get("/api/users").json()["users"])


# ------------------------------------------------------------ Kontingente


def _fake_usage(svc, user_id: str, size: int) -> None:
    svc.db.insert("media", {
        "id": uuid.uuid4().hex, "user_id": user_id, "title": "Platzhalter", "kind": "audio", "format": "m4a",
        "path": f"placeholder-{uuid.uuid4().hex}.m4a", "size": size, "created_at": "2024-01-01T00:00:00+00:00",
    })


def test_quota_exhausted_rejects_new_jobs(authed):
    user = make_user(authed, "anna", quota_mb=1)
    anna = login(authed.app, "anna", "passwort123")
    _fake_usage(authed.app.state.services, user["id"], 1024 * 1024)
    r = anna.post("/api/jobs", json={"url": URL, "format": "m4a"})
    assert r.status_code == 413 and "Speicherkontingent" in r.json()["detail"]
    me = anna.get("/api/auth/me").json()["user"]
    assert me["quota_bytes"] == 1024 * 1024 and me["remaining_bytes"] == 0


def test_quota_limits_download_size(authed):
    user = make_user(authed, "anna", quota_mb=1)
    anna = login(authed.app, "anna", "passwort123")
    _fake_usage(authed.app.state.services, user["id"], 1024 * 1024 - 20_000)  # 20 KB frei
    job = download(anna)
    assert job["status"] == "failed"
    assert "Speicherkontingent" in job["error"]
    assert anna.get("/api/media").json()[0]["title"] == "Platzhalter"  # nichts Neues abgelegt


def test_quota_shown_in_user_list(authed):
    user = make_user(authed, "anna", quota_mb=5, max_jobs=1)
    _fake_usage(authed.app.state.services, user["id"], 2048)
    listed = next(u for u in authed.get("/api/users").json()["users"] if u["id"] == user["id"])
    assert listed["quota_mb"] == 5 and listed["max_jobs"] == 1
    assert listed["usage_bytes"] == 2048 and listed["media_count"] == 1


def test_default_limits_from_env(env, monkeypatch):
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DEFAULT_USER_QUOTA_MB", "500")
    monkeypatch.setenv("DEFAULT_USER_MAX_JOBS", "2")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.headers.update(H)
        c.post("/api/auth/login", json={"username": "admin", "password": "geheim"})
        user = make_user(c, "anna")
        assert user["quota_mb"] == 500 and user["max_jobs"] == 2
        explicit = make_user(c, "bert", quota_mb=0, max_jobs=0)
        assert explicit["quota_mb"] is None and explicit["max_jobs"] is None


# ---------------------------------------------------------- Job-Limits


def test_claim_respects_per_user_max_jobs(env):
    """Scheduler nimmt den ältesten Auftrag, dessen Besitzer sein Limit nicht erreicht hat."""
    from app.config import get_settings
    from app.main import build_services

    svc = build_services(get_settings())
    anna = svc.users.create("anna", "passwort123", max_jobs=1)
    bert = svc.users.create("bert", "passwort123")
    a1 = svc.jobs.create(user=anna, url=URL, fmt="m4a", options={})
    a2 = svc.jobs.create(user=anna, url=URL, fmt="m4a", options={})
    b1 = svc.jobs.create(user=bert, url=URL, fmt="m4a", options={})
    assert svc.jobs._claim()["id"] == a1["id"]
    assert svc.jobs._claim()["id"] == b1["id"]  # a2 wartet: Anna hat bereits einen laufenden Auftrag
    assert svc.jobs._claim() is None
    svc.db.update("jobs", a1["id"], {"status": "done"})
    assert svc.jobs._claim()["id"] == a2["id"]
    svc.db.close()


# -------------------------------------------------------------- Migration


def test_migration_from_single_user(env, fixtures):
    """v1-Datenbank: Bestand und Feed-Token gehen an den Admin, alte Feed-URL bleibt gültig."""
    from app.config import get_settings
    from app.db import MIGRATIONS
    from app.main import create_app

    db_path = Path(env / "database")
    db_path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path / "ytloader.db")
    conn.executescript(f"BEGIN; {MIGRATIONS[0]}; PRAGMA user_version=1; COMMIT;")
    dl = Path(env / "downloads")
    dl.mkdir(parents=True, exist_ok=True)
    (dl / "Alt - Folge.m4a").write_bytes(Path(fixtures["audio"]).read_bytes())
    conn.execute("INSERT INTO media(id,title,kind,format,path,size,created_at) VALUES(?,?,?,?,?,?,?)",
                 ("m" * 32, "Folge", "audio", "m4a", "Alt - Folge.m4a", 123, "2024-01-01T00:00:00+00:00"))
    conn.execute("INSERT INTO jobs(id,url,format,status,created_at,media_id) VALUES(?,?,?,?,?,?)",
                 ("j" * 32, URL, "m4a", "done", "2024-01-01T00:00:00+00:00", "m" * 32))
    conn.execute("INSERT INTO kv(key,value) VALUES('feed_token','alter-token-123')")
    conn.commit()
    conn.close()

    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.headers.update(H)
        c.post("/api/auth/login", json={"username": "admin", "password": "geheim"})
        assert [m["id"] for m in c.get("/api/media").json()] == ["m" * 32]
        assert [j["id"] for j in c.get("/api/jobs").json()] == ["j" * 32]
        r = c.get("/feed/alter-token-123/podcast.xml")
        assert r.status_code == 200 and "Folge" in r.text
        assert c.get(f"/feed/alter-token-123/media/{'m' * 32}/x.m4a").status_code == 200
        assert json.loads(json.dumps(c.get("/api/feed").json()))["media_base"].endswith("/feed/alter-token-123/media")


def test_auth_disabled_single_user_mode(env, monkeypatch):
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.headers.update(H)
        me = c.get("/api/auth/me").json()
        assert me["authenticated"] and me["user"]["role"] == "admin"
        assert c.get("/api/jobs").status_code == 200
