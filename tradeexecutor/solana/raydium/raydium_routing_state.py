"""Route trades to different Uniswap v2 like exchanges."""

import logging
from typing import List, Optional, Tuple

from solana.rpc.api import Client

from soldexpy.raydium_pool import RaydiumPool

from tradeexecutor.solana.tx import TransactionBuilder
from tradeexecutor.solana.solana_routing_state import SolanaRoutingState
from tradeexecutor.solana.raydium.raydium_swap import swap_with_slippage_protection

from tradeexecutor.state.identifier import TradingPairIdentifier, AssetIdentifier
from tradeexecutor.utils.slippage import get_slippage_in_bps
from tradingstrategy.pair import PandasPairUniverse
from tradingstrategy.types import Percent

logger = logging.getLogger(__name__)



class RaydiumRoutingState(SolanaRoutingState):
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

        pool = RaydiumPool(self.web3, target_pair.pool_address)

        swap_instructions = swap_with_slippage_protection(
            pool,
            payer=self.tx_builder.hot_wallet.private_key,
            amount_in=reserve_amount,
            max_slippage=get_slippage_in_bps(max_slippage),
            pool_fees=[raw_fee]
        )

        # If the receiver (vault) has its own security policy for slippage tolerance check it here
        receiver_slippage_tolerance = self.tx_builder.get_internal_slippage_tolerance()
        if receiver_slippage_tolerance is not None:
            # TODO: TxBuilder configures slippage tolerance as opposite of the trades
            receiver_slippage_tolerance = 1 - receiver_slippage_tolerance
            trade_slippage_tolerance = max_slippage
            assert receiver_slippage_tolerance > trade_slippage_tolerance, f"Receiver (vault) slippage tolerance tighter than the trade slippage tolerance.\nReceiver: {receiver_slippage_tolerance}, trade: {trade_slippage_tolerance}"

        return self.create_signed_transaction(
            swap_instructions,
            self.swap_cu_limit,
            notes=notes,
        )
