from app.bot.handlers.start import start_text


def test_start_text_contains_play():
    txt = start_text()
    assert "/play" in txt or "play" in txt.lower()
