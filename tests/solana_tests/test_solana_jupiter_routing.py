"""Solana Raydium routing model tests.

"""

import flaky
import pytest
from decimal import Decimal

from solders.pubkey import Pubkey
from spl.token.client import Token
from spl.token.constants import TOKEN_PROGRAM_ID
from solana_utils import assert_valid_response, OPTS

from tradeexecutor.solana.hot_wallet import HotWallet
from tradeexecutor.solana.tx import HotWalletTransactionBuilder
from tradeexecutor.solana.jupiter.jupiter_routing_state import JupiterRoutingState
from tradeexecutor.solana.solana_routing_model import SolanaRoutingModel
from tradeexecutor.state.identifier import AssetIdentifier, TradingPairIdentifier
from tradeexecutor.strategy.trading_strategy_universe import (
    create_pair_universe_from_code,
)
from tradingstrategy.chain import ChainId
from tradingstrategy.pair import PandasPairUniverse


@pytest.fixture()
def wallet1(payer) -> HotWallet:
    wallet = HotWallet(payer.pubkey(), payer, None)
    return wallet


@pytest.fixture
def ray_asset() -> AssetIdentifier:
    """Mock some assets"""
    ray_mint = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
    key = Pubkey.from_string(ray_mint)
    key_hex = "0x" + bytes(key).hex()
    a = AssetIdentifier(102, key_hex, "RAY", 6)
    a.other_data["Pubkey"] = key
    return a


@pytest.fixture
def sol_usdc_trading_pair(sol_asset, usdc_asset) -> TradingPairIdentifier:
    """sol-usdc pair representation in the trade executor domain."""
    return TradingPairIdentifier(
        sol_asset,
        usdc_asset,
        "???",
        exchange_address="",
        internal_id=1000,  # random number
        internal_exchange_id=1000,  # random number
        fee=0.0001
    )


@pytest.fixture
def ray_usdc_trading_pair(ray_asset, usdc_asset) -> TradingPairIdentifier:
    """ray-usdc pair representation in the trade executor domain."""
    return TradingPairIdentifier(
        ray_asset,
        usdc_asset,
        "???",
        exchange_address="",
        internal_id=1001,  # random number
        internal_exchange_id=1000,  # random number
        fee=0.0025
    )


@pytest.fixture
def ray_sol_trading_pair(ray_asset, sol_asset) -> TradingPairIdentifier:
    """ray-sol pair representation in the trade executor domain."""
    return TradingPairIdentifier(
        ray_asset,
        sol_asset,
        "???",
        exchange_address="",
        internal_id=1002,  # random number
        internal_exchange_id=1000,  # random number
        fee=0.0025
    )


@pytest.fixture
def pair_universe(
    sol_usdc_trading_pair, ray_usdc_trading_pair, ray_sol_trading_pair
) -> PandasPairUniverse:
    """Pair universe needed for the trade routing."""
    return create_pair_universe_from_code(
        ChainId.solana,
        [sol_usdc_trading_pair, ray_usdc_trading_pair, ray_sol_trading_pair],
    )


@pytest.fixture()
def routing_model(usdc_asset):
    allowed_intermediary_pairs = {
    }

    return SolanaRoutingModel(
        allowed_intermediary_pairs,
        reserve_token_address=usdc_asset.address,
    )


# Flaky because Ganache hangs
@flaky.flaky()
def test_jupiter_routing_one_leg(
    client,
    wallet1,
    sol_asset,
    ray_asset,
    payer,
    routing_model,
    ray_sol_trading_pair,
    pair_universe,
):
    """Make 1x two way trade SOL -> RAY.

    - Buy RAY with SOL
    """

    sol_amount = sol_asset.convert_to_raw_amount(Decimal(20))
    sol_account = Token.create_wrapped_native_account(
        client, 
        TOKEN_PROGRAM_ID, 
        payer.pubkey(), 
        payer, 
        sol_amount,
    )
    sol_token = Token(client, sol_asset.other_data["Pubkey"], TOKEN_PROGRAM_ID, payer)
    acc_info = sol_token.get_account_info(sol_account)
    assert acc_info.amount == sol_amount

    # Prepare a transaction builder
    tx_builder = HotWalletTransactionBuilder(
        client,
        wallet1,
    )

    # Create
    routing_state = JupiterRoutingState(pair_universe, tx_builder=tx_builder)
    routing_state.set_dex_routes(["Raydium"])

    txs = routing_model.trade(
        routing_state,
        ray_sol_trading_pair,
        sol_asset,
        10,  # Buy RAY worth of 10 SOL,
        check_balances=True,
    )

    # We should have 1 swap
    assert len(txs) == 1

    resp = client.send_raw_transaction(bytes(txs[0].details), OPTS)
    assert_valid_response(resp)
    client.confirm_transaction(resp.value)

    ray_token = Token(client, ray_asset.other_data["Pubkey"], TOKEN_PROGRAM_ID, payer)

    resp = ray_token.get_accounts_by_owner(payer.pubkey())
    assert_valid_response(resp)
    assert len(resp.value) > 0
    ray_acc = resp.value[0]

    resp = ray_token.get_balance(ray_acc.pubkey)
    assert_valid_response(resp)

    assert int(resp.value.amount) > 0
