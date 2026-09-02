"""Coverage for app.sources: validation edge cases, base classes, providers, resolve_playable."""

import app.sources as sources_mod
import app.sources.base as base_mod
import app.sources.models as models_mod
import app.sources.providers.simple_provider as simple_mod
import app.sources.providers.yt_dlp_provider as yt_mod
import app.sources.validation as validation_mod
import pytest

BAD_IPV6 = "http://[::1"  # urlsplit raises ValueError on malformed IPv6 brackets


def test_validation_host_and_extension_value_errors():
    assert validation_mod._host(BAD_IPV6) == ""
    assert validation_mod._file_extension(BAD_IPV6) == ""
    assert validation_mod._host_matches("", validation_mod.KNOWN_MEDIA_HOSTS) is False


def test_base_music_source_raises():
    class Concrete(base_mod.MusicSource):
        pass

    src = Concrete()
    with pytest.raises(NotImplementedError):
        assert asyncio_run(src.search("q")) is None
    with pytest.raises(NotImplementedError):
        assert asyncio_run(src.get_metadata("sid")) is None
    with pytest.raises(NotImplementedError):
        assert asyncio_run(src.resolve_audio("sid")) is None


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_search_result_model():
    row = models_mod.SearchResult(id="1", title="T", duration=3, thumbnail="t.jpg", source="sc")
    assert row.id == "1"
    assert row.title == "T"
    assert row.duration == 3
    assert row.thumbnail == "t.jpg"
    assert row.source == "sc"
    assert models_mod.SearchResult(id="2", title="U").duration is None


class _Track:
    def __init__(self, **kw):
        self.resolve_key = kw.get("resolve_key")
        self.source_url = kw.get("source_url")
        self.source_id = kw.get("source_id")
        self.id = kw.get("id")


class _RaiseGeneric:
    async def resolve_audio(self, key):
        raise RuntimeError("provider exploded")


class _RaiseValue:
    async def resolve_audio(self, key):
        raise ValueError("refusing non-audio URL")


class _ReturnNone:
    async def resolve_audio(self, key):
        return None


def test_resolve_playable_empty_key():
    assert asyncio_run(sources_mod.resolve_playable(_Track(id=""))) == ""


def test_resolve_playable_all_providers_fail(monkeypatch):
    monkeypatch.setattr(
        sources_mod,
        "get_default_providers",
        lambda: [_RaiseGeneric(), _RaiseValue(), _ReturnNone()],
    )
    with pytest.raises(ValueError):
        asyncio_run(sources_mod.resolve_playable(_Track(id="someid")))


def test_simple_provider_local_query_and_metadata_and_resolve():
    provider = simple_mod.SimpleProvider()
    (track,) = asyncio_run(provider.search("hello world"))
    assert track.id == "local:hello world"
    assert track.source == "simple"

    meta = asyncio_run(provider.get_metadata("abc"))
    assert meta.id == "abc"
    assert meta.source == "simple"

    with pytest.raises(NotImplementedError):
        assert asyncio_run(provider.resolve_audio("abc")) is None


def test_track_try_from_dict_guards():
    from app.player.models import Track

    assert Track.try_from_dict(None) is None
    assert Track.try_from_dict({"title": "no id"}) is None
    assert Track.try_from_dict({"id": "", "title": "empty id"}) is None
    assert Track.try_from_dict({"id": "x", "title": "ok"}).title == "ok"


def test_simple_provider_audio_url_roundtrip():
    provider = simple_mod.SimpleProvider()
    url = "https://cdn.example.com/audio.mp3"
    (track,) = asyncio_run(provider.search(url))
    assert track.source_url == url
    (webpage,) = asyncio_run(provider.search("https://techaistudy.com")) or [None]
    assert webpage is None
    assert asyncio_run(provider.resolve_audio(url)) == url
    with pytest.raises(ValueError):
        assert asyncio_run(provider.resolve_audio("https://techaistudy.com")) is None


class FakeYouTubeDL:
    def __init__(self, info=None, exc=None):
        self.info = info
        self.exc = exc
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        self.calls.append((url, download))
        if self.exc:
            raise self.exc
        return self.info


def _patch_ydl(monkeypatch, fake):
    monkeypatch.setattr(yt_mod, "YoutubeDL", lambda opts=None: fake)


def test_silent_logger_warning():
    yt_mod._SilentLogger().warning("skip", 1)


def test_ytdl_search_error_and_entries(monkeypatch):
    fake = FakeYouTubeDL(exc=RuntimeError("network"))
    _patch_ydl(monkeypatch, fake)
    provider = yt_mod.YtDlpProvider()
    assert asyncio_run(provider.search("news")) == []

    item = {
        "id": "vid1",
        "title": "A Song",
        "duration": 90,
        "thumbnail": "t.jpg",
        "webpage_url": "https://youtube.com/watch?v=vid1",
    }
    monkeypatch.setattr(
        yt_mod,
        "YoutubeDL",
        lambda opts=None: FakeYouTubeDL(info={"entries": [item, {"id": None, "webpage_url": "https://y/x"}]}),
    )
    results = asyncio_run(provider.search("news"))
    assert len(results) == 2
    assert results[0].id == "vid1"
    assert results[0].source == "yt-dlp"
    assert results[0].metadata is item


