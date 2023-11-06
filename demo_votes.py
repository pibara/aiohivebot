#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
from aiohivebot import BaseBot

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""

    async def vote_operation(self, body):
        """Handler for cote_operation type operations in the HIVE block stream"""
        if "voter" in body and "author" in body and "permlink" in body:
            content = await self.bridge.get_post(author=body["author"], permlink=body["permlink"])
            if content and "is_paidout" in content and content["is_paidout"]:
                print("Vote by", body["voter"], "on expired post detected: @" + \
                        body["author"] + "/" + body["permlink"] )
            else:
                print("Vote by", body["voter"], "on active post")

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run())
print("Done")
