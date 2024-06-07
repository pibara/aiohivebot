"""A multi-eventloop asynchonous python lib for writing HIVE-blockchain bots
and DApp backend code"""
import asyncio
import time
import json
import os
import math
import struct
import datetime
import inspect
from binascii import unhexlify, hexlify
from base58 import b58encode, b58decode
from dateutil import parser
import httpx
from ellipticcurve.privateKey import PrivateKey
from average import EWMA
from aiohivebot.l2 import makel2client, l2names, process_l2_event, layer2_multinode_client
from aiohivebot.l2 import l2_process_block
from aiohivebot.core import JsonRpcClient, JsonRpcError, NoResponseError


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
                raise ValueError("Non standard named arguments for method:" + str(list(unknown)), ", expected:", list(kwargs.keys()))
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
    def __init__(self, bot, eat_exceptions=False, layer2=""):
        self._methods = {}
        self._null = FunctionNull()
        self._layer2 = layer2
        self._subs = {}
        if not layer2:
            for subname in l2names():
                self._subs[subname] = ObjectForwarder(
                        bot=bot,
                        eat_exceptions=eat_exceptions,
                        layer2=subname
                    )
            for method in dir(bot):
                if (method[0] != "_"
                        and not method.startswith("internal_")
                        and method not in ['abort', 'run']):
                    self._methods[method] = FunctionForwarder(
                            getattr(bot, method),
                            eat_exceptions,
                            self)
        else:
            prefix = layer2 + "_"
            for method in dir(bot):
                if method.startswith(prefix):
                    self._methods[method[len(prefix):]] = FunctionForwarder(
                            getattr(bot, method),
                            eat_exceptions,
                            self)

    def __getattr__(self, methodname):
        return self._methods.get(methodname, self._null)

    def __call__(self, layer2):
        if layer2:
            return self._subs[layer2]
        return self

class Operation:
    """Represents a single operation within a transaction"""
    def __init__(self, transaction, opname):
        self.transaction=transaction
        self.opname=opname

    def __call__(self, *args, **kwargs):
        self.transaction.operations.append([self.opname, kwargs])
        return self.transaction

class Transaction:
    """Transaction helper class"""
    def __init__(self, bot, key_id):
        self.bot = bot
        self.key_id = key_id
        self.operations = []

    def __getattr__(self, opname):
        return Operation(self, opname)

    async def __call__(self):
        if not self.operations:
            raise RuntimeError("Can't call an empty transaction")
        await self.bot.internal_transaction(key_id=self.key_id, operations=self.operations)

