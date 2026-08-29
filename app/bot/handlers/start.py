from pyrogram import Client, filters
from pyrogram.types import Message


def start_text() -> str:
    return (
        "👋 Hello! I'm a music bot (prototype).\n\n"
        "Use /play <query> to search and queue music.\n"
        "Use /help to list available commands."
    )


async def start_handler(client: Client, message: Message) -> None:
    await message.reply_text(start_text())


def register(app: Client) -> None:
    app.add_handler(__import__("pyrogram").handlers.MessageHandler(start_handler, filters=filters.command("start")))
