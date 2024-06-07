"""A multi-eventloop asynchonous python lib for writing HIVE-blockchain bots
and DApp backend code"""
import asyncio
import time
import json
import math
import ssl
import httpx
from average import EWMA

VERSION = "0.2.3"


class JsonRpcError(Exception):
    """Exception for JSON-RPC errors"""
    def __init__(self, message, response):
        super().__init__(message)
        self.response = response

    def __str__(self):
        return str(super()) + " : " + str(self.response)


class NoResponseError(Exception):
    """Exception for when none of the nodes gave a valid response"""


class _RateLimit:
    def __init__(self, period, req_per_period, node):
        self.period = period
        self.req_per_period = req_per_period
        self.node = node
        self.simulated = True
        self.limit = req_per_period
        self.remaining = req_per_period
        self.reset = time.time() + period
        self.creation = time.time()
        self.interval = time.time()
        self.total_count = 0

    def _ll_update(self, limit=None, remaining=None, reset=None):
        if remaining is None:
            self.remaining -= 1
            if self.reset - time.time() <= 0.0:
                self.reset = time.time() + self.period
                self.remaining = self.req_per_period -1
        else:
            self.remaining = remaining
        if reset is not None:
            self.reset = time.time() + reset
        if limit is not None:
            self.limit = limit
        if self.simulated and self.limit is not None and reset is not None:
            self.simulated = False
            self.period = reset
            self.req_per_period = self.remaining + 1
        if not self.simulated and reset is not None and reset is not None and self.period < reset:
            self.period = reset

    def update(self, headers):
        limit = headers.get("RateLimit-Limit", None)
        remaining = headers.get("RateLimit-Remaining",None)
        reset = headers.get("RateLimit-Reset", None)
        if limit is not None and limit.isnumeric():
            limit=int(limit)
        else:
            limit = None
        if remaining is not None and remaining.isnumeric():
            remaining=int(limit)
        else:
            remaining = None
        if reset is not None and reset.isnumeric():
            reset = int(limit)
        else:
            reset = None
        self._ll_update(limit, remaining, reset)
        self.total_count += 1
        if self.total_count % 100 == 0:
            now = time.time()
            speed_all = self.total_count/(now - self.creation)
            speed_now = 100 / (now - self.interval)
            self.interval = time.time()
            self.interval_count = 0
            

    def predicted_sleep(self):
        if self.reset - time.time() <= 0:
            return None
        if self.remaining == 0:
            return self.reset - time.time() + 0.1
        flat = (self.period - (self.reset - time.time()))* (self.limit/self.period)
        spent = self.limit - self.remaining
        ratio = spent/flat
        if ratio <= 1.3 or self.reset - time.time() <= 0.0 or spent <= self.limit/5:
            return None
        return (self.reset - time.time())/(self.remaining -0.5)

    async def sleep(self):
        sleeptime = self.predicted_sleep()
        if sleeptime is not None:
            await asyncio.sleep(sleeptime)


