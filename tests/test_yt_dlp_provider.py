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
