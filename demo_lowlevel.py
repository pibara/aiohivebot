#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
import time
from aiohivebot import BaseBot

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    def __init__(self):
        super().__init__(79600000)
        self.count = 0
        self.start = None

    async def block(self, block, blockno):
        """Handler for block level data"""
        if self.count==0:
            print("Testing speed, may take some time") 
            self.start = time.time()
        self.count += 1
        if self.count % 1000 == 0:
            print(self.count / (time.time() - self.start), "blocks/second")
            self.start = time.time()
            self.count = 0

#    async def transaction(self, tid, transaction, block):
#        """Handler foe block level data"""
#        print("- transaction", tid)
#
#    async def operation(self, operation, tid, transaction, block):
#        print("  +", operation["type"])

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run(loop))
print("Done")
