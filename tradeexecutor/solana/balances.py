"""Token holding and portfolio for pubkeys."""
import logging
from decimal import Decimal
from typing import Dict, Set

from solders.pubkey import Pubkey
from solana.rpc.types import TokenAccountOpts
from solana.rpc.api import Client

# TODO: Move to Non-ETH package
from eth_defi.balances import DecimalisedHolding

logger = logging.getLogger(__name__)


def fetch_token_details(
    client: Client,
    token: Pubkey
) -> Dict[str, str]:
    resp = client.get_account_info_json_parsed(token).value
    info = resp.data.parsed["info"]
    return info


def convert_to_decimals(amount, decimals):
    return Decimal(amount) / Decimal(10**decimals)    


def fetch_balances_by_token_list(
    client: Client,
    owner: Pubkey,
    tokens: Set[Pubkey | str],
    decimalise=False,
) -> Dict[Pubkey | str, int | Decimal]:
    """Get all current holdings of an account for a limited set of tokens.

    :param tokens:
        Token list

    :param decimalise:
        If ``True``, convert output amounts to humanised format in Python :py:class:`Decimal`.
    """

    balances = {}
    for pubkey in tokens:
        opts = TokenAccountOpts(mint=pubkey)
        resp = client.get_token_accounts_by_owner_json_parsed(owner, opts)
        accounts = resp.value
        if len(accounts) == 0:
            raw_amount = 0
        else:
            data = accounts[0].account.data.parsed
            raw_amount = int(data["info"]["tokenAmount"]["amount"])
        if decimalise:
            info = fetch_token_details(client, pubkey)
            balances[pubkey] = convert_to_decimals(raw_amount, info["decimals"])
        else:
            balances[pubkey] = raw_amount

    return balances


def convert_balances_to_decimal(
    client,
    raw_balances: Dict[Pubkey | str, int],
    require_decimals=True,
) -> Dict[Pubkey, DecimalisedHolding]:
    """Convert mapping of token holdings to decimals.

    Issues a JSON-RPC call to fetch data for each token in the input dictionary.

    Example:

    .. code-block:: python

        raw_balances = fetch_balances_by_token_list(client, pubkey, tokens)
        return convert_balances_to_decimal(client, raw_balances)

    :param raw_balances:
        Token Pubkey -> uint256 mappings

    :param require_decimals:
        Safety check to ensure tokens have valid decimals set.
        Prevents some wrong pubkeys and broken tokens.

    :return: Token Pubkey -> `DecimalisedHolding` mappings
    """

    res = {}

    for pubkey, raw_balance in raw_balances.items():
        info = fetch_token_details(client, pubkey)
        decimals = info["decimals"]

        if require_decimals:
            assert decimals > 0, f"Token decimals did not return a good value: {pubkey}"

        res[pubkey] = DecimalisedHolding(convert_to_decimals(raw_balance, decimals), decimals, None)

    return res
