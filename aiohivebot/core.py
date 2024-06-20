"""A multi-eventloop asynchonous python lib for writing HIVE-blockchain bots
and DApp backend code"""
import asyncio
import time
import json
import math
import ssl
import httpx
from average import EWMA

VERSION = "0.2.9"


class JsonRpcError(Exception):
    """Exception for JSON-RPC errors"""
    def __init__(self, message, response):
        super().__init__(message)
        self.response = response

    def __str__(self):
        return str(super()) + " : " + str(self.response)


class NoResponseError(Exception):
    """Exception for when none of the nodes gave a valid response"""
    def __init__(self, message, exceptions=None, node=None, parents=None):
        super().__init__(message)
        self.exceptions = {}
        if node is not None:
            self.exceptions[node] = exceptions if exceptions is not None else []
        if parents is not None:
            for parent in parents:
                for key, val in parent.items():
                    if key not in self.exceptions:
                        self.exceptions[key] = []
                    for item in val:
                        self.exceptions[key].append(item)

    def dump(self):
        """Dump"""
        print(self.exceptions)


class _RateLimitingPolicy:
    def __init__(self, policy, starttime):
        self.time_window = policy["w"]
        self.quota = policy["v"]
        self.remaining = self.quota
        self.reset = starttime + self.time_window

    def update(self, now):
        """Update state for pollicy"""
        self.remaining -= 1
        self.remaining = max(self.remaining, 0)
        if now >= self.reset:
            self.reset = now + self.time_window
            self.remaining = self.quota -1

    def rate(self, now):
        """Get average rarfore aining window"""
        if self.reset <= now:
            return self.quota / self.time_window
        if self.remaining == 0:
            return None
        return self.remaining / (self.reset - now)

    def get(self, now):
        """Get params of single policy state"""
        if self.reset <= now:
            return self.quota / self.time_window, self.quota, self.quota, now + self.time_window, self.time_window
        if self.remaining == 0:
            return None, self.quota, self.remaining, self.reset, self.time_window
        return self.remaining / (self.reset - now), self.quota, self.remaining, self.reset, self.time_window


class _RateLimitingPolicies:
    """A simple class that simulates a HTTP server with rate limiting policies."""
    def __init__(self, policies):
        self.policies = []
        now = time.time()
        for policy in policies:
            self.policies.append(_RateLimitingPolicy(policy, now))

    def update(self):
        """Update all rate limit state for all policies of this node"""
        now = time.time()
        for policy in self.policies:
            policy.update(now)

    def get(self):
        """Get the the values for the currently relevant rate limit policy"""
        now = time.time()
        best_policy = None
        best_limit = None
        best_remaining = None
        best_reset = None
        best_time_window = None
        for policy in self.policies:
            # The first one id always the best of one
            if best_policy is None:
                best_policy = policy
                best_rate, best_limit, best_remaining, best_reset, best_time_window = policy.get(now)
            else:
                rate, _, remaining, reset, _ = policy.get(now)
                # If one policy is exausted, we must return the best exausted policy no matter what
                if best_rate is None:
                    if rate is None:
                        # If both candidates are exausted, the one with the furthest reset is the one we need
                        if best_reset < reset:
                            best_policy = policy
                            best_rate, best_limit, best_remaining, best_reset, best_time_window = policy.get(now)
                else:
                    # If the old best isn't exausted and the new candidate is, choos the exausted policy
                    if rate is None:
                        best_policy = policy
                        best_rate, best_limit, best_remaining, best_reset, best_time_window = policy.get(now)
                    else:
                        # If neither policy is exausted, pick the one with the lowest remaining value
                        if best_remaining > remaining:
                            best_policy = policy
                            best_rate, best_limit, best_remaining, best_reset, best_time_window = policy.get(now)
                        # if both remaining values are the same, pick the one with the furthest window end.
                        elif best_remaining == remaining and best_reset < reset:
                            best_policy = policy
                            best_rate, best_limit, best_remaining, best_reset, best_time_window = policy.get(now)
        return best_limit, best_remaining, best_reset, best_time_window

