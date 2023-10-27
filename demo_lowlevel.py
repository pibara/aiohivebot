#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
from aiohivebot import BaseBot

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    async def block(self, block, blockno):
        """Handler for block level data"""
        print("block", blockno, "witness =", block["witness"])

    async def transaction(self, tid, transaction, block):
        """Handler foe block level data"""
        print("- transaction", tid)

    async def operation(self, operation, tid, transaction, block):
        print("  +", operation["type"])

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run(loop))
print("Done")
