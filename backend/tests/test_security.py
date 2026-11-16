from pathlib import Path

import pytest

from app.security import (RateLimiter, UserError, media_basename, safe_join, sanitize_filename,
                          validate_url, youtube_video_url)

ALLOWED = ("youtube.com", "youtu.be")


def public(_host):
    return ["142.250.185.78"]


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"),
    ("http://m.youtube.com/watch?v=x#t=10", "https://m.youtube.com/watch?v=x"),
    ("https://music.youtube.com/playlist?list=PL1", "https://music.youtube.com/playlist?list=PL1"),
])
def test_valid_urls(url, expected):
    assert validate_url(url, ALLOWED, resolver=public) == expected


@pytest.mark.parametrize("url", [
    "",
    "ftp://youtube.com/x",
    "file:///etc/passwd",
    "https://youtube.com.evil.com/watch",
    "https://evilyoutube.com/watch",
    "https://user:pw@youtube.com/watch",
    "https://youtube.com:8443/watch",
    "https://127.0.0.1/watch",
    "https://[::1]/watch",
    "https://example.com/",
    "https://www.youtube.com/watch?v=a b",
])
def test_rejected_urls(url):
    with pytest.raises(UserError):
        validate_url(url, ALLOWED, resolver=public)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", "fd00::1"])
def test_ssrf_private_resolution_blocked(ip):
    with pytest.raises(UserError, match="interne"):
        validate_url("https://www.youtube.com/watch?v=x", ALLOWED, resolver=lambda h: [ip])


def test_dns_failure():
    def boom(_):
        raise OSError("nope")

    with pytest.raises(UserError, match="aufgelöst"):
        validate_url("https://www.youtube.com/watch?v=x", ALLOWED, resolver=boom)


def test_video_id_validation():
    assert youtube_video_url("dQw4w9WgXcQ").endswith("v=dQw4w9WgXcQ")
    for bad in ["../../etc", "short", "dQw4w9WgXcQ&x=1", "-o/tmp/xxxxxx"]:
        with pytest.raises(UserError):
            youtube_video_url(bad)


@pytest.mark.parametrize("raw,expected", [
    ("Kanal - Titel", "Kanal - Titel"),
    ("a/b\\c:d*e?f\"g<h>i|j", "a-b-c -defghi-j"),
    ("  ..hidden  ", "hidden"),
    ("CON", "Unbenannt"),
    ("", "Unbenannt"),
    ("Tab\tund\nNewline", "TabundNewline"),
    ("Café – Ü", "Café – Ü"),
])
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_length_limit():
    assert len(sanitize_filename("ä" * 500).encode()) <= 180


def test_media_basename():
    assert media_basename("Kanal", "Video: Teil 1/2") == "Kanal - Video - Teil 1-2"
    assert media_basename("Kanal", "Kanal - Folge 3") == "Kanal - Folge 3"
    assert media_basename(None, "Nur Titel") == "Nur Titel"


def test_safe_join(tmp_path: Path):
    assert safe_join(tmp_path, "a", "b.m4a") == (tmp_path / "a" / "b.m4a").resolve()
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(UserError):
            safe_join(tmp_path, bad)


def test_rate_limiter():
    rl = RateLimiter(limit=3, window_seconds=60)
    for _ in range(3):
        rl.check("1.2.3.4")
    with pytest.raises(UserError) as exc:
        rl.check("1.2.3.4")
    assert exc.value.status_code == 429
    rl.check("5.6.7.8")
