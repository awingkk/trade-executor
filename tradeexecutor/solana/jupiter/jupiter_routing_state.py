"""Route trades to different Uniswap v2 like exchanges."""

import logging
import base64
from typing import List, Optional, Tuple
from decimal import Decimal
import asyncio

from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from solana.rpc.api import Client

from jupiter_python_sdk.jupiter import Jupiter

from tradeexecutor.solana.tx import TransactionBuilder
from tradeexecutor.solana.solana_routing_state import SolanaRoutingState

from tradeexecutor.state.identifier import TradingPairIdentifier, AssetIdentifier
from tradeexecutor.utils.slippage import get_slippage_in_bps
from tradingstrategy.pair import PandasPairUniverse
from tradingstrategy.types import Percent

logger = logging.getLogger(__name__)



def jupiter_quote(
    jupiter: Jupiter,
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int=None,
    swap_mode: str="ExactIn",
    only_direct_routes: bool=False,
    as_legacy_transaction: bool=False,
    dexes: list=None,
    exclude_dexes: list=None,
    max_accounts: int=None,
    platform_fee_bps: int=None        
) -> str:
    loop = asyncio.get_event_loop()
    res = loop.run_until_complete(
        jupiter.quote(
            input_mint,
            output_mint,
            amount,
            slippage_bps,
            swap_mode,
            only_direct_routes,
            as_legacy_transaction,
            dexes,
            exclude_dexes,
            max_accounts,
            platform_fee_bps
        )
    )
    return res


def jupiter_swap(
    jupiter: Jupiter,
    input_mint: str,
    output_mint: str,
    amount: int=0,
    quoteResponse: str=None,
    wrap_unwrap_sol: bool=True,
    slippage_bps: int=1,
    swap_mode: str="ExactIn",
    prioritization_fee_lamports: int=None,
    only_direct_routes: bool=False,
    as_legacy_transaction: bool=False,
    exclude_dexes: list=None,
    max_accounts: int=None,
    platform_fee_bps: int=None,
    instructions: bool=False,        
) -> str | dict:
    loop = asyncio.get_event_loop()
    res = loop.run_until_complete(
        jupiter.swap(
            input_mint,
            output_mint,
            amount,
            quoteResponse,
            wrap_unwrap_sol,
            slippage_bps,
            swap_mode,
            prioritization_fee_lamports,
            only_direct_routes,
            as_legacy_transaction,
            exclude_dexes,
            max_accounts,
            platform_fee_bps,
            instructions
        )
    )
    return res


def convert_dict_to_instruction(ix_data):
    program_id = Pubkey.from_string(ix_data["programId"])
    data = base64.b64decode(ix_data["data"])
    accounts = []
    for acc in ix_data["accounts"]:
        accounts.append(AccountMeta(Pubkey.from_string(acc["pubkey"]), acc["isSigner"], acc["isWritable"]))
    ix = Instruction(program_id, data, accounts)
    return ix



class JupiterRoutingState(SolanaRoutingState):
    """Manage transaction building for multiple Raydium trades.

    - Lifespan is one rebalance - remembers already made approvals

    - Web3 connection and hot wallet

    - Approval tx creation

    - Swap tx creation

    Manage the state of already given approvals here,
    so that we do not do duplicates.

    The approvals are not persistent in the executor state,
    but are specific for each cycle.
    """

    def __init__(
        self,
        pair_universe: PandasPairUniverse,
        *,
        tx_builder: Optional[TransactionBuilder] = None,
        swap_cu_limit: int | None = None,
        approve_cu_limit: int | None = None,
        web3: Optional[Client] = None,
    ):
        super().__init__(pair_universe, tx_builder=tx_builder, swap_cu_limit=swap_cu_limit, approve_cu_limit=approve_cu_limit, web3=web3)

        self.jupiter = Jupiter(self.hot_wallet.private_key)
        self.dex_routes = None


    def set_dex_routes(self, dexes):
        self.dex_routes = dexes


    def trade_on_router_two_way(self,
            target_pair: TradingPairIdentifier,
            reserve_asset: AssetIdentifier,
            reserve_amount: int,
            max_slippage: Percent,
            check_balances: False,
            notes="",
        ):
        """Prepare the actual swap.

        :param check_balances:
            Check on-chain balances that the account has enough tokens
            and raise exception if not.
        """

        if check_balances:
            self.check_has_enough_tokens(reserve_asset, reserve_amount)

        raw_fee = int(target_pair.fee * 1_000_000)

        logger.info(
            "Creating a trade for %s, slippage tolerance %f, trade reserve %s, amount in %d",
            target_pair,
            max_slippage,
            reserve_asset,
            reserve_amount,
        )

        input_mint = target_pair.quote.other_data["Pubkey"]
        output_mint = target_pair.base.other_data["Pubkey"]
        amount_in = reserve_asset.convert_to_raw_amount(Decimal(reserve_amount))

        quote_resp = jupiter_quote(
            self.jupiter,
            input_mint=str(input_mint),
            output_mint=str(output_mint),
            amount=amount_in,
            slippage_bps=int(get_slippage_in_bps(max_slippage)),
            swap_mode="ExactIn",
            only_direct_routes=True,
            dexes=self.dex_routes,
            platform_fee_bps=str(raw_fee),
        )

        instructions = jupiter_swap(
            self.jupiter,
            input_mint=str(input_mint),
            output_mint=str(output_mint),
            quoteResponse=quote_resp,
            instructions=True,
        )

        ix_data_list = []
        ix_data_list.extend(instructions["computeBudgetInstructions"])
        ix_data_list.extend(instructions["setupInstructions"])
        ix_data_list.append(instructions["swapInstruction"])
        ix_data_list.append(instructions["cleanupInstruction"])
        ix_data_list.extend(instructions["otherInstructions"])
        ix_list = []
        for data in ix_data_list:
            ix_list.append(convert_dict_to_instruction(data))
        
        # If the receiver (vault) has its own security policy for slippage tolerance check it here
        receiver_slippage_tolerance = self.tx_builder.get_internal_slippage_tolerance()
        if receiver_slippage_tolerance is not None:
            # TODO: TxBuilder configures slippage tolerance as opposite of the trades
            receiver_slippage_tolerance = 1 - receiver_slippage_tolerance
            trade_slippage_tolerance = max_slippage
            assert receiver_slippage_tolerance > trade_slippage_tolerance, f"Receiver (vault) slippage tolerance tighter than the trade slippage tolerance.\nReceiver: {receiver_slippage_tolerance}, trade: {trade_slippage_tolerance}"

        return self.create_signed_transaction(
            ix_list,
            self.swap_cu_limit,
            notes=notes,
        )
