#!/usr/bin/env python3
import asyncio
from aiohivebot import BaseBot

class MyBot(BaseBot):
    def __init__(self):
        super().__init__()
        self.opnames = set()

    async def vote_operation(self, body):
        if "voter" in body and "author" in body and "permlink" in body:
            print("Checking vote by", body["voter"])
            content = await self.bridge.get_post(author=body["author"], permlink=body["permlink"])
            if "is_paidout" in content and content["is_paidout"]:
                print(" + Vote on expired post detected: @" + body["author"] + "/" + body["permlink"] )

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run(loop))
print("Done")

