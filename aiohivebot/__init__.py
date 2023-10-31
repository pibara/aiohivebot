"""A multi-eventloop asynchonous python lib for writing HIVE-blockchain bots
and DApp backend code"""
import asyncio
import time
import json
import os
import datetime
import inspect
from dateutil import parser
import httpx
from average import EWMA

VERSION = "0.1.3"


class FunctionNull:
    """Do nothing functor"""
    # pylint: disable=too-few-public-methods
    async def __call__(self, *args, **kwargs):
        pass


class FunctionForwarder:
    """Forwarder functor that removes all kwargs not part of the  function fingerprint"""
    # pylint: disable=too-few-public-methods
    def __init__(self, method, eat_exceptions, bot):
        self.method = method
        self.args = set(inspect.signature(method).parameters.keys())
        if eat_exceptions:
            self._bot = bot
        else:
            self._bot = None

    async def __call__(self, *args, **kwargs):
        # droplist because we dont delete inside of key itteration
        droplist = []
        # Find out what to drop
        for key in kwargs:
            if key not in self.args:
                droplist.append(key)
        # drop
        for key in droplist:
            kwargs.pop(key)
        # Check if all user expected arguments are there
        unknown = self.args - set(kwargs.keys())
        if self._bot is None:
            if unknown:
                raise ValueError("Non standard named arguments for method:" + str(list(unknown)))
            await self.method(**kwargs)
        else:
            try:
                if unknown:
                    raise ValueError(
                            "Non standard named arguments for method:" + str(list(unknown)))
                await self.method(**kwargs)
            except Exception as exp:  # pylint: disable=broad-except
                await self._bot.exception(exception=exp)


class ObjectForwarder:
    """Helper class for calling possibly defined user defined methods"""
    # pylint: disable=too-few-public-methods
    def __init__(self, bot, eat_exceptions=False):
        self._methods = {}
        self._null = FunctionNull()
        for method in dir(bot):
            if (method[0] != "_"
                    and not method.startswith("internal_")
                    and method not in ['abort', 'run']):
                self._methods[method] = FunctionForwarder(
                        getattr(bot, method),
                        eat_exceptions,
                        self)

    def __getattr__(self, methodname):
        return self._methods.get(methodname, self._null)


class JsonRpcError(Exception):
    """Exception for JSON-RPC errors"""
    def __init__(self, message, response):
        super().__init__(message)
        self.response = response

    def __str__(self):
        return str(super()) + " : " + str(self.response)


class NoResponseError(Exception):
    """Exception for when none of the nodes gave a valid response"""


