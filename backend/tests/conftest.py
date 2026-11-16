from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory) -> dict[str, Path]:
    """Testmedien: Audio mit Stille am Anfang/Ende, Video (VP9/Opus), Coverbild."""
    d = tmp_path_factory.mktemp("fixtures")
    audio = d / "audio.webm"
    ffmpeg(
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=3",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=3",
        "-filter_complex", "[1]aformat=channel_layouts=stereo,volume=0.3[t];[0][t][2]concat=n=3:v=0:a=1",
        "-c:a", "libopus", "-b:a", "96k", str(audio),
    )
    video = d / "video.webm"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
        "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "200k",
        "-c:a", "libopus", str(video),
    )
    cover = d / "cover.jpg"
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360:duration=1", "-frames:v", "1", str(cover))
    return {"audio": audio, "video": video, "cover": cover, "dir": d}


FAKE_YTDLP = r'''
#!{python}
"""Fake yt-dlp: gleicher CLI-Vertrag wie die App ihn nutzt (Analyse, Download, Fortschritt)."""
import json, os, shutil, sys, time
args = sys.argv[1:]
if "--version" in args:
    print("2099.01.01"); sys.exit(0)
url = args[args.index("--") + 1]
if "fail-bot" in url:
    print("ERROR: [youtube] abc: Sign in to confirm you're not a bot", file=sys.stderr); sys.exit(1)
if "fail-drm" in url:
    print("ERROR: [youtube] abc: This video is DRM protected", file=sys.stderr); sys.exit(1)
if "--dump-single-json" in args:
    if "playlist" in url:
        print(json.dumps({{"_type": "playlist", "id": "PL1", "title": "Meine Liste", "channel": "Kanal X",
            "webpage_url": url, "entries": [
                {{"id": "aaaaaaaaaaa", "title": "Folge 1", "duration": 10, "ie_key": "Youtube"}},
                {{"id": "bbbbbbbbbbb", "title": "Folge 2", "duration": 10, "ie_key": "Youtube"}}]}}))
    else:
        print(json.dumps({{"id": "dQw4w9WgXcQ", "title": "Test: Titel / mit Sonderzeichen?", "channel": "Testkanal",
            "description": "Beschreibung", "duration": 10, "webpage_url": url, "upload_date": "20240131",
            "thumbnails": [{{"url": "https://i.ytimg.com/vi/x/hq.jpg", "width": 480}}],
            "formats": [{{"format_id": "251", "acodec": "opus", "vcodec": "none", "abr": 130}},
                        {{"format_id": "137", "acodec": "none", "vcodec": "avc1", "height": 1080}}]}}))
    sys.exit(0)
paths = args[args.index("--paths") + 1]
fmt = args[args.index("--format") + 1]
os.makedirs(paths, exist_ok=True)
if "acodec~=" in fmt:
    print("ERROR: [youtube] x: Requested format is not available", file=sys.stderr); sys.exit(1)
src = os.environ["FAKE_AUDIO"] if fmt.startswith("ba") else os.environ["FAKE_VIDEO"]
total = os.path.getsize(src)
max_size = int(args[args.index("--max-filesize") + 1])
if total > max_size:
    print("[download] File is larger than max-filesize ({{}} bytes > {{}} bytes). Aborting.".format(total, max_size))
    sys.exit(0)
for i in range(1, 5):
    print("@@P {{}}|{{}}|NA|{{}}|{{}}|251".format(total * i // 4, total, 100000.0, 4 - i), flush=True)
    time.sleep(float(os.environ.get("FAKE_DELAY", "0.05")))
dest = os.path.join(paths, "media.webm")
shutil.copy(src, dest)
with open(os.path.join(paths, "info.info.json"), "w") as fh:
    json.dump({{"id": "dQw4w9WgXcQ", "title": "Test: Titel / mit Sonderzeichen?", "channel": "Testkanal",
               "description": "Beschreibung", "webpage_url": url, "upload_date": "20240131"}}, fh)
shutil.copy(os.environ["FAKE_COVER"], os.path.join(paths, "cover.jpg"))
print("@@F " + dest, flush=True)
'''


@pytest.fixture
def env(tmp_path, fixtures, monkeypatch):
    venv = tmp_path / "ytdlp-venv"
    (venv / "bin").mkdir(parents=True)
    fake = venv / "bin" / "yt-dlp"
    fake.write_text(textwrap.dedent(FAKE_YTDLP).lstrip().format(python=sys.executable))
    fake.chmod(0o755)
    values = {
        "DOWNLOAD_PATH": str(tmp_path / "downloads"),
        "TEMP_PATH": str(tmp_path / "temp"),
        "DATABASE_PATH": str(tmp_path / "database"),
        "CONFIG_PATH": str(tmp_path / "config"),
        "FRONTEND_PATH": str(ROOT / "frontend"),
        "YTDLP_BUILTIN_VENV": str(venv),
        "YTDLP_AUTO_UPDATE": "false",
        "AUTH_ENABLED": "true",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "geheim",
        "CHECK_DNS": "false",
        "MAX_CONCURRENT_DOWNLOADS": "2",
        "FAKE_AUDIO": str(fixtures["audio"]),
        "FAKE_VIDEO": str(fixtures["video"]),
        "FAKE_COVER": str(fixtures["cover"]),
        "PATH": f"{venv / 'bin'}:{os.environ['PATH']}",
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        c.headers.update({"X-Requested-With": "ytloader"})
        yield c


@pytest.fixture
def authed(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "geheim"})
    assert r.status_code == 200, r.text
    return client


def wait_for(client, job_id: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = next(j for j in client.get("/api/jobs").json() if j["id"] == job_id)
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"Job {job_id} nicht fertig geworden")

