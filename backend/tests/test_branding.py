"""Erscheinungsbild: Name und Icon, vom Admin änderbar."""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from conftest import ffmpeg

H = {"X-Requested-With": "ytloader"}


def probe(content: bytes, tmp_path, name="x.png") -> dict:
    p = tmp_path / name
    p.write_bytes(content)
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(p)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["streams"][0]


@pytest.fixture
def image(tmp_path):
    def make(w=800, h=600, name="logo.png"):
        path = tmp_path / name
        ffmpeg("-f", "lavfi", "-i", f"testsrc=size={w}x{h}:duration=1", "-frames:v", "1", str(path))
        return path
    return make


def test_defaults(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>MyYTLoader</title>" in r.text
    assert "%%" not in r.text
    m = client.get("/manifest.webmanifest").json()
    assert m["name"] == "MyYTLoader" and len(m["icons"]) == 3
    info = client.get("/api/branding").json()
    assert info["custom_icon"] is False
    for url in info["icons"].values():
        assert client.get(url).status_code == 200


def test_rename(authed):
    v0 = authed.get("/api/branding").json()["version"]
    r = authed.put("/api/branding", json={"name": "  Meine   Hörbücher "})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["name"] == "Meine Hörbücher" and info["version"] != v0
    html = authed.get("/").text
    assert "<title>Meine Hörbücher</title>" in html
    assert 'apple-mobile-web-app-title" content="Meine Hörbücher"' in html
    assert f"?v={info['version']}" in html
    assert authed.get("/manifest.webmanifest").json()["short_name"] == "Meine Hörbücher"
    # zurücksetzen
    assert authed.put("/api/branding", json={"name": None}).json()["name"] == "MyYTLoader"


def test_name_is_escaped(authed):
    authed.put("/api/branding", json={"name": '<script>x</script>"'})
    html = authed.get("/").text
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert authed.get("/manifest.webmanifest").json()["name"] == '<script>x</script>"'


@pytest.mark.parametrize("name", ["", "   ", "x" * 41])
def test_invalid_names(authed, name):
    assert authed.put("/api/branding", json={"name": name}).status_code == 400


def test_icon_upload_generates_all_sizes(authed, image, tmp_path):
    with open(image(1200, 800), "rb") as fh:
        r = authed.post("/api/branding/icon", files={"file": ("logo.png", fh, "image/png")})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["custom_icon"] is True
    expected = {
        "icon-192.png": (192, True), "icon-512.png": (512, True), "icon-maskable-512.png": (512, False),
        "apple-touch-icon.png": (180, False), "podcast-1400.png": (1400, False),
    }
    for filename, (size, alpha) in expected.items():
        res = authed.get(info["icons"][filename])
        assert res.status_code == 200 and res.headers["content-type"] == "image/png"
        s = probe(res.content, tmp_path, filename)
        assert (s["width"], s["height"]) == (size, size), filename
        assert ("a" in s["pix_fmt"]) == alpha, (filename, s["pix_fmt"])  # rgba vs. rgb24
    # Reset
    info = authed.delete("/api/branding/icon").json()
    assert info["custom_icon"] is False
    assert authed.get(info["icons"]["icon-512.png"]).status_code == 200


@pytest.mark.parametrize("w,h", [(64, 64), (100, 400)])
def test_icon_too_small(authed, image, w, h):
    with open(image(w, h), "rb") as fh:
        r = authed.post("/api/branding/icon", files={"file": ("small.png", fh, "image/png")})
    assert r.status_code == 400 and "zu klein" in r.json()["detail"]


def test_icon_rejects_non_images(authed, fixtures, tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512"/></svg>'
    for name, data in (("x.svg", svg), ("x.png", b"garbage")):
        r = authed.post("/api/branding/icon", files={"file": (name, data, "image/png")})
        assert r.status_code == 400, name
    with open(fixtures["audio"], "rb") as fh:
        assert authed.post("/api/branding/icon", files={"file": ("a.png", fh, "image/png")}).status_code == 400
    assert authed.get("/api/branding").json()["custom_icon"] is False
    # temporäre Upload-Dateien werden aufgeräumt
    assert not list((tmp_path / "temp" / "uploads").glob("icon-*"))


def test_non_admin_cannot_change(authed):
    authed.post("/api/users", json={"username": "anna", "password": "passwort123"})
    anna = TestClient(authed.app)
    anna.headers.update(H)
    anna.post("/api/auth/login", json={"username": "anna", "password": "passwort123"})
    assert anna.put("/api/branding", json={"name": "X"}).status_code == 403
    assert anna.delete("/api/branding/icon").status_code == 403
    assert anna.post("/api/branding/icon", files={"file": ("x.png", b"x", "image/png")}).status_code == 403
    assert anna.get("/api/branding").status_code == 200


def test_unknown_icon_file(client):
    assert client.get("/branding/../../etc/passwd").status_code == 404
    assert client.get("/branding/secret.key").status_code == 404


def test_feed_uses_branding(authed, image):
    authed.put("/api/branding", json={"name": "Hörbuch-Server"})
    with open(image(600, 600), "rb") as fh:
        authed.post("/api/branding/icon", files={"file": ("logo.png", fh, "image/png")})
    feed = authed.get("/api/feed").json()
    path = feed["feeds"][0]["url"].split("testserver", 1)[1]
    xml = authed.get(path).text
    assert "<title>Hörbuch-Server</title>" in xml
    assert "<itunes:author>Hörbuch-Server</itunes:author>" in xml
    art = path.replace("podcast.xml", "artwork.png")
    r = authed.get(art)
    assert r.status_code == 200
    custom = authed.get(authed.get("/api/branding").json()["icons"]["podcast-1400.png"]).content
    assert r.content == custom