class _PubNodeClient:
    """Client that keeps up with a single public HIVE-API node"""
    # pylint: disable=too-many-instance-attributes
    def __init__(self, nodeurl, probes, bot):
        headers = {"user-agent": "aiohivebot-/" + VERSION}
        self._nodeurl = nodeurl
        self._probes = probes
        self.bot = bot
        self._client = httpx.AsyncClient(base_url="https://" + nodeurl, headers=headers)
        self._api_list = []
        self._active = False
        self._abandon = False
        self._latency = EWMA(beta=0.8)
        self._error_rate = EWMA(beta=0.8)
        self._requests = 0
        self._errors = 0
        self._blocks = 0
        self._last_reinit = 0
        self._id = 0

    def get_api_quality(self, api):
        """Get the current quality metrics for the HIVE-API node, that is the current error rate
        and request latency"""
        # There is currently no scan for the network_broadcast_api, for now we assume that anything
        # that has the condenser_api also supports network_broadcast_api, we need to fix this.
        if api == "network_broadcast_api":
            api = "condenser_api"
        # If an API isn't supported, return unacceptable error rate and latency.
        if api not in self._api_list:
            return [1, 1000000, self]
        # Otherwise return the current error rate and latency for the node, also return
        # self for easy sorting
        return [self._error_rate.get(), self._latency.get(), self]

    def api_check(self, api, error_rate_treshold, max_latency):
        """Check if the node has the given API and if its error rate and latency are within
        reasonable tresholds"""
        if (api in self._api_list and error_rate_treshold > self._error_rate.get()
                and max_latency > self._latency.get()):
            return True
        return False

    # pylint: disable=too-many-arguments
    async def retried_request(self, api, method, params=None, retry_pause=0.5, max_retry=-1):
        """Try to do a request repeatedly on a potentially flaky API node untill it
        succeeds or limits are exceeded"""
        # Most APIs use dicts as params, the condenser API uses a list, so replace the
        # default empty dict with an empty list
        if api == "condenser_api" and not params:
            params = []
        elif not params:
            params = {}
        # Use unique id's for JSON-RPC requests, not realy usefull without JSON-RPC batches, but
        # may help with debugging someday.
        self._id += 1
        # Create the JSON-RPC request
        jsonrpc = {"jsonrpc": "2.0", "method": api + "." + method, "params": params, "id": self._id}
        jsonrpc_json = json.dumps(jsonrpc)
        tries = 0
        # The main retry loop, note we also stop (prematurely) after _abandon is set.
        while max_retry == -1 and not self._abandon or tries < max_retry and not self._abandon:
            tries += 1
            req = None
            rjson = None
            # Measure total request latency: start time
            start_time = time.time()
            try:
                self._requests += 1
                req = await self._client.post("/", content=jsonrpc_json)
            except httpx.HTTPError:
                # HTTP errors are one way for the error_rate to increase, uses decaying average
                self._error_rate.update(1)
                self._errors += 1
            if req is not None:
                # HTTP action has completed, update the latency decaying average.
                self._latency.update(time.time() - start_time)
                if req.status_code == 200:
                    try:
                        rjson = req.json()
                    except json.decoder.JSONDecodeError:
                        # JSON decode errors on what is expected to be a valid JSONRPC response
                        #  is another way for the error_rate to increase, uses decaying average
                        self._error_rate.update(1)
                        self._errors += 1
                else:
                    # Non 200 OK responses are another way for the error_rate to increase, uses
                    #  decaying average
                    self._error_rate.update(1)
                    self._errors += 1
            if rjson is not None:
                if "error" in rjson and "jsonrpc" in rjson:
                    # A JSON-RPC error is likely still a valid response, so doesn't
                    # add to error rate
                    self._error_rate.update(0)
                    raise JsonRpcError("JsonRPC error", rjson["error"])
                if "result" in rjson and "jsonrpc" in rjson:
                    # A regular valid JSONRPC response, decrease the error rate.
                    # Uses decaying average.
                    self._error_rate.update(0)
                    # Return only the result.
                    return rjson["result"]
                # JSON but not a valid JSON-RPC response
                self._error_rate.update(1)
                self._errors += 1
            if tries < max_retry and not self._abandon:
                # Back off for a short while before trying again
                await asyncio.sleep(retry_pause)
        raise NoResponseError("No valid JSON-RPC response on query from any node.")

    async def _initialize_api(self):
        """(Re)-initialize the API for this node by figuring out what API subsets
        this node supports"""
        if self._active:
            interval = time.time() - self._last_reinit
            error_rate = self._errors / interval
            ok_rate = (self._requests - self._errors) / interval
            block_rate = self._blocks / interval
            await self.bot.internal_node_status(node_uri=self._nodeurl,
                                                error_percentage=100.0 * self._error_rate.get(),
                                                latency=1000.0 * self._latency.get(),
                                                ok_rate=60 * ok_rate,
                                                error_rate=60 * error_rate,
                                                block_rate=60 * block_rate)
        # Let the BaseBot know that we aren't active right now for a little bit.
        self._active = False
        # Get the list or methods that are explicitly advertised
        try:
            methods = await self.retried_request(api="jsonrpc",
                                                 method="get_methods",
                                                 retry_pause=30,
                                                 max_retry=10)
        except (JsonRpcError, NoResponseError):
            methods = []
        if methods is None:
            methods = []
        # Extract the sub-API namespaces from the advertised methods list.
        found_endpoints = set()
        for method in methods:
            if "." in method:
                namespace, _ = method.split(".")
                found_endpoints.add(namespace)
        published_endpoints = found_endpoints.copy()
        # _probes contains API requests probe JSON-RPC request info,
        # Call the probing request for every known sub-API not explicitly advertised
        all_sub_apis = set(found_endpoints).union(set(self._probes.keys()))
        all_sub_apis.add("network_broadcast_api")
        for namespace, testmethod in self._probes.items():
            if namespace not in found_endpoints:
                try:
                    result = await self.retried_request(api=namespace,
                                                        method=testmethod["method"],
                                                        params=testmethod["params"],
                                                        max_retry=5)
                except (JsonRpcError, NoResponseError):
                    result = None
                if result is not None:
                    found_endpoints.add(namespace)
        # We don't have a probe yet for network_broadcast_api, this is a hack
        if "condenser_api" in found_endpoints:
            found_endpoints.add("network_broadcast_api")
        self._api_list = sorted(list(found_endpoints))
        # Let the BaseBot know that we are active again
        api_support_status = {}
        for subapi in all_sub_apis:
            api_support_status[subapi] = {}
            api_support_status[subapi]["published"] = subapi in published_endpoints
            api_support_status[subapi]["available"] = subapi in found_endpoints
        await self.bot.internal_node_api_support(node_uri=self._nodeurl,
                                                 api_support=api_support_status)
        self._requests = 0
        self._errors = 0
        self._blocks = 0
        self._active = True

    async def get_block(self, blockno):
        """Get a specific block from this node's block_api"""
        self._blocks += 1
        try:
            return await self.retried_request(api="block_api",
                                              method="get_block",
                                              params={"block_num": blockno})
        except (JsonRpcError, NoResponseError):
            return None

    async def run(self):
        """The main ever lasting loop for this public API node"""
        client_info = {"uri": self._nodeurl, "latency": None, "error_percentage": None}
        # Only stop when explicitly abandoned.
        while not self._abandon:
            # If a node operator upgrades or reconfigures his/her node, this may break the
            # assumptions made at initialization time. So we renitialize each node roughly
            # once an our, to make sure a long running bot won't get confused over time about
            # what node supports what API's
            if time.time() - self._last_reinit > 90:
                await self._initialize_api()
                self._last_reinit = time.time()
            # Our heartbeat operation is get_dynamic_global_properties.
            if "condenser_api" in self._api_list:
                try:
                    dynprob = await self.retried_request(api="condenser_api",
                                                         method="get_dynamic_global_properties")
                except (JsonRpcError, NoResponseError):
                    dynprob = None
                if dynprob is not None and "head_block_number" in dynprob:
                    headblock = dynprob["head_block_number"]
                    if not self._abandon:
                        # Tell the BaseBot what the last block available on this node is.
                        client_info["latency"] = self._latency.get() * 1000
                        client_info["error_percentage"] = self._error_rate.get() * 100
                        await self.bot.internal_potential_block(headblock, self, client_info)
            if not self._abandon:
                # Wait a few seconds before we check for new blocks again.
                await asyncio.sleep(3)

    def abort(self):
        """Try to break out of the main loop as quickly as possible so the app can end"""
        self._abandon = True


