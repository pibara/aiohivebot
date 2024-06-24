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
        jan2024=81520247
        super().__init__(jan2024, use_virtual=False, maintain_order=False)
        self.count=0
        self.start_time = time.time()
        self.total_cought_up = datetime.timedelta(seconds=0)

    async def monitor_block_rate(self, rate, behind_head, behind_irreversable):
        self.count += 1
        behind = datetime.timedelta(seconds=behind_irreversable*3)
        if rate * 3 -1 < 0:
            negative_cought_up = datetime.timedelta(seconds=(1 - rate*3)*60)
            print("block rate =", rate,"blocks/second, behind =", behind, "fell behind =", negative_cought_up)
            self.total_cought_up -= negative_cought_up
        else:
            cought_up = datetime.timedelta(seconds=(rate*3-1)*60)
            print("block rate =", rate,"blocks/second, behind =", behind, "cought up =", cought_up)
            self.total_cought_up += cought_up
        if self.count == 60:
            print("Cought up", self.total_cought_up, "in", datetime.timedelta(seconds=time.time() - self.start_time))
            self.abort()


pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run())
print("Done")
