"""This sanic example works but isn't perfect because aiohivebot tasks arent stopped"""
import asyncio
from aiohivebot import BaseBot
from sanic import Sanic
from sanic.response import json as json_response

class MyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""
    def __init__(self):
        super().__init__()
        self.supportmap = {}

    async def node_api_support(self, node_uri, api_support):
        if node_uri not in self.supportmap:
            print("Adding node ", node_uri)
        sup = {
                key: ("published" if value["published"] else 
                    ("hiden" if value["available"] 
                        else "disabled")) for key, value in api_support.items()}
        self.supportmap[node_uri] = sup

bot = MyBot()
app = Sanic("HiveNodeMonitor")
bot.wire_up_sanic(app)

@app.get("/node-api-support")
async def node_api_support(request):
    return json_response(bot.supportmap)


