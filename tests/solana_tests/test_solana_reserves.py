"""Sync reserves from on-chain data."""

from decimal import Decimal
from typing import List

import pytest
from solders.pubkey import Pubkey
from solana.rpc.types import TxOpts
from solana.rpc.commitment import Processed, Confirmed

from tradeexecutor.solana.wallet import sync_reserves

# TODO: Move to Non-ETH package
from tradeexecutor.ethereum.balance_update import apply_reserve_update_events

from tradeexecutor.state.state import State


def test_update_reserves_empty(client, start_ts, hot_wallet, supported_reserves):
    """Syncing empty reserves does not cause errors."""
    events = sync_reserves(client, start_ts, hot_wallet, [], supported_reserves)
    assert len(events) == 0


def test_update_reserves_one_deposit(client, usdc_token, payer, payer_usdc_account, start_ts, hot_wallet, hot_wallet_usdc_account, supported_reserves):
    """Sync reserves from one deposit."""

    # Deposit 10 usd
    tx = usdc_token.transfer(
        source=payer_usdc_account,
        dest=hot_wallet_usdc_account,
        owner=payer,
        amount=10 * 10**6,
        opts=TxOpts(skip_confirmation=False, preflight_commitment=Processed),
    )
    client.confirm_transaction(tx.value)
    events = sync_reserves(client, start_ts, hot_wallet, [], supported_reserves)
    assert len(events) == 1

    evt = events[0]
    assert evt.updated_at == start_ts
    assert evt.new_balance == 10
    assert evt.past_balance == 0


def test_update_reserves_no_change(client, usdc_token, payer, payer_usdc_account, start_ts, hot_wallet, hot_wallet_usdc_account, supported_reserves):
    """Do not generate deposit events if there has not been changes."""

    state = State()
    portfolio = state.portfolio

    # Deposit 10 usd
    tx = usdc_token.transfer(
        source=payer_usdc_account,
        dest=hot_wallet_usdc_account,
        owner=payer,
        amount=10 * 10**6,
        opts=TxOpts(skip_confirmation=False, preflight_commitment=Processed),
    )
    client.confirm_transaction(tx.value)
    events = sync_reserves(client, start_ts, hot_wallet, [], supported_reserves)
    assert len(events) == 1

    apply_reserve_update_events(state, events)

    events = sync_reserves(client, start_ts, hot_wallet, portfolio.reserves.values(), supported_reserves)
    assert len(events) == 0


def test_update_reserves_twice(client, usdc_token, payer, payer_usdc_account, start_ts, hot_wallet, hot_wallet_usdc_account, supported_reserves):
    """Sync reserves from one deposit."""

    state = State()
    portfolio = state.portfolio

    # Deposit 10 usd
    tx = usdc_token.transfer(
        source=payer_usdc_account,
        dest=hot_wallet_usdc_account,
        owner=payer,
        amount=10 * 10**6,
        opts=TxOpts(skip_confirmation=False, preflight_commitment=Processed),
    )
    client.confirm_transaction(tx.value)
    events = sync_reserves(client, start_ts, hot_wallet, [], supported_reserves)
    assert len(events) == 1

    apply_reserve_update_events(state, events)

    address = f"102-0x{bytes(usdc_token.pubkey).hex()}"

    assert portfolio.reserves[address].quantity == Decimal(10)
    assert portfolio.reserves[address].reserve_token_price == 1.0

    # Deposit 5 usd more
    tx = usdc_token.transfer(
        source=payer_usdc_account,
        dest=hot_wallet_usdc_account,
        owner=payer,
        amount=5 * 10**6,
        opts=TxOpts(skip_confirmation=False, preflight_commitment=Processed),
    )
    client.confirm_transaction(tx.value)
    events = sync_reserves(client, start_ts, hot_wallet, portfolio.reserves.values(), supported_reserves)
    assert len(events) == 1

    evt = events[0]
    assert evt.updated_at == start_ts
    assert evt.new_balance == 15
    assert evt.past_balance == 10

    apply_reserve_update_events(state, events)

    assert portfolio.reserves[address].quantity == Decimal(15)
