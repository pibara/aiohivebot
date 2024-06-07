#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
from aiohivebot import BaseBot

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    def __init__(self):
        super().__init__()
        self.seen = set()

    async def block(self, block, blockno):
        print(block["timestamp"])

    async def vote_operation(self, body):
        if body["weight"] < 0:
            print(body)

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run())
print("Done")
