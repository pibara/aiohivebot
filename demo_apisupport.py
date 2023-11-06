#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
from aiohivebot import BaseBot

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    async def node_api_support(self, node_uri, api_support):
        print("NODE:", node_uri)
        sup = {
                key: ("published" if value["published"] else
                    ("hiden" if value["available"]
                        else "disabled")) for key, value in api_support.items()}
        for key, val in sup.items():
            print(" -", key, ":", val)

pncset = MyBot()
#loop = asyncio.get_event_loop()
#loop.run_until_complete(pncset.run())
asyncio.run(pncset.run())
