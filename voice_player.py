import asyncio
import os
from collections import defaultdict, deque
from pathlib import Path

from pyrogram import Client
from pytgcalls import GroupCallFactory

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION = os.getenv("VOICE_ACCOUNT_SESSION", "voice_account")

class VoicePlayer:
    def __init__(self):
        if not API_ID or not API_HASH:
            raise RuntimeError("TELEGRAM_API_ID و TELEGRAM_API_HASH مطلوبان")
        self.client = Client(SESSION, api_id=API_ID, api_hash=API_HASH)
        self.calls = {}
        self.queues = defaultdict(deque)
        self.current = {}

    async def start(self):
        await self.client.start()

    async def join(self, chat_id):
        if chat_id not in self.calls:
            factory = GroupCallFactory(self.client)
            self.calls[chat_id] = factory.get_file_group_call(play_on_repeat=False)
        call = self.calls[chat_id]
        await call.start(chat_id)
        return call

    async def play(self, chat_id, source):
        call = await self.join(chat_id)
        await call.play(source)
        self.current[chat_id] = source

    async def pause(self, chat_id):
        call = self.calls.get(chat_id)
        if call:
            await call.pause_playout()

    async def resume(self, chat_id):
        call = self.calls.get(chat_id)
        if call:
            await call.resume_playout()

    async def stop(self, chat_id):
        call = self.calls.get(chat_id)
        if call:
            await call.stop()
        self.current.pop(chat_id, None)

    async def leave(self, chat_id):
        await self.stop(chat_id)
        self.calls.pop(chat_id, None)

    async def close(self):
        for chat_id in list(self.calls):
            await self.leave(chat_id)
        await self.client.stop()

voice_player = None

def get_voice_player():
    global voice_player
    if voice_player is None:
        voice_player = VoicePlayer()
    return voice_player