class _RateLimitClient:
    def __init__(self, policies, node):
        self.simulator = _RateLimitingPolicies(policies)
        self.node = node
        self.limit, self.remaining, self.reset, self.time_window = self.simulator.get()
        self.status = {}
        self.clear_status()

    def clear_status(self):
        """Clear rate limit status"""
        self.status["count"] = 0
        self.status["uses_ratelimit_header"] = False
        self.status["used_429"] = False
        self.status["used_retry_header"] = False
        self.status["total_ratelimit_sleeptime"] = 0.0

    def get_projected_ratelimit_window_latency(self):
        """Get the mean latency under max load focurre t rate limmit window"""
        remaining_window = self.reset - time.time()
        if remaining_window <= 0:
            return self.time_window/self.limit
        if self.remaining == 0:
            return remaining_window + self.time_window/self.limit
        return remaining_window / self.remaining

    def update(self, headers, code):
        """Update rate limmiting vars based on response and simulated policies"""
        self.simulator.update()
        self.status["count"] += 1
        self.limit, self.remaining, self.reset, self.time_window = self.simulator.get()
        unparsed = headers.get("RateLimit", None)
        retry = headers.get("Retry-After", None)
        if unparsed is not None:
            self.status["uses_ratelimit_header"] = True
            for part in unparsed.split(","):
                part = part.lstrip()
                subparts = part.split("=")
                if len(subparts) == 2 and subparts[1].isnumeric() and subparts[0] in ("limit", "remaining", "reset"):
                    if subparts[0] == "limit":
                        self.limit = int(subparts[1])
                    if subparts[0] == "remaining":
                        self.remaining = int(subparts[1])
                    if subparts[0] == "reset":
                        self.reset = int(subparts[1])
        elif code == 429:
            self.status["used_429"] = True
            self.remaining = 0
            if retry is not None and retry.isnumeric():
                self.status["used_retry_header"] = True
                self.reset = time.time() + int(retry)
                print("RETRY HEADER:", retry, self.node)
        if self.remaining == 0 and self.reset - time.time() > 900:
            self.reset = time.time() + 900

    def predicted_sleep(self):
        """Get the predicted duration for the next rate limmiting sleep"""
        if self.reset - time.time() <= 0:
            return None
        if self.remaining == 0:
            return self.reset - time.time() + 0.1
        flat = (self.time_window - (self.reset - time.time()))* (self.limit/self.time_window)
        spent = self.limit - self.remaining
        ratio = spent/flat
        if ratio <= 1.3 or self.reset - time.time() <= 0.0 or spent <= self.limit/5:
            return None
        return (self.reset - time.time())/(self.remaining -0.5)

    async def sleep(self):
        """Sleep if needed for rate limiting"""
        sleeptime = self.predicted_sleep()
        if sleeptime is not None:
            self.status["total_ratelimit_sleeptime"] += sleeptime
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
            reinit_time=900,  # 15 minutes
            config=None):
        headers = {
                "user-agent": "aiohivebot-/" + VERSION,
                "Content-Type": "application/json"
            }
        nodeurl = "https://" + public_api_node
        if public_api_path:
            nodeurl += "/" + public_api_path
        self._api_node = public_api_node
        self.config = config if config is not None else {}
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
        self._stats["detailed_error_stats"] = {}
        self._abandon = False
        self._active = False
        self._id = 0
        self._probe_time = probe_time
        self._reinit_time = reinit_time
        self._last_reinit = time.time()
        self._rate_limit = _RateLimitClient(policies=self.config["policies"], node=public_api_node)

    def predicted_sleep(self):
        """Get the predicted sleep that ratelimiting would imply for next get"""
        psleep = self._rate_limit.predicted_sleep()
        if psleep is None:
            return 0.0
        return psleep

    def get_projected_ratelimit_window_latency(self):
        """Get the projected latency if the current rate limit window is to get exausted"""
        return self._rate_limit.get_projected_ratelimit_window_latency()

    def get_node(self):
        """Get api node host name"""
        return self._api_node

    # pylint: disable=too-many-statements,too-many-locals,too-many-branches
    async def retried_request(self,
                              method,
                              params,
                              api="",
                              retry_pause=0.5,
                              max_retry=-1):
        """Try to do a request repeatedly on a potentially flaky API node untill it
        succeeds or limits are exceeded"""
        exceptions = []
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
        if full_method not in self._stats["detailed_error_stats"]:
            self._stats["detailed_error_stats"][full_method] = {}
            self._stats["detailed_error_stats"][full_method]["http-error"] = 0
            self._stats["detailed_error_stats"][full_method]["ssl-error"] = 0
            self._stats["detailed_error_stats"][full_method]["code"] = {}
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
            except httpx.HTTPError as exp:
                exceptions.append(exp)
                # HTTP errors are one way for the error_rate to increase, uses
                # decaying average
                self._stats["error_rate"].update(1)
                self._stats["errors"] += 1
                self._stats["detailed_error_stats"][full_method]["http-error"] += 1
            except ssl.SSLError as exp:
                exceptions.append(exp)
                self._stats["error_rate"].update(1)
                self._stats["errors"] += 1
                self._stats["detailed_error_stats"][full_method]["ssl-error"] += 1
            if req is not None:
                if full_method not in self._stats["detailed_error_stats"]:
                    self._stats["detailed_error_stats"][full_method] = {}
                    self._stats["detailed_error_stats"][full_method]["http-error"] = 0
                    self._stats["detailed_error_stats"][full_method]["ssl-error"] = 0
                    self._stats["detailed_error_stats"][full_method]["code"] = {}
                if req.status_code not in self._stats["detailed_error_stats"][full_method]["code"]:
                    self._stats["detailed_error_stats"][full_method]["code"][req.status_code] = 0
                self._stats["detailed_error_stats"][full_method]["code"][req.status_code] += 1
                self._rate_limit.update(req.headers, code=req.status_code)
                # HTTP action has completed, update the latency decaying average.
                self._stats["latency"].update(time.time() - start_time)
                if req.status_code == 200:
                    try:
                        rjson = req.json()
                    except json.decoder.JSONDecodeError as exc:
                        exceptions.append(exc)
                        # JSON decode errors on what is expected to be a valid
                        # JSONRPC response is another way for the error_rate to
                        # increase, uses decaying average
                        self._stats["error_rate"].update(1)
                        self._stats["errors"] += 1
                else:
                    # Non 200 OK responses are another way for the error_rate to
                    # increase, uses decaying average
                    exceptions.append([req.status_code, req.text])
                    self._stats["error_rate"].update(1)
                    self._stats["errors"] += 1
                    #print("##############################")
                    #print("#    ERROR", req.status_code, "              #")
                    #print("##############################")
                    #print(req.text)
                    #print("##############################")
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
        raise NoResponseError("No valid JSON-RPC response on query from any node.", exceptions=exceptions, node=self._api_node)

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
                ok_rate = (self._stats["requests"] - self._stats["errors"]) / interval
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
                                                     rate_limit_status = self._rate_limit.status.copy(),
                                                     detailed_error_status = self._stats["detailed_error_stats"].copy(),
                                                     layer2=self._layer2)
                self._active = False
                await self.initialize_api()
                self._active = True
                self._stats["requests"] = 0
                self._stats["errors"] = 0
                self._stats["blocks"] = 0
                self._stats["detailed_error_stats"] = {}
                self._rate_limit.clear_status()
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
