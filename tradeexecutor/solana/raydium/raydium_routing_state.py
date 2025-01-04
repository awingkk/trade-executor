"""Route trades to different Uniswap v2 like exchanges."""

import os
import logging
from collections import defaultdict
from typing import List, Optional, Tuple
from abc import abstractmethod

from solders.pubkey import Pubkey
from solders.instruction import Instruction
from solana.rpc.api import Client

from soldexpy.raydium_pool import RaydiumPool

from tradeexecutor.solana.tx import TransactionBuilder
from tradeexecutor.solana.balances import fetch_token_details, fetch_balances_by_token_list, convert_to_decimals
from tradeexecutor.solana.raydium.raydium_swap import swap_with_slippage_protection

from tradeexecutor.state.blockhain_transaction import BlockchainTransaction
from tradeexecutor.state.identifier import TradingPairIdentifier, AssetIdentifier
from tradeexecutor.strategy.routing import RoutingState
from tradeexecutor.utils.slippage import get_slippage_in_bps
from tradingstrategy.chain import ChainId
from tradingstrategy.pair import PandasPairUniverse
from tradingstrategy.types import Percent

logger = logging.getLogger(__name__)


class OutOfBalance(Exception):
    """Did not have enough tokens"""


#: How much maximum we can spend on a approve for a router
#:
#:
APPROVE_CU_LIMITS = {
    ChainId.solana.value: 200_000,
}

DEFAULT_APPROVE_CU_LIMIT = 200_000

#: How much maximum we can spend on a swap
#:
SWAP_CU_LIMITS = {
    ChainId.solana.value: 1_400_000,
}

DEFAULT_SWAP_CU_LIMIT = 1_400_000