def test_ytdl_resolve_playlist(monkeypatch):
    provider = yt_mod.YtDlpProvider()

    fake_none = FakeYouTubeDL(info=None)
    _patch_ydl(monkeypatch, fake_none)
    with pytest.raises(RuntimeError):
        asyncio_run(provider.resolve_playlist("https://example.com/x"))

    leaf = {
        "id": "a",
        "title": "Leaf",
        "webpage_url": "https://x/a",
        "url": "https://cdn/x.mp3",
    }
    playlist_info = {
        "_type": "playlist",
        "entries": [
            {"id": "b", "title": "B", "webpage_url": "https://x/b"},
            {"_type": "playlist"},  # nested playlist -> skipped
            None,
        ],
    }
    fake_list = FakeYouTubeDL(info=playlist_info)
    _patch_ydl(monkeypatch, fake_list)
    tracks = asyncio_run(provider.resolve_playlist("https://example.com/pl"))
    assert [t.id for t in tracks] == ["b"]

    fake_single = FakeYouTubeDL(info=leaf)
    _patch_ydl(monkeypatch, fake_single)
    (single,) = asyncio_run(provider.resolve_playlist("https://example.com/track"))
    assert single.id == "a"


def test_ytdl_get_metadata(monkeypatch):
    info = {
        "id": "vid2",
        "title": "Meta Song",
        "duration": 10,
        "thumbnail": "th.jpg",
        "webpage_url": "https://youtube.com/watch?v=vid2",
    }
    fake = FakeYouTubeDL(info=info)
    _patch_ydl(monkeypatch, fake)
    provider = yt_mod.YtDlpProvider()

    track = asyncio_run(provider.get_metadata("vid2"))
    assert track.id == "vid2"
    assert "watch?v=vid2" in fake.calls[0][0]
    assert track.metadata is info

    asyncio_run(provider.get_metadata("https://example.com/direct"))
    assert fake.calls[1][0] == "https://example.com/direct"


def test_ytdl_resolve_audio_paths(monkeypatch):
    provider = yt_mod.YtDlpProvider()

    fake_none = FakeYouTubeDL(info=None)
    _patch_ydl(monkeypatch, fake_none)
    with pytest.raises(RuntimeError):
        asyncio_run(provider.resolve_audio("https://example.com/x"))

    fake_url = FakeYouTubeDL(info={"url": "https://cdn/x.m4a"})
    _patch_ydl(monkeypatch, fake_url)
    assert asyncio_run(provider.resolve_audio("vid3")) == "https://cdn/x.m4a"
    assert "watch?v=vid3" in fake_url.calls[0][0]

    fake_formats = FakeYouTubeDL(info={"formats": [{"acodec": "mp4a", "url": "https://cdn/a.m4a"}]})
    _patch_ydl(monkeypatch, fake_formats)
    assert asyncio_run(provider.resolve_audio("vid4")) == "https://cdn/a.m4a"

    fake_mute = FakeYouTubeDL(info={"formats": [{"acodec": "none", "url": "https://cdn/v.mp4"}]})
    _patch_ydl(monkeypatch, fake_mute)
    with pytest.raises(RuntimeError):
        asyncio_run(provider.resolve_audio("vid5"))


def test_ytdl_download_paths(monkeypatch, tmp_path):
    provider = yt_mod.YtDlpProvider()
    real_file = tmp_path / "out.opus"
    real_file.write_bytes(b"audio")

    fake_none = FakeYouTubeDL(info=None)
    _patch_ydl(monkeypatch, fake_none)
    with pytest.raises(RuntimeError):
        provider.download("vid6", str(tmp_path))

    fake_req = FakeYouTubeDL(info={"requested_downloads": [{"filepath": str(real_file)}]})
    _patch_ydl(monkeypatch, fake_req)
    assert provider.download("vid7", str(tmp_path)) == str(real_file)

    fake_info = FakeYouTubeDL(info={"requested_downloads": [], "filepath": str(real_file)})
    _patch_ydl(monkeypatch, fake_info)
    assert provider.download("vid8", str(tmp_path)) == str(real_file)

    fake_glob = FakeYouTubeDL(info={"requested_downloads": [], "filepath": None})
    _patch_ydl(monkeypatch, fake_glob)
    assert provider.download("vid9", str(tmp_path)) == str(real_file)

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    fake_empty = FakeYouTubeDL(info={"requested_downloads": []})
    _patch_ydl(monkeypatch, fake_empty)
    with pytest.raises(RuntimeError):
        provider.download("vid10", str(empty_dir))
