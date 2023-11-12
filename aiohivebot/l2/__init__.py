"""This file contains the glue code for the layer 2 handles"""
from aiohivebot.l2.engine import EngineClient, process_engine_event, HiveEngineAPI
from aiohivebot.l2.engine import hiveengine_process_block


def l2names():
    """Return the valid names for layer 2"""
    return ["engine"]


def makel2client(layer2, node_array, bot):
    """Create a client for a given layer 2 public API node"""
    if layer2 == "engine":
        return EngineClient(node_array, bot)
    raise RuntimeError("No such layer 2:" + layer2)


async def process_l2_event(
        forwarder,
        custom_json_id,
        required_auths,
        required_posting_auths,
        body,
        tid,
        transaction,
        blockno,
        block,
        client_info,
        timestamp):
    """Process one layer 2 event"""
    # pylint: disable=too-many-arguments
    if custom_json_id == "l2_ssc_mainnet_hive":
        await process_engine_event(
                forwarder=forwarder("engine"),
                required_auths=required_auths,
                required_posting_auths=required_posting_auths,
                body=body,
                tid=tid,
                transaction=transaction,
                blockno=blockno,
                block=block,
                client_info=client_info,
                timestamp=timestamp)


def layer2_multinode_client(api, bot):
    """Get an API proxy that we can throw queries at"""
    if api == "engine":
        return HiveEngineAPI(bot)
    raise RuntimeError("Not a defined layer 2:" + api)


async def l2_process_block(
                forwarder,
                layer2,
                blockno,
                block,
                client_info,
                timestamp):
    """Process a block from a layer2 side chain"""
    # pylint: disable=too-many-arguments
    if layer2 == "engine":
        await hiveengine_process_block(
                forwarder=forwarder,
                blockno=blockno,
                block=block,
                client_info=client_info,
                timestamp=timestamp)
