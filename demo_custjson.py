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

    async def engine_market_buy(self, required_auths, required_posting_auths, body, blockno):
        """Hive Engine custom json action for market buy"""
        if body["symbol"] == "SWAP.BTC":
            info = await self.l2.engine.contracts.market.metrics.findOne(symbol="SWAP.BTC")
            relbid = int(10000*(float(info['lowestAsk']) - float(body["price"])) /
                    float(info['lowestAsk']))/100
            print("BUY", (required_posting_auths + required_auths)[0],
                    body["quantity"], "at", body["price"], relbid, "% below lowest ask")
    
    async def engine_market_sell(self, required_auths, required_posting_auths, body):
        """Hive Engine custom json action for market sell"""
        if body["symbol"] == "SWAP.BTC":
            info = await self.l2.engine.contracts.market.metrics.findOne(symbol="SWAP.BTC")
            relask = int(10000*(float(body["price"]) - float(info["highestBid"])) / 
                    float(info["highestBid"]))/100
            print("SELL", (required_posting_auths + required_auths)[0],
                    body["quantity"], "at", body["price"], relask, "% above highest bid")

    async def engine_l2_market_buy(self, sender, body):
        """Hive Engine l2 feed custom json action for market buy"""
        if body["symbol"] == "SWAP.BTC":
            info = await self.l2.engine.contracts.market.metrics.findOne(symbol="SWAP.BTC")
            relbid = int(10000*(float(info['lowestAsk']) - float(body["price"])) /
                    float(info['lowestAsk']))/100
            print("BUY L2", sender, body["quantity"], "at", body["price"], relbid,
                    "% below lowest ask")


    async def engine_l2_market_sell(self, sender, body):
        """Hive Engine l2 feed custom json action for market sell"""
        if body["symbol"] == "SWAP.BTC":
            info = await self.l2.engine.contracts.market.metrics.findOne(symbol="SWAP.BTC")
            relask = int(10000*(float(body["price"]) - float(info["highestBid"])) /
                    float(info["highestBid"]))/100
            print("SELL L2", sender, body["quantity"], "at", body["price"], relask,
                    "% above highest bid")

    async def engine_node_status(self, node_uri, error_percentage, latency, ok_rate, error_rate, block_rate):
        print("STATUS:", node_uri, "error percentage =", int(100*error_percentage)/100,
                "latency= ", int(100*latency)/100,
                "ok=", int(100*ok_rate)/100,
                "req/min, errors=", int(100*error_rate)/100,
                "req/min, blocks=", int(100*block_rate)/100,
                "blocks/min" )

pncset = MyBot()
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run())
