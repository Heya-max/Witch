import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.bot.handlers.playback import play_handler
from tests.test_handlers_play_stop import FakeMessage, FakePlayer, FakePlayerManager


async def main():
    fake_player = FakePlayer()
    pm = FakePlayerManager(fake_player)
    client = type("C", (), {"player_manager": pm})
    msg = FakeMessage(chat_id=1, user_id=10, text="/play https://example.com/song.mp3")
    await play_handler(client, msg)
    print("replies:", msg.replies)
    print("enqueued:", fake_player.enqueued)


if __name__ == "__main__":
    asyncio.run(main())
