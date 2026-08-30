from pyrogram import Client, filters
from pyrogram.types import Message


def help_text() -> str:
    return (
        "Available commands:\n"
        "/start - Start interaction with the bot\n"
        "/help - Show this help\n"
        "/play <query> - Search and play music; pick a result when multiple are found\n"
        "/playnext <query> - Queue a track to play right after the current one\n"
        "/queue - Show the queue (paginated)\n"
        "/nowplaying - Show the current track\n"
        "/download <query> - Send the audio file into the chat (alias /dl)\n"
        "/lyrics <artist - title> - Show lyrics; no args uses the current track (alias /ly)\n"
        "/fav <query> - Save a track to your favorites\n"
        "/favs - List your favorites with play/remove buttons\n"
        "/unfav <number> - Remove a favorite by its number\n"
        "/join - Join the voice chat\n"
        "/leave - Leave the voice chat\n"
        "/health - Report bot subsystem health (client/db/redis/voice/player)\n"
        "Inline: type @<botname> <song> in any chat to search and send audio\n"
        "Admin commands:\n"
        "/pause - Pause playback\n"
        "/resume - Resume playback\n"
        "/volume <0-200> - Set playback volume\n"
        "/skip - Skip the current track\n"
        "/rm <position> - Remove a track from the queue\n"
        "/move <from> <to> - Move a track in the queue\n"
        "/shuffle - Shuffle the queue\n"
        "/stop - Stop playback and clear the queue\n"
        "/clear - Clear the queue\n"
    )


async def help_handler(client: Client, message: Message) -> None:
    await message.reply_text(help_text())


def register(app: Client) -> None:
    app.add_handler(__import__("pyrogram").handlers.MessageHandler(help_handler, filters=filters.command("help")))
