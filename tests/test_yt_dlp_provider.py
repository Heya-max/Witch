import os
import sys
import types

import pytest


class FakeYDL:
    def __init__(self, opts=None):
        self.opts = opts or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, query, download=False):
        # Simulate ytsearch vs direct URL
        if str(query).startswith("ytsearch"):
            return {
                "entries": [
                    {
                        "id": "abc123",
                        "title": "Test Song",
                        "duration": 123,
                        "thumbnail": "http://example.com/thumb.jpg",
                        "webpage_url": "https://youtu.be/abc123",
                    }
                ]
            }

        # Direct video info with formats
        return {
            "id": "abc123",
            "title": "Test Song",
            "duration": 123,
            "thumbnail": "http://example.com/thumb.jpg",
            "webpage_url": "https://youtu.be/abc123",
            "formats": [
                {"format_id": "140", "acodec": "mp4a.40.2", "url": "https://audio.example/stream1"},
            ],
        }


class FakeDownloadYDL(FakeYDL):
    def extract_info(self, query, download=False):
        info = super().extract_info(query, download=download)
        if download:
            outtmpl = (self.opts.get("outtmpl") or "").replace("%(id)s", "abc123").replace("%(ext)s", "m4a")
            with open(outtmpl, "wb") as fh:
                fh.write(b"\0" * 10)
            info["requested_downloads"] = [{"filepath": outtmpl}]
        return info


def make_fake_yt_dlp():
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = FakeYDL
    return mod


@pytest.mark.asyncio
async def test_yt_dlp_provider_search_and_resolve(monkeypatch):
    # Ensure project root is on sys.path so `app` package is importable
    import os

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import app.sources.providers.yt_dlp_provider as provider_mod

    monkeypatch.setattr(provider_mod, "YoutubeDL", FakeYDL)

    provider = provider_mod.YtDlpProvider()

    results = await provider.search("test query")
    assert len(results) == 1
    track = results[0]
    assert track.title == "Test Song"
    assert track.source == "yt-dlp" or track.source == "yt-dlp"

    audio_url = await provider.resolve_audio("abc123")
    assert audio_url.startswith("https://")


def test_yt_dlp_provider_download(monkeypatch, tmp_path):
    import app.sources.providers.yt_dlp_provider as provider_mod

    monkeypatch.setattr(provider_mod, "YoutubeDL", FakeDownloadYDL)
    provider = provider_mod.YtDlpProvider()
    path = provider.download("abc123", str(tmp_path))
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0


def test_platform_providers_search_prefixes():
    from app.sources.providers.yt_dlp_platform import DeezerProvider, SoundCloudProvider, SpotifyProvider
    from app.sources.providers.yt_dlp_provider import YtDlpProvider

    assert YtDlpProvider()._search_query("some song") == "ytsearch5:some song"
    assert SpotifyProvider()._search_query("some song") == "spsearch5:some song"
    assert SoundCloudProvider()._search_query("some song") == "scsearch5:some song"
    assert DeezerProvider()._search_query("some song") == "dzsearch5:some song"


def test_default_providers_ordering():
    from app.sources import get_default_providers
    from app.sources.providers.simple_provider import SimpleProvider
    from app.sources.providers.yt_dlp_platform import SpotifyProvider

    providers = get_default_providers()
    assert isinstance(providers[0], SpotifyProvider)
    assert isinstance(providers[-1], SimpleProvider)