class _PubNodeClient(JsonRpcClient):
    """Client that keeps up with a single public HIVE-API node"""
    # pylint: disable=too-many-instance-attributes
    def __init__(self, nodeurl, probes, bot, config):
        super().__init__(
                public_api_node=nodeurl,
                public_api_path="",
                public_api_path_2=None,
                bot=bot,
                layer2="",
                probe_time=3,
                reinit_time=900,
                config=config
            )
        self._api_list = []
        self._probes = probes

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
        error_rate = self._stats["error_rate"].get()
        if math.isnan(error_rate):
            error_rate = 1
        latency = self._stats["latency"].get()
        if math.isnan(latency):
            latency = 1000000
        return [error_rate, latency, self]

    def api_check(self, api, error_rate_treshold, max_latency):
        """Check if the node has the given API and if its error rate and latency are within
        reasonable tresholds"""
        if (api in self._api_list and error_rate_treshold > self._stats["error_rate"].get()
                and max_latency > self._stats["latency"].get()):
            return True
        return False

    async def initialize_api(self):
        """(Re)-initialize the API for this node by figuring out what API subsets
        this node supports"""
        # Get the list or methods that are explicitly advertised
        try:
            methods = await self.retried_request(method="jsonrpc.get_methods",
                                                 params={},
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
                    result = await self.retried_request(
                            method=namespace + "." + testmethod["method"],
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
        api_support_status = {}
        for subapi in all_sub_apis:
            api_support_status[subapi] = {}
            api_support_status[subapi]["published"] = subapi in published_endpoints
            api_support_status[subapi]["available"] = subapi in found_endpoints
        await self._bot.internal_node_api_support(
                node_uri=self._api_node,
                api_support=api_support_status)

    async def get_block(self, blockno):
        """Get a specific block from this node's block_api"""
        self._stats["blocks"] += 1
        try:
            return await self.retried_request(method="block_api.get_block",
                                              params={"block_num": blockno})
        except (JsonRpcError, NoResponseError):
            return None

    async def get_block_range(self, blockno, count):
        """Get a specific block range from this node's block_api"""
        self._stats["block_range"] += 1
        try:
            return await self.retried_request(method="block_api.get_block_range",
                                              params={"starting_block_num": blockno,
                                                  "count": count})
        except (JsonRpcError, NoResponseError):
            return None


    async def heartbeat(self):
        if "condenser_api" in self._api_list:
            try:
                dynprob = await self.retried_request(
                        params=[],
                        method="condenser_api.get_dynamic_global_properties")
            except (JsonRpcError, NoResponseError):
                dynprob = None
            if (dynprob is not None and
                    "head_block_number" in dynprob and
                    "last_irreversible_block_num" in dynprob and
                    "head_block_id" in dynprob and
                    "time" in dynprob):
                headblock = dynprob["head_block_number"]
                headblock_id = dynprob["head_block_id"]
                irrblock = dynprob["last_irreversible_block_num"]
                timestamp = parser.parse(dynprob["time"])
                if not self._abandon:
                    # Tell the BaseBot what the last block available on this node is.
                    error_rate = self._stats["error_rate"].get()
                    if math.isnan(error_rate):
                        error_rate = 1
                    latency = self._stats["latency"].get()
                    if math.isnan(latency):
                        latency = 1000000
                    client_info = {}
                    client_info["uri"] = self._api_node
                    client_info["latency"] = latency * 1000
                    client_info["error_percentage"] = error_rate * 100
                    await self._bot.internal_potential_block(
                            headblock,
                            irrblock,
                            self,
                            client_info,
                            headblock_id,
                            timestamp)


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


class _Layer2APIs:
    # pylint: disable=too-few-public-methods
    """Helper for Layer-2 JSON-RPC APIs"""
    def __init__(self, bot):
        self.bot = bot

    def __getattr__(self, api):
        return self.bot.get_layer2_api(api)


class Signer:
    """Signer for signed operations"""
    def __init__(self, bot, key):
        self.bot=bot
        self.key=PrivateKey.fromString(hexlify(b58decode(key)[1:-4]))
        self.blockno = None
        self.ref_block_num = None
        self.ref_block_prefix=None
        self.expiration = datetime.datetime.fromtimestamp(0)

    def update_signing_reference(self, blockno, blockid, timestamp):
        if self.blockno is None or blockno > self.blockno:
            self.blockno = blockno
            self.ref_block_num = blockno & 0xFFFF
            self.ref_block_prefix = struct.unpack_from("<I", unhexlify(blockid), 4)[0]
            self.expiration = timestamp + datetime.timedelta(seconds=30)
            print(self.expiration, self.ref_block_num, self.ref_block_prefix)

    async def transaction(self, operations):
        tx = {
                "ref_block_num": self.ref_block_num,
                "ref_block_prefix": self.ref_block_prefix,
                "expiration": self.expiration.strftime('%Y-%m-%dT%H:%M:%S%Z'),
                "operations": operations,
                "extensions": [],
                "signatures": []
            }
        print(json.dumps(tx, indent=2))
        transhex = await self.bot.condenser_api.get_transaction_hex(tx)
        print("transhex:", transhex)



class BaseBot:
    """This classe should be subclassed by the actual bot. It connects to all
    the public HIVE API nodes, streams the blocks, trnasactions and/or operations
    to the derived class, and allows invocation of JSON-RPC calls from whithin the
    stream event handlers"""
    # pylint: disable=too-many-arguments, too-many-instance-attributes
    def __init__(self,
                 start_block=None,
                 roll_back=0,
                 roll_back_units="blocks",
                 eat_exceptions=False,
                 enable_layer2=None,
                 use_irreversable=False,
                 use_virtual=False,
                 maintain_order=False,
                 ratelimit_period=60,
                 ratelimit_req_per_period=60):
        # pylint: disable=too-many-locals
        self.forwarder = ObjectForwarder(self, eat_exceptions)
        self._block = start_block
        self._l2block = {}
        self.roll_back = roll_back
        self.abort_block = None
        self._alltasks = []
        self._signers = {}
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
        if enable_layer2 is None:
            self.enable_layer2 = set()
        else:
            self.enable_layer2 = set(enable_layer2)
        self.use_irreversable = use_irreversable
        self.use_virtual = use_virtual
        self.maintain_order = maintain_order
        self.processing = False
        self._layer2_clients = {}
        # Read in the config that we need for API probing
        probepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.json")
        with open(probepath, encoding="utf-8") as jsonfile:
            probes = json.load(jsonfile)
            self.api_list = list(probes.keys())
        hivenodepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hive-nodes.json")
        with open(hivenodepath, encoding="utf-8") as jsonfile:
            api_nodes_config = json.load(jsonfile)
            api_nodes = list(api_nodes_config.keys())
        for key in self.enable_layer2:
            self._layer2_clients[key] = []
            l2nodepath = os.path.join(
                    os.path.dirname(
                        os.path.abspath(__file__)
                    ),
                    "l2", key + "-nodes.json"
                )
            with open(l2nodepath, encoding="utf-8") as jsonfile:
                l2_nodes = json.load(jsonfile)
                l2_nodes = [[x, ""] if isinstance(x, str) else x for x in l2_nodes]
                for public_api_node in l2_nodes:
                    self._layer2_clients[key].append(
                            makel2client(
                                layer2=key,
                                node_array=public_api_node,
                                bot=self))
        self._abandon = False
        # Create a collection of public API node clients, one for each node.
        for public_api_node in api_nodes:
            self._clients.append(_PubNodeClient(public_api_node, probes, self, config=api_nodes_config[public_api_node]))
        self.block_counter = 0
        self.start = time.time()

    # pylint: disable=too-many-arguments
    async def internal_node_status(self,
                                   node_uri,
                                   error_percentage,
                                   latency,
                                   ok_rate,
                                   error_rate,
                                   block_rate=0,
                                   layer2=""):
        """This callback forwards hourly node status to the bot implementation if
        callback is defined"""
        if layer2:
            await self.forwarder(layer2).l2_node_status(
                    node_uri=node_uri,
                    error_percentage=error_percentage,
                    latency=latency,
                    ok_rate=ok_rate,
                    error_rate=error_rate,
                    block_rate=block_rate)
        else:
            await self.forwarder.node_status(
                    node_uri=node_uri,
                    error_percentage=error_percentage,
                    latency=latency,
                    ok_rate=ok_rate,
                    error_rate=error_rate,
                    block_rate=block_rate)

    async def internal_node_api_support(self, node_uri, api_support, layer2=""):
        """This callback forwards hourly node API support to the bot implementation
        if callback is defined"""
        await self.forwarder(layer2).node_api_support(
                node_uri=node_uri,
                api_support=api_support)

    async def internal_potential_block(self, block, irrblock, nodeclient, client_info,
            headblock_id, timestamp):
        """This is the callback used by the node clients to tell the BaseBot about the latest
        known block on a node. If there are new blocks that arent processed yet, the client is
        asked to fetch the blocks"""
        self.update_signing_reference(blockno=block, blockid=headblock_id, timestamp=timestamp)
        if self.use_irreversable:
            block = irrblock
        # If there is no known last block, consider this one minus one to be the last block
        if self._block is None:
            self._block = block - 1 - self.roll_back
            if self.roll_back:
                self.abort_block = block
        # If the providing node is reliable (95% OK) and fast (less than half a second latency)
        # go and see if we can fetch some blocks
        if (block > self._block and nodeclient.api_check("block_api", 0.05, 0.5) and 
                (self.maintain_order == False or self.processing == False))  :
            if block - self._block < 20 or nodeclient.config["single_block"]:
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
                            self.processing = True
                            await self._process_block(blockno, wholeblock["block"].copy(), client_info)
                            self.processing = False
            else: # There is a lot to catch up with, we get many blocks at once in order to catch up.
                blockcount = block - self._block - 10
                if blockcount > 128:
                    blockcount = 128
                manyblocks = await nodeclient.get_block_range(self._block + 1, blockcount)
                if manyblocks is not None and "blocks" in manyblocks and len(manyblocks["blocks"]) == blockcount:
                    for index in range(0, blockcount):
                        blockno = self._block + 1 + index
                        if blockno - self._block == 1:
                            self._block += 1
                            self.processing = True
                            await self._process_block(blockno, manyblocks["blocks"][index].copy(), client_info)
                            self.processing = False

        if self.abort_block:
            self.abort()

    async def internal_potential_block_l2(self, block, irrblock, nodeclient, client_info, layer2):
        """This is the callback used by the l2 node clients to tell the BaseBot about the latest
        known block on a node. If there are new blocks that arent processed yet, the client is
        asked to fetch the blocks"""
        if self.use_irreversable:
            block = irrblock
        if layer2 not in self._l2block:
            self._l2block[layer2] = block - 1
        # If the providing node is reliable (95% OK) and fast (less than half a second latency)
        # go and see if we can fetch some blocks
        if block > self._l2block[layer2] and nodeclient.reliability_check(0.05, 0.5):
            for blockno in range(self._l2block[layer2] + 1, block + 1):
                # Don't "start" fetching blocks out of order, this doesn't mean,
                # we allow other loops to go and fetch the same block to minimize
                # chain to API latency.
                if blockno - self._l2block[layer2] == 1:
                    # Fetch a single block
                    wholeblock = await nodeclient.get_block(blockno)
                    # If no other eventloop beat us to it, process the new block
                    if (blockno - self._l2block[layer2] == 1
                            and wholeblock is not None):
                        self._l2block[layer2] += 1
                        # Process the actual block
                        await self._process_block_l2(layer2=layer2,
                                                     blockno=blockno,
                                                     block=wholeblock.copy(),
                                                     client_info=client_info)

    def wire_up_get_runnables(self):
        """Get runnables with an async run ans synchonous abort method"""
        runnables = []
        for nodeclient in self._clients:
            runnables.append(nodeclient)
        for _, nodeclients in self._layer2_clients.items():
            for nodeclient in nodeclients:
                runnables.append(nodeclient)
        return runnables

    def wire_up_get_tasks(self):
        """Get all runnables as tasks"""
        tasks = []
        for runnable in self.wire_up_get_runnables():
            tasks.append(asyncio.create_task(runnable.run()))
        return tasks

    def wire_up_on_startup(self):
        """Get all runnables as tasks, and tuck them away"""
        self._alltasks = self.wire_up_get_tasks()

    async def wire_up_on_shutdown_async(self):
        """Abort all the runnable tasks and gather all their tasks"""
        self.abort()
        tasks = self._alltasks
        await asyncio.gather(*tasks)

    def wire_up_sanic(self, app):
        """Non-ideal wire-up for sanic apps, no abort for sanic, don't use"""
        for runnable in self.wire_up_get_runnables():
            app.add_task(runnable.run)

    def wire_up_aiohttp(self, app):
        """A simle wire-up function for aiohttp apps"""
        class IgnoreApp:
            """Helper class that idnores the app argument used by aiohttp"""
            # pylint: disable=too-few-public-methods
            def __init__(self, runnable, make_task=False):
                self.runnable = runnable
                self.make_task = make_task

            async def __call__(self, app, make_task=False):
                if self.make_task:
                    asyncio.create_task(self.runnable())
                else:
                    self.runnable()

        for runnable in self.wire_up_get_runnables():
            app.on_startup.append(IgnoreApp(runnable.run, True))
            app.on_cleanup.append(IgnoreApp(runnable.abort))

    async def run(self):
        """Run all of the API node clients as seperate tasks untill all are explicitly abandoned"""
        tasks = self.wire_up_get_tasks()
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

    async def _process_custom_json(self,
                                   operation,
                                   tid,
                                   transaction,
                                   blockno,
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
                        blockno=blockno,
                        block=block,
                        client_info=client_info,
                        timestamp=timestamp)
            await process_l2_event(
                        self.forwarder,
                        custom_json_id=custom_json_id,
                        required_auths=operation["value"]["required_auths"],
                        required_posting_auths=operation["value"][
                            "required_posting_auths"],
                        body=operation["value"]["json"],
                        tid=tid,
                        transaction=transaction,
                        blockno=blockno,
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
                                             blockno=blockno,
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
                                               blockno=blockno,
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
                                blockno=blockno,
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
                                blockno=blockno,
                                block=block,
                                client_info=client_info,
                                timestamp=timestamp)
        if self.use_virtual:
            virtual_ops = await self.account_history_api.get_ops_in_block(
                    block_num=blockno,
                    only_virtual=True,
                    include_reversible=self.use_irreversable)
            if self._abandon:
                    return
            for vop in virtual_ops["ops"]:
                operation = vop["op"]
                await self.forwarder.operation(operation=operation,
                                               tid=vop["trx_id"],
                                               transaction="VIRTUAL",
                                               blockno=vop["block"],
                                               block=block,
                                               client_info=client_info,
                                               timestamp=timestamp)
                if self._abandon:
                    return
                if "type" in operation and "value" in operation:
                    if hasattr(self, operation["type"]):
                        await getattr(self.forwarder, operation["type"])(
                                body=operation["value"],
                                operation=operation,
                                tid=vop["trx_id"],
                                transaction="VIRTUAL",
                                blockno=block,
                                block=block,
                                client_info=client_info,
                                timestamp=timestamp)
                        if self._abandon:
                            return
        await self.forwarder.block_processed(
                blockno=blockno,
                client_info=client_info,
                timestamp=timestamp)
        await self._block_processed()

    async def _block_processed(self):
        self.block_counter += 1
        age = time.time() -  self.start
        if age >= 60:
            await self.forwarder.monitor_block_rate(rate=int(self.block_counter * 100 / age)/100)
            self.block_counter = 0
            self.start = time.time()

    async def _process_block_l2(self, layer2, blockno, block, client_info):
        """Process a brand new block"""
        # Separate transactions and transaction ids from the block
        if "timestamp" in block:
            try:
                timestamp = parser.parse(block["timestamp"])
            except ValueError:
                timestamp = datetime.datetime.fromtimestamp(0)
        else:
            timestamp = datetime.datetime.fromtimestamp(0)
        # If the derived class has a "block" callback, invoke it
        await self.forwarder(layer2).l2_block(
                blockno=blockno,
                block=block,
                client_info=client_info,
                timestamp=timestamp)
        await l2_process_block(
                forwarder=self.forwarder(layer2),
                layer2=layer2,
                blockno=blockno,
                block=block,
                client_info=client_info,
                timestamp=timestamp)
        await self.forwarder(layer2).l2_block_processed(
                blockno=blockno,
                client_info=client_info,
                timestamp=timestamp)

    def __getattr__(self, attr):
        """The __getattr__ method provides the sub-API's."""
        if attr == "l2":
            return _Layer2APIs(self)
        if attr == "broadcast":
            if "default" in self._signers:
                key_id = "default"
            elif len(self._signers) == 1:
                key_id = list(self._signers.keys())[0]
            elif self._signers:
                raise RuntimeError("No broadcast when using multipte keys and no default,"
                        "use start_transaction method instead and specify key id.")
            else:
                raise RuntimeError("No broadcast when zero keys defined for bot")
            return Transaction(self, key_id)
        if attr in self.api_list or attr == "network_broadcast_api":
            return _SubAPI(self, attr)
        raise AttributeError(f"Basebot has no sub-API {attr}")

    def start_transaction(self, key_id=None):
        """Start building a new transaction"""
        if not self._signers:
            raise RuntimeError("No start_transaction when zero keys defined for bot")
        if key_id is None:
            if "default" in self._signers:
                my_key_id = "default"
            elif len(self._signers) == 1:
                my_key_id = list(self._signers.keys())[0]
            else:
                raise RuntimeError("No key_id defined while using multipte keys and no default")
        else:
            if key_id not in self._signers:
                raise RuntimeError("Specified key_id not defined for bot")
            my_key_id = key_id
        return Transaction(self, key_id)

    async def internal_transaction(self, key_id,  operations):
        """Sign and broadcast a list of operations as a transaction""" 
        await self._signers[key_id].transaction(operations)
        

    def get_layer2_api(self, api):
        """Get the layer-2 API as a helper object"""
        if api in self._layer2_clients and self._layer2_clients[api]:
            return layer2_multinode_client(api=api, bot=self)
        raise AttributeError(f"Basebot has no active layer-2 {api}")

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

    async def internal_api_call_l2(self, layer2, api, method, params):
        """This method sets out a JSON-RPC request with the curently most reliable
        and fast node"""
        # Create a sorted list of node suitability metrics and node clients
        unsorted = []
        for client in self._layer2_clients[layer2]:
            unsorted.append(client.get_quality())
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

    def set_key(self, key, identity="default"):
        self._signers[identity] = Signer(bot=self, key=key)

    def update_signing_reference(self, blockno, blockid, timestamp):
        """Update the signing reference for all active signers"""
        for _, signer in self._signers.items():
            signer.update_signing_reference(blockno=blockno, blockid=blockid, timestamp=timestamp)

    def abort(self):
        """Abort async operations in all running tasks"""
        self._abandon = True
        for client in self._clients:
            client.abort()
