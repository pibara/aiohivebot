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

    async def vote_operation(self, body):
        """Handler for cote_operation type operations in the HIVE block stream"""
        if "voter" in body and "author" in body and "permlink" in body:
            result = await self.bridge.get_post(author=body["author"], permlink=body["permlink"])
            content = result.result()
            if content and "is_paidout" in content and content["is_paidout"]:
                pass
                #print("Vote by", body["voter"], "on expired post detected: @" + \
                #        body["author"] + "/" + body["permlink"] )
            if self.count == 1000000:
                self.abort()
            self.count += 1

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run(loop))
print("Done")
