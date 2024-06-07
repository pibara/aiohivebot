"""Hive-engine specific L2 code"""
import math
import copy
import json
from aiohivebot.core import JsonRpcClient, JsonRpcError, NoResponseError
# pylint: disable=too-few-public-methods


class BlockChainMethod:
    """Function object for blockchain endpoint"""
    def __init__(self, bot, method):
        self.bot = bot
        self.method = method

    async def __call__(self, *args, **kwargs):
        return await self.bot.internal_api_call_l2(
                layer2="engine",
                api="blockchain",
                method=self.method,
                params=kwargs)


class ContractMethod:
    """Function object for contract endpoint"""
    def __init__(self, bot, contract, table, method):
        self.bot = bot
        self.contract = contract
        self.table = table
        self.method = method

    async def __call__(self, *args, **kwargs):
        """Forward the call to the api_call method of the BaseBot"""
        return await self.bot.internal_api_call_l2(layer2="engine",
                                                   api="contracts",
                                                   method=self.method,
                                                   params={
                                                       "contract": self.contract,
                                                       "table": self.table,
                                                       "query": kwargs
                                                    })


class ContractMethodAPI:
    """Intermidiate __getattr__ layer for hive-engine contract API"""
    def __init__(self, bot, contract, table):
        self.bot = bot
        self.contract = contract
        self.table = table

    def __getattr__(self, method):
        return ContractMethod(self.bot, self.contract, self.table, method)


class ContractTableAPI:
    """Intermidiate __getattr__ layer for hive-engine contract API"""
    def __init__(self, bot, contract):
        self.bot = bot
        self.contract = contract

    def __getattr__(self, table):
        return ContractMethodAPI(self.bot, self.contract, table)


class ContractAPI:
    """Intermidiate __getattr__ layer for hive-engine contract API"""
    def __init__(self, bot):
        self.bot = bot

    def __getattr__(self, contract):
        return ContractTableAPI(self.bot, contract)


class BlockChainAPI:
    """Intermidiate __getattr__ layer for hive-engine blockchain API"""
    def __init__(self, bot):
        self.bot = bot

    def __getattr__(self, method):
        return BlockChainMethod(self.bot, method)


class HiveEngineAPI:
    """Multi-client API for Hive-engine"""
    def __init__(self, bot):
        self.bot = bot

    def __getattr__(self, api):
        if api == "contracts":
            return ContractAPI(self.bot)
        if api == "blockchain":
            return BlockChainAPI(self.bot)
        raise AttributeError(f"HiveEngineAPI has no sub-API {api}")


class EngineClient(JsonRpcClient):
    """Client for single Hive-Engine API node"""
    def __init__(self, public_api_node, bot):
        super().__init__(
                public_api_node=public_api_node[0],
                public_api_path=public_api_node[1],
                public_api_path_2=["contracts", "blockchain"],
                bot=bot,
                layer2="engine",
                probe_time=3,
                reinit_time=900
            )

    async def heartbeat(self):
        """Heartbeat operation for hive-engine nodes"""
        try:
            nodestatus = await self.retried_request(
                    api="blockchain",
                    method="getStatus",
                    params={},
                    max_retry=3
                )
            if "lastBlockNumber" in nodestatus and "lastVerifiedBlockNumber" in nodestatus:
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
                    await self._bot.internal_potential_block_l2(
                            block=nodestatus["lastBlockNumber"],
                            irrblock=nodestatus["lastVerifiedBlockNumber"],
                            nodeclient=self,
                            client_info=client_info,
                            layer2="engine")
        except (JsonRpcError, NoResponseError):
            pass

    async def get_block(self, blockno):
        """Get a specific block from this node's block_api"""
        self._stats["blocks"] += 1
        try:
            return await self.retried_request(api="blockchain",
                                              method="getBlockInfo",
                                              params={"blockNumber": blockno})
        except (JsonRpcError, NoResponseError):
            return None

    async def initialize_api(self):
        """Initialize API, this method must be overridden"""


async def process_engine_event(
        forwarder,
        required_auths,
        required_posting_auths,
        body,
        tid,
        transaction,
        blockno,
        block,
        client_info,
        timestamp):
    """Process a main-chain event for hive-engine custom_json"""
    # pylint: disable=too-many-arguments
    if isinstance(body, dict):
        actions = [body]
    elif isinstance(body, list):
        actions = body
    else:
        actions = []
    for action in actions:
        if ('contractName' in action and
                action["contractName"] and
                'contractAction' in action and
                action["contractAction"] and
                'contractPayload' in action and
                action["contractPayload"]):
            methodname = action["contractName"] + "_" + action["contractAction"]
            await getattr(forwarder, methodname)(
                    required_auths=required_auths,
                    required_posting_auths=required_posting_auths,
                    body=action["contractPayload"],
                    tid=tid,
                    transaction=transaction,
                    blockno=blockno,
                    block=block,
                    client_info=client_info,
                    timestamp=timestamp)


async def hiveengine_process_block(
                forwarder,
                blockno,
                block,
                client_info,
                timestamp):
    """Process a bloch from the hive-engine side-chain"""
    # pylint: disable=too-many-arguments
    myblock = copy.deepcopy(block)
    if "transactions" in myblock:
        transactions = myblock.pop("transactions")
    else:
        transactions = []
    for transaction in transactions:
        if ("sender" in transaction and
                "transactionId" in transaction and
                "contract" in transaction and
                "action" in transaction and
                "payload" in transaction):
            sender = transaction.pop("sender")
            tid = transaction.pop("transactionId")
            contract = transaction.pop("contract")
            action = transaction.pop("action")
            body = transaction.pop("payload")
            try:
                body = json.loads(body)
            except json.decoder.JSONDecodeError:
                pass
            await forwarder.l2_transaction(
                sender=sender,
                contract=contract,
                action=action,
                body=body,
                tid=tid,
                transaction=transaction,
                blockno=blockno,
                block=myblock,
                client_info=client_info,
                timestamp=timestamp
            )
            methodname = "l2_" + contract + "_" + action
            await getattr(forwarder, methodname)(
                sender=sender,
                body=body,
                tid=tid,
                transaction=transaction,
                blockno=blockno,
                block=myblock,
                client_info=client_info,
                timestamp=timestamp
            )
