from pyrogram import Client


def register_handlers(app: Client) -> None:
    # Import handlers here so they register their callbacks
    from . import favorites, health, help, inline, media, playback, start

    start.register(app)
    help.register(app)
    health.register(app)
    playback.register(app)
    media.register(app)
    favorites.register(app)
    inline.register(app)
