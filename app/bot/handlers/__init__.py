from pyrogram import Client


def register_handlers(app: Client) -> None:
    # Import handlers here so they register their callbacks
    from . import help, playback, start

    start.register(app)
    help.register(app)
    playback.register(app)
