#!/usr/bin/env python3
import asyncio
from aiohivebot import BaseBot
from aiohttp import web

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    def __init__(self):
        super().__init__()
        self.supportmap = {}

    async def node_api_support(self, node_uri, api_support):
        sup = {
                key: ("published" if value["published"] else 
                    ("hiden" if value["available"] 
                        else "disabled")) for key, value in api_support.items()}
        self.supportmap[node_uri] = sup

async def node_api_support(request):
    return web.json_response(bot.supportmap)

bot = MyBot()

app = web.Application()
app.add_routes([web.get('/', node_api_support)])
bot.wire_up_aiohttp(app)
web.run_app(app)
