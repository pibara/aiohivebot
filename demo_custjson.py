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

    async def engine_market_buy(self, required_auths, required_posting_auths, body):
        """Hive Engine custom json action for market buy"""
        print("Hive-engine market buy", body, required_posting_auths + required_auths)
    
    async def engine_market_sell(self, required_auths, required_posting_auths, body):
        """Hive Engine custom json action for market sell"""
        print("Hive-engine market sell", body, required_posting_auths + required_auths)

    async def engine_market_cancel(self, required_auths, required_posting_auths, body):
        """Hive Engine custom json action for market cancel"""
        print("Hive-engine market cancel", body, required_posting_auths + required_auths)

    async def sm_sell_card(self, required_auths, required_posting_auths, body):
        print("sm_sell_card", body,  required_posting_auths + required_auths)

    async def pp_podcast_update(self,required_auths, required_posting_auths, body):
        if "iris" in body:
            print("pp_podcast_update", body["iris"],  required_posting_auths + required_auths)

    async def notify_setLastRead(self, required_auths, required_posting_auths, body):
        print("notify setLastRead", body,  required_posting_auths + required_auths)

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run(loop))
