#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
from aiohivebot import BaseBot, NoResponseError, JsonRpcError

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    def __init__(self):
        super().__init__()
        self.count = 0

    async def node_status(self, node_uri, error_percentage, latency, ok_rate, error_rate, block_rate):
        print("STATUS:", node_uri, "error percentage =", int(100*error_percentage)/100,
                "latency= ", int(100*latency)/100,
                "ok=", int(100*ok_rate)/100, 
                "req/min, errors=", int(100*error_rate)/100,
                "req/min, blocks=", int(100*block_rate)/100,
                "blocks/min" )

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run())
print("Done")