class _Method:
    """Function object representing a single method"""
    # pylint: disable=too-few-public-methods
    def __init__(self, bot, api, method):
        self.bot = bot
        self.api = api
        self.method = method

    async def __call__(self, *args, **kwargs):
        """Forward the call to the api_call method of the BaseBot"""
        if self.api == "condenser_api":
            return await self.bot.internal_api_call(self.api, self.method, args)
        return await self.bot.internal_api_call(self.api, self.method, kwargs)


class _SubAPI:
    """Helper classe representing the sub-API"""
    # pylint: disable=too-few-public-methods
    def __init__(self, bot, api):
        self.bot = bot
        self.api = api

    def __getattr__(self, method):
        return _Method(self.bot, self.api, method)


class BaseBot:
    """This classe should be subclassed by the actual bot. It connects to all
    the public HIVE API nodes, streams the blocks, trnasactions and/or operations
    to the derived class, and allows invocation of JSON-RPC calls from whithin the
    stream event handlers"""
    def __init__(self,
                 start_block=None,
                 roll_back=0,
                 roll_back_units="blocks",
                 eat_exceptions=False):
        self.forwarder = ObjectForwarder(self, eat_exceptions)
        self._block = start_block
        self.roll_back = roll_back
        self.abort_block = None
        if roll_back_units == "blocks":
            self.roll_back *= 1
        elif roll_back_units == "minutes":
            self.roll_back *= 20
        elif roll_back_units == "hours":
            self.roll_back *= 1200
        elif roll_back_units == "days":
            self.roll_back *= 28800
        elif roll_back_units == "weeks":
            self.roll_back *= 201600
        else:
            raise RuntimeError("Invalid roll_back_units")
        self._clients = []
        # Read in the config that we need for API probing
        probepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.json")
        with open(probepath, encoding="utf-8") as jsonfile:
            probes = json.load(jsonfile)
            self.api_list = list(probes.keys())
        self._abandon = False
        # Create a collection of public API node clients, one for each node.
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
            self._clients.append(_PubNodeClient(public_api_node, probes, self))

    # pylint: disable=too-many-arguments
    async def internal_node_status(self,
                                   node_uri,
                                   error_percentage,
                                   latency,
                                   ok_rate,
                                   error_rate,
                                   block_rate):
        """This callback forwards hourly node status to the bot implementation if
        callback is defined"""
        await self.forwarder.node_status(node_uri=node_uri,
                                         error_percentage=error_percentage,
                                         latency=latency,
                                         ok_rate=ok_rate,
                                         error_rate=error_rate,
                                         block_rate=block_rate)

    async def internal_node_api_support(self, node_uri, api_support):
        """This callback forwards hourly node API support to the bot implementation
        if callback is defined"""
        await self.forwarder.node_api_support(node_uri=node_uri,
                                              api_support=api_support)

    async def internal_potential_block(self, block, nodeclient, client_info):
        """This is the callback used by the node clients to tell the BaseBot about the latest
        known block on a node. If there are new blocks that arent processed yet, the client is
        asked to fetch the blocks"""
        # If there is no known last block, consider this one minus one to be the last block
        if self._block is None:
            self._block = block - 1 - self.roll_back
            if self.roll_back:
                self.abort_block = block
        # If the providing node is reliable (95% OK) and fast (less than half a second latency)
        # go and see if we can fetch some blocks
        if block > self._block and nodeclient.api_check("block_api", 0.05, 0.5):
            for blockno in range(self._block + 1, block + 1):
                # Don't "start" fetching blocks out of order, this doesn't mean,
                # we allow other loops to go and fetch the same block to minimize
                # chain to API latency.
                if blockno - self._block == 1:
                    # Fetch a single block
                    wholeblock = await nodeclient.get_block(blockno)
                    # If no other eventloop beat us to it, process the new block
                    if (blockno - self._block == 1
                            and wholeblock is not None
                            and "block" in wholeblock):
                        self._block += 1
                        # Process the actual block
                        await self._process_block(blockno, wholeblock["block"].copy(), client_info)
        if self.abort_block:
            self.abort()

    async def run(self, loop, other_tasks=None):
        """Run all of the API node clients as seperate tasks untill all are explicitly abandoned"""
        tasks = []
        # Add any aditinal tasks, this is meant for things like web servers
        # running in the same async process.
        if other_tasks is not None:
            tasks = other_tasks.copy()
        # Add all of the API node clients as tasks.
        for nodeclient in self._clients:
            tasks.append(loop.create_task(nodeclient.run()))
        # Wait for all tasks to complete
        await asyncio.gather(*tasks)

    async def _process_notify(self, operation, tid, transaction, block, client_info, timestamp):
        if (isinstance(operation["value"]["json"], list) and
                len(operation["value"]["json"]) == 2):
            methodname = "notify_" + str(operation["value"]["json"][0])
            if hasattr(self, methodname):
                await getattr(self.forwarder, methodname)(
                    required_auths=operation["value"]["required_auths"],
                    required_posting_auths=operation["value"][
                        "required_posting_auths"],
                    body=operation["value"]["json"][1],
                    tid=tid,
                    transaction=transaction,
                    block=block,
                    client_info=client_info,
                    timestamp=timestamp)

    async def _process_hive_engine(self,
                                   operation,
                                   tid,
                                   transaction,
                                   block,
                                   client_info,
                                   timestamp):
        if isinstance(operation["value"]["json"], dict):
            actions = [operation["value"]["json"]]
        elif isinstance(operation["value"]["json"], list):
            actions = operation["value"]["json"]
        else:
            actions = []
        for action in actions:
            if ('contractName' in action and
                    'contractAction' in action and
                    'contractPayload' in action):
                methodname = "engine_" + \
                        action["contractName"] + \
                        "_" + action["contractAction"]
                if hasattr(self, methodname):
                    await getattr(self.forwarder, methodname)(
                            required_auths=operation["value"]["required_auths"],
                            required_posting_auths=operation["value"][
                                "required_posting_auths"],
                            body=action["contractPayload"],
                            tid=tid,
                            transaction=transaction,
                            block=block,
                            client_info=client_info,
                            timestamp=timestamp)

    async def _process_custom_json(self,
                                   operation,
                                   tid,
                                   transaction,
                                   block,
                                   client_info,
                                   timestamp):
        if ("id" in operation["value"] and
                "json" in operation["value"] and
                "required_auths" in operation["value"] and
                "required_posting_auths" in operation["value"]):
            custom_json_id = "l2_" + operation["value"]["id"].replace("-", "_")
            if isinstance(operation["value"]["json"], str):
                try:
                    operation["value"]["json"] = json.loads(operation["value"]["json"])
                except json.decoder.JSONDecodeError:
                    pass
            if hasattr(self, custom_json_id):
                await getattr(self.forwarder, custom_json_id)(
                        required_auths=operation["value"]["required_auths"],
                        required_posting_auths=operation["value"][
                            "required_posting_auths"],
                        body=operation["value"]["json"],
                        block=block,
                        client_info=client_info,
                        timestamp=timestamp)
            if custom_json_id == "l2_notify":
                await self._process_notify(
                        operation=operation,
                        tid=tid,
                        transaction=transaction,
                        block=block,
                        client_info=client_info,
                        timestamp=timestamp)
            if custom_json_id == "l2_ssc_mainnet_hive":
                await self._process_hive_engine(
                        operation,
                        tid=tid,
                        transaction=transaction,
                        block=block,
                        client_info=client_info,
                        timestamp=timestamp)

    async def _process_block(self, blockno, block, client_info):
        """Process a brand new block"""
        # Separate transactions and transaction ids from the block
        transactions = block.pop("transactions")
        transaction_ids = block.pop("transaction_ids")
        if "timestamp" in block:
            try:
                timestamp = parser.parse(block["timestamp"])
            except ValueError:
                timestamp = datetime.datetime.fromtimestamp(0)
        else:
            timestamp = datetime.datetime.fromtimestamp(0)
        # If the derived class has a "block" callback, invoke it
        await self.forwarder.block(block=block,
                                   blockno=blockno,
                                   transactions=transactions,
                                   transaction_ids=transaction_ids,
                                   client_info=client_info,
                                   timestamp=timestamp)
        # Process all the transactions in the block
        # pylint: disable=consider-using-enumerate
        for index in range(0, len(transactions)):
            # Separate operations from the transaction
            operations = transactions[index].pop("operations")
            # If the derived class has a "transaction" callback, invoke it
            await self.forwarder.transaction(tid=transaction_ids[index],
                                             transaction=transactions[index],
                                             block=block,
                                             client_info=client_info,
                                             timestamp=timestamp)
            if self._abandon:
                return
            # Process all the operations in the transaction
            for operation in operations:
                # If the derived class has a "operation" callback, invoke it
                await self.forwarder.operation(operation=operation,
                                               tid=transaction_ids[index],
                                               transaction=transactions[index],
                                               block=block,
                                               client_info=client_info,
                                               timestamp=timestamp)
                if self._abandon:
                    return
                # If the derived class has an operation type specificcallback, invoke it
                if "type" in operation and "value" in operation:
                    if hasattr(self, operation["type"]):
                        await getattr(self.forwarder, operation["type"])(
                                body=operation["value"],
                                operation=operation,
                                tid=transaction_ids[index],
                                transaction=transactions[index],
                                block=block,
                                client_info=client_info,
                                timestamp=timestamp)
                        if self._abandon:
                            return
                    if operation["type"] == "custom_json_operation":
                        await self._process_custom_json(
                                operation,
                                tid=transaction_ids[index],
                                transaction=transactions[index],
                                block=block,
                                client_info=client_info,
                                timestamp=timestamp)
        await self.forwarder.block_processed(
                blockno=blockno,
                client_info=client_info,
                timestamp=timestamp)

    def __getattr__(self, attr):
        """The __getattr__ method provides the sub-API's."""
        if attr in self.api_list or attr == "network_broadcast_api":
            return _SubAPI(self, attr)
        raise AttributeError(f"Basebot has no sub-API {attr}")

    async def internal_api_call(self, api, method, params):
        """This method sets out a JSON-RPC request with the curently most reliable
        and fast node"""
        # Create a sorted list of node suitability metrics and node clients
        unsorted = []
        for client in self._clients:
            unsorted.append(client.get_api_quality(api))
        slist = sorted(unsorted, key=lambda x: (x[0], x[1]))
        # We give it four tries at most
        last_exception = None
        for _ in range(0, 4):
            # Go through the full list of node clients
            for entry in slist:
                # Don't use nodes with an error rate of two thirds or more,
                # don'r use nodes with a latency of more than 30 seconds.
                if entry[0] < 0.667 and entry[1] < 30:
                    try:
                        # Every node client gets two quick tries.
                        return await entry[2].retried_request(api=api,
                                                              method=method,
                                                              params=params,
                                                              max_retry=2,
                                                              retry_pause=0.2)
                    except JsonRpcError as exp:
                        last_exception = exp
                    except NoResponseError:
                        pass
        if last_exception is not None:
            raise last_exception
        raise NoResponseError("No valid response from any node""")

    def abort(self):
        """Abort async operations in all running tasks"""
        self._abandon = True
        for client in self._clients:
            client.abort()
