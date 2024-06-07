#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
import time
import datetime
from aiohivebot import BaseBot

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    def __init__(self):
        jan2024=82316952
        super().__init__(jan2024, use_virtual=True, maintain_order=False, ratelimit_req_per_period=150)
        self.seen = set()
        self.tfields = set()

    async def interest_operation(self, body, timestamp):
        if body["interest"]["nai"] == "@@000000013":
            print(body["owner"] + ";" + body["interest"]["amount"] + ";" + timestamp.isoformat())

    async def monitor_block_rate(self, rate):
        print("block rate =", rate,"blocks/second")

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run())
print("Done")
