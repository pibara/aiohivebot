#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
from aiohivebot import BaseBot

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    def __init__(self):
        super().__init__()
        self.count = 0

    def node_api_support(self, node_uri, api_support):
        print("NODE:", node_uri)
        for key, val in api_support.items():
            if not val["published"]:
                pass
                print(" -", key, ":", val)

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run(loop))
print("Done")
