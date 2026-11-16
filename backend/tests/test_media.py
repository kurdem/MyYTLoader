"""FFmpeg-Pipeline mit echten Testmedien."""
import base64

import pytest
from mutagen import File as MutagenFile

from app.media import FFmpeg, Metadata, write_tags

ff = FFmpeg()


@pytest.mark.parametrize("fmt", ["m4a", "mp3", "opus", "flac"])
def test_convert_audio_with_tags(fmt, fixtures, tmp_path):
    out = tmp_path / f"out.{fmt}"
    ff.convert_audio(fixtures["audio"], out, fmt, "128k", normalize=True, trim_silence=True)
    probe = ff.probe(out)
    expected_codec = {"m4a": "aac", "mp3": "mp3", "opus": "opus", "flac": "flac"}[fmt]
    assert ff.stream(probe, "audio")["codec_name"] == expected_codec
    # 3 s Stille + 4 s Ton + 3 s Stille -> nach Trimmen ca. 4-5 s
    assert 3.5 < ff.duration(probe) < 5.5

    cover = ff.make_cover(fixtures["cover"], tmp_path / "c.jpg", square=True)
    assert cover is not None
    cprobe = ff.probe(cover)
    cs = ff.stream(cprobe, "video")
    assert cs["width"] == cs["height"]

    meta = Metadata(title="Täst Titel", artist="Kanal", album="Album", description="Beschreibung", date="2024-01-31")
    write_tags(out, fmt, meta, cover)
    f = MutagenFile(str(out))
    if fmt == "mp3":
        assert str(f.tags["TIT2"]) == "Täst Titel"
        assert str(f.tags["TPE1"]) == "Kanal"
        assert f.tags.getall("APIC")
    elif fmt == "m4a":
        assert f.tags["\xa9nam"] == ["Täst Titel"]
        assert f.tags["\xa9ART"] == ["Kanal"]
        assert f.tags["covr"]
    else:
        assert f["title"] == ["Täst Titel"]
        assert f["artist"] == ["Kanal"]
        if fmt == "flac":
            assert f.pictures
        else:
            assert base64.b64decode(f["metadata_block_picture"][0])


def test_loudness_normalized(fixtures, tmp_path):
    out = tmp_path / "n.m4a"
    ff.convert_audio(fixtures["audio"], out, "m4a", "192k", normalize=True, trim_silence=False)
    analysis = ff.analyze_audio(out)
    assert abs(float(analysis["loudnorm"]["input_i"]) - (-16)) < 2.5


def test_copy_without_filters(fixtures, tmp_path):
    out = tmp_path / "c.opus"
    ff.convert_audio(fixtures["audio"], out, "opus", "128k", normalize=False, trim_silence=False)
    assert ff.stream(ff.probe(out), "audio")["codec_name"] == "opus"
    assert 9.5 < ff.duration(ff.probe(out)) < 10.5


def test_trim_window():
    assert FFmpeg.trim_window([(0.0, 3.0), (7.0, None)], 10.0) == (2.7, 7.3)
    assert FFmpeg.trim_window([], 10.0) == (0.0, None)
    # Stille nur in der Mitte -> nichts trimmen
    assert FFmpeg.trim_window([(4.0, 6.0)], 10.0) == (0.0, None)


def test_convert_video_to_ios_mp4(fixtures, tmp_path):
    out = tmp_path / "v.mp4"
    ff.convert_video(fixtures["video"], out, "mp4")
    probe = ff.probe(out)
    assert ff.stream(probe, "video")["codec_name"] == "h264"
    assert ff.stream(probe, "audio")["codec_name"] == "aac"
    write_tags(out, "mp4", Metadata(title="Video"), None)
    assert MutagenFile(str(out)).tags["\xa9nam"] == ["Video"]


def test_convert_video_webm_with_metadata(fixtures, tmp_path):
    out = tmp_path / "v.webm"
    ff.convert_video(fixtures["video"], out, "webm", Metadata(title="Web Video", artist="K"))
    probe = ff.probe(out)
    assert ff.stream(probe, "video")["codec_name"] == "vp9"
    tags = {k.lower(): v for k, v in probe["format"].get("tags", {}).items()}
    assert tags.get("title") == "Web Video"


def test_probe_rejects_garbage(tmp_path):
    from app.security import UserError

    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not a media file")
    with pytest.raises(UserError):
        ff.probe(bad)
