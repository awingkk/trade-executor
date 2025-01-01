"""Hot wallet based synching tests."""
import datetime

import pytest
from tradingstrategy.chain import ChainId

from solders.pubkey import Pubkey
from solders.keypair import Keypair
from spl.token.client import Token
from solana.rpc.types import TxOpts
from solana.rpc.api import Client
from solana.rpc.commitment import Processed
from tradeexecutor.solana.hot_wallet_sync_model import HotWallet, HotWalletSyncModel

from tradeexecutor.state.identifier import AssetIdentifier
from tradeexecutor.state.state import State


@pytest.fixture
def hot_wallet_obj(client, hot_wallet, payer) -> HotWallet:
    """Create hot wallet for the signing tests.

    Top is up with some gas money and 500 USDC.
    """
    wallet = HotWallet(hot_wallet, payer)
    return wallet


def test_hot_wallet_sync_model_init(client, hot_wallet_obj) -> None:
    """Set up strategy sync mode on init."""

    sync_model = HotWalletSyncModel(client, hot_wallet_obj)
    state = State()
    sync_model.sync_initial(state)

    deployment = state.sync.deployment
    assert deployment.address == str(hot_wallet_obj.address)
    assert deployment.block_number == 0
    assert deployment.tx_hash is None
    assert deployment.block_mined_at is not None
    assert deployment.vault_token_symbol is None
    assert deployment.vault_token_name is None
    assert deployment.chain_id == ChainId.solana


def test_hot_wallet_sync_model_deposit(
    client: Client,
    hot_wallet_obj: HotWallet,
    hot_wallet_usdc_account: Pubkey,
    usdc_asset: AssetIdentifier,
    usdc_token: Token,
    payer: Keypair,
    payer_usdc_account: Pubkey,
):
    """Update reserve balances."""

    sync_model = HotWalletSyncModel(client, hot_wallet_obj)
    state = State()
    sync_model.sync_initial(state)

    supported_reserves = [usdc_asset]

    sync_model.sync_treasury(
        datetime.datetime.utcnow(),
        state,
        supported_reserves
    )

    assert len(state.portfolio.reserves) == 0

    tx = usdc_token.transfer(
        source=payer_usdc_account,
        dest=hot_wallet_usdc_account,
        owner=payer,
        amount=10 * 10**6,
        opts=TxOpts(skip_confirmation=False, preflight_commitment=Processed),
    )
    client.confirm_transaction(tx.value)

    sync_model.sync_treasury(
        datetime.datetime.utcnow(),
        state,
        supported_reserves
    )

    assert len(state.portfolio.reserves) == 1

    assert state.portfolio.get_total_equity() == 10

    balances = list(sync_model.fetch_onchain_balances(supported_reserves))
    assert len(balances) == 1
    assert balances[0].asset.token_symbol == "USDC"
    assert balances[0].amount == 10


def test_hot_wallet_sync_model_sync_twice(
    client: Client,
    hot_wallet_obj: HotWallet,
    hot_wallet_usdc_account: Pubkey,
    usdc_asset: AssetIdentifier,
    usdc_token: Token,
    payer: Keypair,
    payer_usdc_account: Pubkey
):
    """Check that we do not generate extra events."""

    sync_model = HotWalletSyncModel(client, hot_wallet_obj)
    state = State()
    sync_model.sync_initial(state)

    supported_reserves = [usdc_asset]

    sync_model.sync_treasury(
        datetime.datetime.utcnow(),
        state,
        supported_reserves
    )

    assert len(state.portfolio.reserves) == 0

    tx = usdc_token.transfer(
        source=payer_usdc_account,
        dest=hot_wallet_usdc_account,
        owner=payer,
        amount=5 * 10**6,
        opts=TxOpts(skip_confirmation=False, preflight_commitment=Processed),
    )
    client.confirm_transaction(tx.value)

    sync_model.sync_treasury(
        datetime.datetime.utcnow(),
        state,
        supported_reserves
    )

    assert len(state.portfolio.reserves) == 1

    reserve_position = state.portfolio.get_default_reserve_position()
    assert len(reserve_position.balance_updates) == 1

    sync_model.sync_treasury(
        datetime.datetime.utcnow(),
        state,
        supported_reserves
    )

    assert len(reserve_position.balance_updates) == 1