class RaydiumRoutingState(RoutingState):
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
        """

        :param pair_universe:
            Pairs we trade

        :param tx_builder:
            For creating trade transactions.

            Can be set to None on DummyExecutionModel.

        :param web3:
            Use for routing smart contract reads.

            Given when `tx_builder` is not present.

        :param swap_gas_limit:
            What is the max gas we are willing to pay for a swap.

        """

        # Parent does not need to be called,
        # TODO: fix parent API signature later,
        # because full universe is not needed
        self.pair_universe = pair_universe

        if tx_builder is not None:
            self.tx_builder = tx_builder
            self.web3 = self.tx_builder.web3
            self.chain_id = self.tx_builder.chain_id
        else:
            # DummyExecution model does not have a wallet
            # and cannot build transactions
            self.tx_builder = None
            self.hot_wallet = None
            self.web3 = web3
            self.chain_id = web3.eth.chain_id

        # router -> erc-20 mappings
        self.approved_routes = defaultdict(set)

        # set compute unit limits: 
        # use provided via constructor -> environment variable -> default (chain-specific)
        if approve_cu_limit:
            self.approve_cu_limit = approve_cu_limit
        elif os.environ.get("APPROVE_CU_LIMIT"):
            self.approve_cu_limit = int(os.environ.get("APPROVE_CU_LIMIT"))
        else:
            self.approve_cu_limit = APPROVE_CU_LIMITS.get(self.chain_id, DEFAULT_APPROVE_CU_LIMIT)

        if swap_cu_limit:
            self.swap_cu_limit = swap_cu_limit
        elif os.environ.get("SWAP_CU_LIMIT"):
            self.swap_cu_limit = int(os.environ.get("SWAP_CU_LIMIT"))
        else:
            self.swap_cu_limit = SWAP_CU_LIMITS.get(self.chain_id, DEFAULT_SWAP_CU_LIMIT)
        

    def get_pool_for_pair(self, target_pair: TradingPairIdentifier) -> RaydiumPool:
        """Get a router for a trading pair."""
        assert target_pair.pool_address, f"Pool address missing for {target_pair}"
        pool = RaydiumPool(self.web3, target_pair.pool_address)
        return pool


    def trade_on_router_two_way(self,
            pool: RaydiumPool,
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

    '''
    @abstractmethod
    def trade_on_router_three_way(self,
            uniswap: deployment_types,
            target_pair: TradingPairIdentifier,
            intermediary_pair: TradingPairIdentifier,
            reserve_asset: AssetIdentifier,
            reserve_amount: int,
            max_slippage: float,
            check_balances: False,
            asset_deltas: Optional[List[AssetDelta]] = None,
            notes="",
        ):
        """Prepare the actual swap for three way trade."""

    def is_route_approved(self, router_address: str):
        return router_address in self.approved_routes

    def mark_router_approved(self, token_address, router_address):
        self.approved_routes[router_address].add(token_address)

    def is_approved_on_chain(self, token_address: str, router_address: str) -> bool:
        erc_20 = get_deployed_contract(self.web3, "ERC20MockDecimals.json", token_address)
        # Assume allowance is always infinity
        return erc_20.functions.allowance.call(self.tx_builder.get_token_delivery_address(), router_address) > 0
    '''

    def check_has_enough_tokens(
            self,
            token: AssetIdentifier,
            amount: int,
    ):
        """Check we have enough buy side tokens to do a trade.

        This might not be the case if we are preparing transactions ahead of time and
        sell might have not happened yet.
        """
        address = token.other_data["Pubkey"]
        holding_address = self.tx_builder.get_fees_wallet_address()
        balance = fetch_balances_by_token_list(self.web3, holding_address, [address])[address]
        if balance < amount:
            token_details = fetch_token_details(
                self.web3,
                address,
            )
            decimals = token_details["decimals"]
            d_balance = convert_to_decimals(balance, decimals)
            d_amount = convert_to_decimals(amount, decimals)
            raise OutOfBalance(f"Address {address} does not have enough {str(token)} tokens to trade. Need {d_amount}, has {d_balance}")

    '''
    def ensure_token_approved(
        self,
        token_address: str,
        router_address: str,
        amount: int = 2**256-1,
    ) -> List[BlockchainTransaction]:
        """Make sure we have ERC-20 approve() for the trade

        - Infinite approval on-chain

        - ...or previous approval in this state,

        :param token_address:
        :param router_address:
        :param amount: How much to approve, default to approve infinite amount

        :return: Create 0 or 1 transactions if needs to be approved
        """

        assert self.tx_builder is not None

        if token_address in self.approved_routes[router_address]:
            # Already approved for this cycle in previous trade
            return []

        erc_20 = get_deployed_contract(self.web3, "ERC20MockDecimals.json", Web3.to_checksum_address(token_address))

        # Set internal state we are approved
        self.mark_router_approved(token_address, router_address)

        approve_address = self.tx_builder.get_token_delivery_address()

        if erc_20.functions.allowance(approve_address, router_address).call() > 0:
            # already approved in previous execution cycle
            logger.info("approve() - already approved: ERC-20: %s, router: %s", erc_20.address, router_address)
            return []

        logger.info("approve(), ERC-20: %s, router: %s", erc_20.address, router_address)

        # Create infinite approval
        tx = self.tx_builder.sign_transaction(
            erc_20,
            erc_20.functions.approve(router_address, amount),
            gas_limit=self.approve_gas_limit,
            gas_price_suggestion=None,
            asset_deltas=[],
        )

        return [tx]
    '''


    def create_signed_transaction(
        self,
        ix_list: list[Instruction],
        compute_unit_limit: Optional[int] = None,
        notes="",
    ):
        signed_tx = self.tx_builder.sign_transaction(
            ix_list,
            compute_unit_limit,
            priority_fee_suggestion=None,
            notes=notes,
        )
        return [signed_tx]


    def adjust_spend(
        self,
        asset: AssetIdentifier,
        required_amount: int,
        epsilon: float = 0.00001,
        check_balances: bool = False,
    ) -> int:
        """Check that our on-chain balances have enough tokens to cover the trade.

        This is designed to work with the case that there is one
        position and sell all trade is incoming.
        We do not want this trade to fail because some mishaps
        in the accounting, or at least have a very clear error when it does.

        :raise OutOfBalance:
            We do not have enough tokens to cover the trade

        :param epsilon:
            How much floating point error tolerance is ok.

            Default to 1 BPS.

        :return:
            The actual token amount we can spent on the transaction
        """

        assert required_amount > 0, f"Cannot adjust trade spend. Asset {asset}, required_amount: {required_amount}"

        token_pubkey = asset.other_data["Pubkey"]
        holding_address = self.tx_builder.get_fees_wallet_address()
        on_chain_balance = fetch_balances_by_token_list(self.web3, holding_address, [token_pubkey])[token_pubkey]

        if on_chain_balance < required_amount:
            # Check if we are within epsilon
            if (required_amount - on_chain_balance) / required_amount < epsilon:
                logger.info("Adjusting spending amount to fit to the epsilon. For %s we have on-chain: %d, required: %d", asset, on_chain_balance, required_amount)
                return on_chain_balance
            else:
                if not check_balances:
                    logger.warning("For %s we have on-chain: %d < required: %d, but we skip balance check since check_balances=False", asset, on_chain_balance, required_amount)
                    return required_amount
                
                raise OutOfBalance(
                    f"Not enough tokens for {asset} to perform the trade. Required: {required_amount}, on-chain balance for {holding_address} is {on_chain_balance}."
                )
        else:
            # We are correctly funded
            return required_amount

    '''
    @staticmethod
    def validate_pairs(target_pair, intermediary_pair):
        """Check we can chain two pairs
        """
        assert intermediary_pair.base == target_pair.quote, f"Could not hop from intermediary {intermediary_pair} -> destination {target_pair}"

        assert target_pair.exchange_address, f"Target pair {target_pair} missing exchange information"
        assert intermediary_pair.exchange_address, f"Intermediary pair {intermediary_pair} missing exchange information"

        # Check routing happens on the same exchange
        assert intermediary_pair.exchange_address.lower() == target_pair.exchange_address.lower()
        
    @staticmethod
    def validate_exchange(target_pair, intermediary_pair):
        """Check routing happens on the same exchange"""
        assert intermediary_pair.exchange_address.lower() == target_pair.exchange_address.lower()
            
def route_tokens(
        trading_pair: TradingPairIdentifier,
        intermediate_pair: Optional[TradingPairIdentifier],
)-> Tuple[ChecksumAddress, ChecksumAddress, Optional[ChecksumAddress]]:
    """Convert trading pair route to physical token addresses.
    """

    if intermediate_pair is None:
        return (Web3.to_checksum_address(trading_pair.base.address),
            Web3.to_checksum_address(trading_pair.quote.address),
            None)

    return (Web3.to_checksum_address(trading_pair.base.address),
        Web3.to_checksum_address(intermediate_pair.quote.address),
        Web3.to_checksum_address(trading_pair.quote.address))


def get_base_quote(web3: Client, target_pair: TradingPairIdentifier, reserve_asset: AssetIdentifier, error_msg: str = None):
        """Get base and quote token from the pair and reserve asset. Called in parent class (RoutingState) with error_msg.
        
        See: https://tradingstrategy.ai/docs/programming/market-data/trading-pairs.html
        
        :param target_pair: Pair to be traded
        :param reserver_asset: Asset to be kept as reserves
        :returns: (base_token: Contract, quote_token: Contract)
        :param error_msg:
            Only provide this argument if error message includes external info such as an intermediary pair
        """
        if error_msg is None:
            error_msg = f"Cannot route trade through {target_pair}"
        
        if reserve_asset == target_pair.quote:
            # Buy with e.g. BUSD
            base_token = get_token_for_asset(web3, target_pair.base)
            quote_token = get_token_for_asset(web3, target_pair.quote)
            
        elif reserve_asset == target_pair.base:
            # Sell, flip the direction
            base_token = get_token_for_asset(web3, target_pair.quote)
            quote_token = get_token_for_asset(web3, target_pair.base)
            
        else:
            raise RuntimeError(error_msg)
        
        return base_token, quote_token


def get_base_quote_intermediary(web3: Web3, target_pair: TradingPairIdentifier, intermediary_pair: TradingPairIdentifier, reserve_asset: AssetIdentifier):
        
        if reserve_asset == intermediary_pair.quote:
            # Buy BUSD -> BNB -> Cake
            base_token = get_token_for_asset(web3, target_pair.base)
            quote_token = get_token_for_asset(web3, intermediary_pair.quote)
            intermediary_token = get_token_for_asset(web3, intermediary_pair.base)
        elif reserve_asset == target_pair.base:
            # Sell, Cake -> BNB -> BUSD
            base_token = get_token_for_asset(web3, intermediary_pair.quote)  # BUSD
            quote_token = get_token_for_asset(web3, target_pair.base)  # Cake
            intermediary_token = get_token_for_asset(web3, intermediary_pair.base)  # BNB
        else:
            raise RuntimeError(f"Cannot trade {target_pair} through {intermediary_pair}")
        return base_token,quote_token,intermediary_token

    
def get_token_for_asset(web3: Client, asset: AssetIdentifier) -> Contract:
    """Get ERC-20 contract proxy."""
    erc_20 = get_deployed_contract(web3, "ERC20MockDecimals.json", Web3.to_checksum_address(asset.address))
    return erc_20

'''
