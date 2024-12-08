"""Sync model for strategies using a single hot wallet."""
import datetime
import logging
from decimal import Decimal
from types import NoneType
from typing import List, Optional, Iterable

from tradeexecutor.state.balance_update import BalanceUpdate
from tradeexecutor.state.types import BlockNumber, USDollarPrice
from tradingstrategy.chain import ChainId

from tradeexecutor.state.identifier import AssetIdentifier
from tradeexecutor.state.state import State

from solders.pubkey import Pubkey
from solana.rpc.api import Client
from tradeexecutor.solana.wallet import sync_reserves
from tradeexecutor.solana.balances import fetch_balances_by_token_list
from tradeexecutor.solana.hot_wallet import HotWallet

from tradeexecutor.strategy.sync_model import SyncModel, OnChainBalance

# TODO: Move to Non-ETH package
from web3.types import BlockIdentifier
from tradeexecutor.ethereum.balance_update import apply_reserve_update_events
from tradeexecutor.ethereum.tx import HotWalletTransactionBuilder


logger = logging.getLogger(__name__)


class HotWalletSyncModel(SyncModel):
    """Solana sync model.

    """

    def __init__(self, web3: Client, hot_wallet: HotWallet):
        self.web3 = web3
        self.hot_wallet = hot_wallet

    def init(self):
        self.hot_wallet.sync_nonce(self.web3)

    def get_hot_wallet(self) -> Optional[HotWallet]:
        return self.hot_wallet

    def get_token_storage_address(self) -> Optional[str]:
        return self.hot_wallet.address

    def get_main_address(self) -> Optional[Pubkey]:
        return self.hot_wallet.address

    def resync_nonce(self):
        self.hot_wallet.sync_nonce(self.web3)

    def create_transaction_builder(self) -> HotWalletTransactionBuilder:
        return HotWalletTransactionBuilder(self.web3, self.hot_wallet)


    def sync_initial(
        self, state: State,
        reserve_asset: AssetIdentifier | None = None,
        reserve_token_price: USDollarPrice | None = None,
        **kwargs,
    ):
        """Set up the initial sync details.

        - Initialise vault deployment information

        - Set the reserve assets (optional, sometimes can be read from the chain)

        """
        deployment = state.sync.deployment
        deployment.chain_id = ChainId(ChainId.solana)
        deployment.address = str(self.get_main_address())
        deployment.block_number = 0
        deployment.tx_hash = None
        deployment.block_mined_at = datetime.datetime.utcnow()
        deployment.vault_token_name = None
        deployment.vault_token_symbol = None
        deployment.initialised_at = datetime.datetime.utcnow()

        if reserve_asset:
            position = state.portfolio.initialise_reserves(reserve_asset)
            position.last_pricing_at = datetime.datetime.utcnow()
            position.reserve_token_price = reserve_token_price

        logger.info(
            "HotWallet sync model initialised, reserve asset is is %s, price is %s",
            reserve_asset,
            reserve_token_price,
        )


    def sync_treasury(
        self,
        strategy_cycle_ts: datetime.datetime,
        state: State,
        supported_reserves: Optional[List[AssetIdentifier]] = None,
        end_block: BlockNumber | NoneType = None,
    ) -> List[BalanceUpdate]:
        """Poll chain for updated treasury token balances.

        - Apply the balance sync before each strategy cycle.

        TODO: end_block is being ignored
        """

        if supported_reserves is None:
            supported_reserves = [p.asset for p in state.portfolio.reserves.values()]

        # TODO: This code is not production ready - use with care
        # Needs legacy cleanup
        address = self.get_token_storage_address()
        logger.info("HotWalletSyncModel treasury sync starting for token hold address %s", address)
        assert address, f"Token storage address is None on {self}"
        current_reserves = list(state.portfolio.reserves.values())
        events = sync_reserves(
            self.web3,
            strategy_cycle_ts,
            address,
            current_reserves,
            supported_reserves
        )

        # Map ReserveUpdateEvent (internal transitory) to BalanceUpdate events (persistent)
        balance_update_events = apply_reserve_update_events(state, events)

        treasury = state.sync.treasury
        treasury.last_updated_at = datetime.datetime.utcnow()
        treasury.last_cycle_at = strategy_cycle_ts
        treasury.last_block_scanned = None

        logger.info(f"Chain polling sync done, got {len(events)} events")

        return balance_update_events


    def setup_all(self, state: State, supported_reserves: List[AssetIdentifier]):
        """Make sure we have everything set up and initial test balance synced.
        
        A shortcut used in testing.
        """
        self.init()
        self.sync_initial(state)
        self.sync_treasury(datetime.datetime.utcnow(), state, supported_reserves)


    def fetch_onchain_balances(
        self,
        assets: List[AssetIdentifier],
        filter_zero=True,
        block_identifier: BlockIdentifier = None,
    ) -> Iterable[OnChainBalance]:

        timestamp = datetime.datetime.utcnow()

        token_addresses = [asset.other_data["Pubkey"] for asset in assets]

        balances = fetch_balances_by_token_list(
            self.web3,
            self.get_token_storage_address(),
            token_addresses,
            decimalise=True,
        )

        for asset in assets:
            amount = balances[asset.other_data["Pubkey"]]

            if filter_zero and amount == 0:
                continue

            yield OnChainBalance(
                block_number=None,
                timestamp=timestamp,
                asset=asset,
                amount=amount,
            )


    def create_transaction_builder(self) -> HotWalletTransactionBuilder:
        # return HotWalletTransactionBuilder(self.web3, self.hot_wallet)
        return None
