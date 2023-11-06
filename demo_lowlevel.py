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
        super().__init__(79600000)
        self.count = 0
        self.tcount = 0
        self.ocount = 0
        self.start = None

    async def block(self, block, blockno, timestamp):
        """Handler for block level data"""
        if self.count==0:
            self.start = time.time()
        self.count += 1
        if self.count % 1000 == 0:
            seconds_per_block = 3
            bspeed = int(100 * self.count / (time.time() - self.start))/100
            hourdelta =datetime.timedelta(seconds=bspeed * 3600 / seconds_per_block)
            tspeed = int(100 * self.tcount / (time.time() - self.start))/100
            ospeed = int(100*self.tcount / (time.time() - self.start))/100
            print(timestamp, str(hourdelta), "per hour", bspeed, "blocks/second",tspeed, "transactions/second", ospeed, "operations/second" )
            self.start = time.time()
            self.count = 0
            self.tcount = 0
            self.o_count = 0

    async def transaction(self):
        """Handler foe block level data"""
        self.tcount += 1

    async def operation(self):
        """Handlert for operation level stuff"""
        self.ocount += 1

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run())
print("Done")
