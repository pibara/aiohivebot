#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
import datetime
from aiohivebot import BaseBot

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    def __init__(self):
        data = {"block": 1}
        try: 
            with open("persistent.json", encoding="utf-8") as persistent:
                data = json.load(persistent)
        except FileNotFoundError:
            data = {"block": None}
        super().__init__(start_block=data["block"])

    async def block_processed(self,blockno, timestamp):
        print(blockno, datetime.datetime.utcnow() - timestamp )
        data = {"block": blockno}
        with open("persistent.json", "w", encoding="utf-8") as persistent:
            json.dump(data, persistent, indent=2)

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run(loop))
print("done")