class JsonRpcClient:
    """Baseclass for Json-RPC clients"""
    # pylint: disable=too-many-instance-attributes, too-many-arguments
    def __init__(
            self,
            bot,
            public_api_node,
            public_api_path="",
            public_api_path_2=None,
            layer2="",
            probe_time=3,
            reinit_time=900,
            config={}):
        headers = {
                "user-agent": "aiohivebot-/" + VERSION,
                "Content-Type": "application/json"
            }
        nodeurl = "https://" + public_api_node
        if public_api_path:
            nodeurl += "/" + public_api_paself.nodeth
        self._api_node = public_api_node
        self.config = config
        if "rate" not in config:
            config["rate"] = 60
        if "single_block" not in config:
            config["single_block"] = False
        if "period" not in config:
            config["period"] = 60
        self._bot = bot
        self._layer2 = layer2
        if public_api_path_2 is None:
            self._client = httpx.AsyncClient(base_url=nodeurl, headers=headers)
        else:
            self._client = {}
            for path2 in public_api_path_2:
                self._client[path2] = httpx.AsyncClient(
                        base_url=nodeurl + "/" + path2,
                        headers=headers)
        self._stats = {}
        self._stats["latency"] = EWMA(beta=0.8)
        self._stats["error_rate"] = EWMA(beta=0.8)
        self._stats["requests"] = 0
        self._stats["errors"] = 0
        self._stats["blocks"] = 0
        self._stats["block_range"] = 0
        self._abandon = False
        self._active = False
        self._id = 0
        self._probe_time = probe_time
        self._reinit_time = reinit_time
        self._last_reinit = time.time()
        self._rate_limit = _RateLimit(period=self.config["period"], req_per_period=self.config["rate"], node=public_api_node)

    def predicted_sleep(self):
        psleep = self._rate_limit.predicted_sleep()
        if psleep is None:
            return 0.0
        return psleep

    async def retried_request(self,
                              method,
                              params,
                              api="",
                              retry_pause=0.5,
                              max_retry=-1):
        # pylint: disable=too-many-branches
        """Try to do a request repeatedly on a potentially flaky API node untill it
        succeeds or limits are exceeded"""
        # Use unique id's for JSON-RPC requests, not realy usefull without JSON-RPC
        # batches, but may help with debugging someday.
        self._id += 1
        # Create the JSON-RPC request
        if api:
            if isinstance(self._client, dict):
                client = self._client[api]
                full_method = method
            else:
                client = self._client
                full_method = api + "." + method
        else:
            if isinstance(self._client, dict):
                raise RuntimeError("api must be specified for multi endpoint layer-2")
            client = self._client
            full_method = method
        if full_method not in self._stats:
            self._stats[full_method] = 0
        self._stats[full_method] += 1
        jsonrpc = {
                "jsonrpc": "2.0",
                "method": full_method,
                "params": params,
                "id": self._id
            }
        jsonrpc_json = json.dumps(jsonrpc)
        tries = 0
        # The main retry loop, note we also stop (prematurely) after _abandon is
        # set.
        while (max_retry == -1 and not self._abandon or
                tries < max_retry and not self._abandon):
            tries += 1
            req = None
            rjson = None
            # Measure total request latency: start time
            start_time = time.time()
            try:
                self._stats["requests"] += 1
                await self._rate_limit.sleep()
                req = await client.post("/", content=jsonrpc_json)
            except httpx.HTTPError:
                # HTTP errors are one way for the error_rate to increase, uses
                # decaying average
                self._stats["error_rate"].update(1)
                self._stats["errors"] += 1
            except ssl.SSLError:
                self._stats["error_rate"].update(1)
                self._stats["errors"] += 1
            if req is not None:
                self._rate_limit.update(req.headers)
                # HTTP action has completed, update the latency decaying average.
                self._stats["latency"].update(time.time() - start_time)
                if req.status_code == 200:
                    try:
                        rjson = req.json()
                    except json.decoder.JSONDecodeError as exc:
                        # JSON decode errors on what is expected to be a valid
                        # JSONRPC response is another way for the error_rate to
                        # increase, uses decaying average
                        self._stats["error_rate"].update(1)
                        self._stats["errors"] += 1
                else:
                    # Non 200 OK responses are another way for the error_rate to
                    # increase, uses decaying average
                    self._stats["error_rate"].update(1)
                    self._stats["errors"] += 1
            if rjson is not None:
                if "error" in rjson and "jsonrpc" in rjson:
                    # A JSON-RPC error is likely still a valid response, so doesn't
                    # add to error rate
                    self._stats["error_rate"].update(0)
                    raise JsonRpcError("JsonRPC error", rjson["error"])
                if "result" in rjson and "jsonrpc" in rjson:
                    # A regular valid JSONRPC response, decrease the error rate.
                    # Uses decaying average.
                    self._stats["error_rate"].update(0)
                    # Return only the result.
                    return rjson["result"]
                # JSON but not a valid JSON-RPC response
                self._stats["error_rate"].update(1)
                self._stats["errors"] += 1
            if tries < max_retry and not self._abandon:
                # Back off for a short while before trying again
                await asyncio.sleep(retry_pause)
        raise NoResponseError("No valid JSON-RPC response on query from any node.")

    def reliability_check(self, error_rate_treshold, max_latency):
        """Check if the node has its error rate and latency are within
        reasonable tresholds"""
        if (error_rate_treshold > self._stats["error_rate"].get()
                and max_latency > self._stats["latency"].get()):
            return True
        return False

    def get_quality(self):
        """Get the current quality metrics for the HIVE-API node, that is the current error rate
        and request latency"""
        # Return the current error rate and latency for the node, also return
        # self for easy sorting
        error_rate = self._stats["error_rate"].get()
        if math.isnan(error_rate):
            error_rate = 1
        latency = self._stats["latency"].get()
        if math.isnan(latency):
            latency = 1000000
        else:
            latency += self.predicted_sleep()
        return [error_rate, latency, self]

    async def run(self):
        """The main ever lasting loop for this public API node"""
        await self.initialize_api()
        while not self._abandon:
            if time.time() - self._last_reinit > self._reinit_time:
                interval = time.time() - self._last_reinit
                self._last_reinit = time.time()
                error_rate = self._stats["errors"] / interval
                ok_rate = ((self._stats["requests"] - self._stats["errors"]) / interval)
                block_rate = self._stats["blocks"] / interval
                error_rate = self._stats["error_rate"].get()
                if math.isnan(error_rate):
                    error_rate = 1
                latency = self._stats["latency"].get()
                if math.isnan(latency):
                    latency = 1000000
                await self._bot.internal_node_status(node_uri=self._api_node,
                                                     error_percentage=(100.0 * error_rate),
                                                     latency=1000.0 * latency,
                                                     ok_rate=60 * ok_rate,
                                                     error_rate=60 * error_rate,
                                                     block_rate=60 * block_rate,
                                                     layer2=self._layer2)
                self._active = False
                await self.initialize_api()
                self._active = True
                self._stats["requests"] = 0
                self._stats["errors"] = 0
                self._stats["blocks"] = 0
            await self.heartbeat()
            times = int(self._probe_time)
            while not self._abandon and times > 0:
                times -= 1
                await asyncio.sleep(1)

    async def initialize_api(self):
        """Initialize API, this method must be overridden"""
        print("ERROR: initialize_api not overriden for client""")

    async def heartbeat(self):
        """Heartbeat action, this method should be overridden"""
        print("ERROR: heartbeat not overriden for client""")

    def abort(self):
        """Try to break out of the main loop as quickly as possible so the app
        can end"""
        self._abandon = True
