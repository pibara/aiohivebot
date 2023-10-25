import asyncio
import time
import json
import os
import httpx
from average import EWMA

version = "0.1.0"

class _PubNodeClient:
    def __init__(self, nodeurl, probes, channels):
        global version
        headers = {"user-agent": "aiohivebot-/" + version}
        self._nodeurl = nodeurl
        self._probes = probes
        self.channels = channels
        self._client = httpx.AsyncClient(base_url="https://" + nodeurl, headers = headers)
        self._api_list = []
        self._active = False
        self._abandon = False
        self._latency = EWMA(beta=0.8)
        self._error_rate = EWMA(beta=0.8)
        self._last_reinit = 0
        self._id = 0

    def get_api_quality(self, api):
        if api == "network_broadcast_api":
            api = "condenser_api"
        if api not in self._api_list:
            return [1, 1000000, self]
        else:
            return [self._error_rate.get(), self._latency.get(), self]

    def api_check(self, api, error_rate_treshold, max_latency):
        if api in self._api_list and error_rate_treshold > self._error_rate.get() and max_latency >  self._latency.get():
            return True

    async def retried_request(self, api, method, params={}, retry_pause=0.5, max_retry=-1):
        if api == "condenser_api" and not params:
            params = [] 
        self._id += 1
        jsonrpc = {"jsonrpc": "2.0", "method": api + "." + method,"params": params, "id": self._id}
        jsonrpc_json = json.dumps(jsonrpc)
        tries = 0
        while max_retry == -1 or tries < max_retry:
            tries += 1
            r = None
            rjson = None
            start_time = time.time()
            try:
                r = await self._client.post("/", content=jsonrpc_json)
            except httpx.HTTPError as exc:
                self._error_rate.update(1)
            if r is not None:
                self._latency.update(time.time() - start_time)
                if r.status_code == 200:
                    try:
                        rjson = r.json()
                    except json.decoder.JSONDecodeError as exp:
                        self._error_rate.update(1)
                else:
                    self._error_rate.update(1)
            if rjson is not None:
                if "error" in rjson:
                    self._error_rate.update(0)
                    return None
                elif "jsonrpc" not in rjson or "result" not in rjson:
                    self._error_rate.update(1)
                else:
                    self._error_rate.update(0)
                    return rjson["result"]
            if tries < max_retry:
                await asyncio.sleep(retry_pause)
        return None

    async def _initialize_api(self):
        self._active = False
        methods = await self.retried_request(api="jsonrpc",
                                              method="get_methods",
                                              retry_pause=60,
                                              max_retry=5)
        if methods is None:
            methods = []
        found_endpoints = set()
        for method in  methods:
            namespace, met = method.split(".")
            found_endpoints.add(namespace)
        for namespace, testmethod in self._probes.items():
            if namespace not in found_endpoints:
                result = await self.retried_request(api=namespace,
                                                     method=testmethod["method"],
                                                     params=testmethod["params"],
                                                     max_retry=5)
                if result is not None:
                    found_endpoints.add(namespace)
        self._api_list = sorted(list(found_endpoints))
        self._active = True

    async def get_block(self, blockno):
        return await self.retried_request(api="block_api",
                                           method="get_block",
                                           params={"block_num": blockno})

    async def run(self):
        while not self._abandon:
            if time.time() - self._last_reinit > 3600:
                await self._initialize_api()
                self._last_reinit = time.time()
            if "condenser_api" in self._api_list:
                dynprob = await self.retried_request(api="condenser_api",
                                                      method="get_dynamic_global_properties")
                if dynprob and "head_block_number" in dynprob:
                    headblock = dynprob["head_block_number"]
                    await self.channels(headblock, self)
            await asyncio.sleep(3)

class _Method:
    def __init__(self, bot, api, method):
        self.bot = bot
        self.api = api
        self.method = method

    async def __call__(self, *args, **kwargs):
        if self.api == "condenser_api":
            return await self.bot.api_call(self.api, self.method, args)
        return await self.bot.api_call(self.api, self.method, kwargs)

class _SubAPI:
    def __init__(self, bot, api):
        self.bot = bot
        self.api = api

    def __getattr__(self, method):
        return _Method(self.bot, self.api, method)


class BaseBot:
    def __init__(self):
        self._block = None
        self._clients = []
        probepath = os.path.join(os.path.dirname(os.path.abspath(__file__)),"probe.json")
        with open(probepath) as jsonfile:
            probes = json.load(jsonfile)
            self.api_list = list(probes.keys())
        self._abandon = False
        for public_api_node in [
                "api.hive.blog",
                "api.deathwing.me",
                "hive-api.arcange.eu",
                "hived.emre.sh",
                "api.openhive.network",
                "rpc.ausbit.dev",
                "rpc.mahdiyari.info",
                "hive-api.3speak.tv",
                "anyx.io",
                "techcoderx.com",
                "api.hive.blue",
                "hived.privex.io",
                "hive.roelandp.nl"]:
            self._clients.append(_PubNodeClient(public_api_node, probes, self._potential_block))
    
    async def _potential_block(self, block, nodeclient):
        if self._block is None:
            self._block = block - 1
        if block > self._block and nodeclient.api_check("block_api", 0.05, 0.5):
            for blockno in range(self._block + 1, block +1):
                if blockno - self._block == 1:
                    wholeblock = await nodeclient.get_block(blockno)
                    if blockno - self._block == 1 and "block" in wholeblock:
                        self._block += 1
                        await self._process_block(blockno, wholeblock["block"])
            self._block = block

    async def run(self, loop):
        tasks = []
        for nodeclient in self._clients:
            tasks.append(loop.create_task(nodeclient.run()))
        await asyncio.gather(*tasks)

    async def _process_block(self, blockno, block):
        transactions = block.pop("transactions")
        transaction_ids = block.pop("transaction_ids")
        if hasattr(self, "block"):
            await self.block(block)
        for index in range(0, len(transactions)):
            operations = transactions[index].pop("operations")
            if hasattr(self, "transaction"):
                await self.transaction(transaction_ids[index], transactions[index], block)
            for operation in operations:
                if hasattr(self, "operation"):
                    await self.operation(operation, transaction_ids[index], transactions[index], block)
                if "type" in operation and "value" in operation and hasattr(self, operation["type"]):
                    await getattr(self, operation["type"])(operation["value"])

    def __getattr__(self, attr):
        if attr in self.api_list or attr=="network_broadcast_api":
            return _SubAPI(self, attr)
        raise AttributeError("BaseBot has no sub-API %s" % attr)

    async def api_call(self, api, method, params):
        unsorted = []
        for client in self._clients:
            unsorted.append(client.get_api_quality(api))
        slist = sorted(unsorted, key = lambda x: (x[0], x[1]))
        for _ in range(0,4):
            for entry in slist:
                rval = await entry[2].retried_request(api=api,
                                                      method=method,
                                                      params=params,
                                                      max_retry=2,
                                                      retry_pause=0.2)
                if rval is not None:
                    return rval
        raise RuntimeError("No node can awnser " + api + "." + method)



