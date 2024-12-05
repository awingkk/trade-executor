"""Deposit and withdraw detection and management."""

import logging
import datetime
from decimal import Decimal
from typing import Dict, List

from solders.pubkey import Pubkey
from solana.rpc.api import Client

from tradeexecutor.solana.balances import DecimalisedHolding, \
    fetch_balances_by_token_list, convert_balances_to_decimal

# TODO: Move to Non-ETH package
from tradeexecutor.ethereum.wallet import ReserveUpdateEvent

from tradeexecutor.state.reserve import ReservePosition
from tradeexecutor.state.identifier import AssetIdentifier

logger = logging.getLogger(__name__)


def update_wallet_balances(
    client: Client,
    address: Pubkey,
    tokens: List[Pubkey],
) -> Dict[Pubkey, DecimalisedHolding]:
    """Get raw balances of tokens."""

    balances = fetch_balances_by_token_list(client, address, tokens)
    return convert_balances_to_decimal(client, balances)


def sync_reserves(
    client: Client,
    clock: datetime.datetime,
    wallet_address: Pubkey,
    current_reserves: List[ReservePosition],
    supported_reserve_currencies: List[AssetIdentifier],
) -> List[ReserveUpdateEvent]:
    """Check the address for any incoming stablecoin transfers to see how much cash we have."""

    assert supported_reserve_currencies, f"Supported reserve currency address empty when syncing: {wallet_address}"

    # Get raw holdings of the address
    balances = update_wallet_balances(
        client,
        wallet_address,
        [a.other_data["Pubkey"] for a in supported_reserve_currencies],
    )

    reserves_per_token = {r.asset.address.lower(): r for r in current_reserves}

    events: List[ReserveUpdateEvent] = []

    for currency in supported_reserve_currencies:

        pubkey = currency.other_data["Pubkey"]
        address = "0x" + bytes(pubkey).hex()

        if address in reserves_per_token:
            # We have an existing record of having this reserve
            current_value = reserves_per_token[address].quantity
        else:
            current_value = Decimal(0)

        decimal_holding = balances.get(pubkey)

        # We get decimals = None if Ganache is acting
        assert decimal_holding.decimals, f"Token did not have decimals: token:{currency} holding:{decimal_holding}"

        if (decimal_holding is not None) and (decimal_holding.value != current_value):
            evt = ReserveUpdateEvent(
                asset=currency,
                past_balance=current_value,
                new_balance=decimal_holding.value,
                updated_at=clock,
                mined_at=clock,  # TODO: We do not have logic to get actual block_mined_at of Transfer() here
                block_number=None,
            )
            events.append(evt)
            logger.info("Reserve currency update detected. Asset: %s, past: %s, new: %s", evt.asset, evt.past_balance, evt.new_balance)

    return events
