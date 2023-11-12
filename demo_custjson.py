#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
from aiohivebot import BaseBot

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    def __init__(self):
        super().__init__(enable_layer2=["engine"])
        self.count = 0
        self.old_bid = 0.0
        self.new_bid = 0.0
        self.old_ask = float('inf')
        self.new_ask = float('inf')

    async def engine_l2_block(self):
        self.old_ask = self.new_ask
        self.old_bid = self.new_bid
        info = await self.l2.engine.contracts.market.metrics.findOne(symbol="SWAP.BTC")
        self.new_ask = float(info['lowestAsk'])
        self.new_bid = float(info["highestBid"])

    async def engine_market_buy(self, required_auths, required_posting_auths, body, blockno, timestamp):
        """Hive Engine custom json action for market buy"""
        if body["symbol"] == "SWAP.BTC" and self.old_ask > 0.0:
            bid = float(body["price"])
            quantity = float(body["quantity"])
            relbid1 = int(10000*(self.old_ask - bid) / self.old_ask)/100
            relbid2 = int(10000*(self.new_ask - bid) / self.new_ask)/100
            if relbid2 >= 0.0:
                print(timestamp, "BUY", (required_posting_auths + required_auths)[0],
                        quantity, "at", bid, relbid2, "(", relbid1, ") % below lowest ask")
            else:
                print(timestamp, "BUY", (required_posting_auths + required_auths)[0],
                        quantity, "at", bid, -relbid2, "(", -relbid1, ") % ABOVE lowest ask")
    
    async def engine_market_sell(self, required_auths, required_posting_auths, body, timestamp):
        """Hive Engine custom json action for market sell"""
        if body["symbol"] == "SWAP.BTC" and self.old_bid >0.0:
            ask = float(body["price"])
            quantity = float(body["quantity"])
            relask1 = int(10000*(self.old_bid - ask) / self.old_bid)/100
            relask2 = int(10000*(self.new_bid - ask) / self.new_bid)/100
            if relask2 >= 0.0:
                print(timestamp, "SELL", (required_posting_auths + required_auths)[0],
                        quantity, "at", ask, relask2,"(", relask1, ") % above highest bid")
            else:
                print(timestamp, "SELL", (required_posting_auths + required_auths)[0],
                        quantity, "at", ask, -relask2,"(", -relask1, ") % BELOW highest bid")

    async def engine_l2_node_status(
            self,
            node_uri,
            error_percentage,
            latency,
            ok_rate,
            error_rate,
            block_rate):
        print(
                "STATUS:",node_uri, 
                "error percentage =", int(100*error_percentage)/100,
                "latency= ", int(100*latency)/100,
                "ok=", int(100*ok_rate)/100,
                "req/min, errors=", int(100*error_rate)/100,
                "req/min, blocks=", int(100*block_rate)/100,
                "blocks/min" )

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run())
