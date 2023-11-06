import json, typing
import asyncio
from aiohivebot import BaseBot
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

class JsonResponse(Response):
    media_type = "application/json"

    def render(self, content: typing.Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(", ", ": "),
        ).encode("utf-8")

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

bot = MyBot()

async def node_api_support(request):
    return JsonResponse(bot.supportmap)

app = Starlette(debug=True, routes=[
    Route('/', node_api_support)],
    on_startup=[bot.wire_up_on_startup],
    on_shutdown=[bot.wire_up_on_shutdown_async]
)
