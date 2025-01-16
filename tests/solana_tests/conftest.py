"""Solana deployment fixtures.
"""

import datetime
from typing import List
import base64, base58, json
import shutil, os
import logging

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.types import TxOpts
from solana.rpc.api import Client
from solana.rpc.commitment import Processed, Confirmed
from spl.token.client import Token
from spl.token.constants import TOKEN_PROGRAM_ID

from tradeexecutor.solana.test_validator import TestValidatorLaunch, launch_test_validator
from tradeexecutor.state.identifier import AssetIdentifier

from solana_tests.solana_utils import assert_valid_response


pytestmark = pytest.mark.skipif(
    (os.environ.get("SOLANA_CHAIN_JSON_RPC") is None) or (shutil.which("solana-test-validator") is None),
    reason="Set SOLANA_CHAIN_JSON_RPC env install solana-test-validator command to run these tests",
)

httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)


@pytest.fixture()
def payer() -> Keypair:
    """Deploy account.

    Do some account allocation for tests.
    """
    return Keypair.from_seed(bytes([1] * 32))


@pytest.fixture()
def hot_wallet() -> Pubkey:
    """Deploy account.

    Do some account allocation for tests.
    """
    return Pubkey.from_string("J3dxNj7nDRRqRRXuEMynDG57DkZK4jYRuv3Garmb1i99")


@pytest.fixture
def usdc_asset() -> AssetIdentifier:
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    key = Pubkey.from_string(usdc_mint)
    usdc_hex = "0x" + bytes(key).hex()
    a = AssetIdentifier(102, usdc_hex, "USDC", 6)
    a.other_data["Pubkey"] = key
    return a


@pytest.fixture
def sol_asset() -> AssetIdentifier:
    sol_mint = "So11111111111111111111111111111111111111112"
    key = Pubkey.from_string(sol_mint)
    sol_hex = "0x" + bytes(key).hex()
    a = AssetIdentifier(102, sol_hex, "SOL", 9)
    a.other_data["Pubkey"] = key
    return a


@pytest.fixture()
def usdc_account_file(payer, tmp_path_factory):
    """Change the mint authority of USDC mint account to payer, and save the account to temp file.
    """
    usdc = json.load(open("tests/solana_tests/usdc.json"))
    data = bytearray(base64.b64decode(usdc["account"]["data"][0]))
    data[4:4+32] = base58.b58decode(str(payer.pubkey()))
    usdc["account"]["data"][0] = base64.b64encode(data).decode("utf8")

    fn = str(tmp_path_factory.mktemp("account").joinpath("usdc.json"))
    json.dump(usdc, open(fn, "w"))
    return fn


@pytest.fixture()
def clone_accounts():
    return [
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", # Raydium Liquidity Pool V4

        "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2", # Raydium (SOL-USDC) Pool
        "8BnEgHoWFysVcuFFX7QztDmzuH8r5ZFvyP3sYwn1XTh6", # Raydium (SOL-USDC) Market
        "CKxTHwM9fPMRRvZmFnFoqKNd9pQR21c5Aq9bh5h9oghX", # Openbook (SOL-USDC) Pool 1
        "DQyrAcCrDXQ7NeoqGgDCZwBvWDcYmFCjSb9JtteuvPpz", # Raydium (SOL-USDC) Pool 1
        "HLmqeL62xR1QoZ1HKKbXRrdN1p3phKpxRMb2VVopvBBz", # Raydium (SOL-USDC) Pool 2

        "AVs9TA4nWDzfPJE9gGVNJMVhcQy3V9PGazuz33BfG2RA", # Raydium (RAY-SOL) Pool
        "C6tp2RVZnxBPFbnAsfTjis8BN9tycESAT4SgDQgbbrsA", # Raydium (RAY-SOL) Market
        "6U6U59zmFWrPSzm9sLX7kVkaK78Kz7XJYkrhP1DjF3uF", #
        "Em6rHi68trYgBFyJ5261A2nhwuQWfLcirgzZZYoRcrkX", # Raydium (RAY-SOL) Pool 1
        "3mEFzHsJyu2Cpjrz6zPmTzP7uoLFj9SbbecGVzzkL1mJ", # Raydium (RAY-SOL) Pool 2
        "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", # Ray Mint
    ]

@pytest.fixture()
def clone_programs():
    return [
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", # Raydium Liquidity Pool V4
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter Aggregator v6
    ]


@pytest.fixture()
def validator(payer, hot_wallet, usdc_account_file, clone_accounts, clone_programs, tmp_path_factory) -> TestValidatorLaunch:
    """Set up the Test Validator, and request airdrop to two account.

    Also perform the Test Validator state reset for each test.
    """
    ledger_path = str(tmp_path_factory.getbasetemp().joinpath("test-ledger-trade-execute"))

    validator = launch_test_validator(
        fork_url=os.environ.get("SOLANA_CHAIN_JSON_RPC"),
        ledger=ledger_path,
        account_file=usdc_account_file,
        clone_accounts=clone_accounts,
        clone_programs=clone_programs,
    )
    client = Client("http://127.0.0.1:8899", commitment=Confirmed)

    # airdrop
    airdrop_amount = 1000 * 10**9
    tx = client.request_airdrop(payer.pubkey(), airdrop_amount)
    assert_valid_response(tx)
    client.confirm_transaction(tx.value)
    balance = client.get_balance(payer.pubkey())
    assert_valid_response(balance)
    assert balance.value == airdrop_amount

    tx = client.request_airdrop(hot_wallet, airdrop_amount)
    assert_valid_response(tx)
    client.confirm_transaction(tx.value)
    balance = client.get_balance(hot_wallet)
    assert_valid_response(balance)
    assert balance.value == airdrop_amount

    try:
        yield validator
    finally:
        validator.close()
        shutil.rmtree(ledger_path)


@pytest.fixture()
def client(validator: TestValidatorLaunch) -> Client:
    """Set up the Test Validator client connection.
    """
    client = Client(validator.json_rpc_url, timeout=2, commitment=Confirmed)
    return client


@pytest.fixture()
def usdc_token(client, payer, hot_wallet, usdc_asset) -> Token:
    token = Token(client, usdc_asset.other_data["Pubkey"], TOKEN_PROGRAM_ID, payer)
    mint_info = token.get_mint_info()
    assert mint_info.mint_authority == payer.pubkey()

    usdc_dict = {
        payer.pubkey() : 1000 * 10**6,
        hot_wallet : 0,
    }
    for user, amount in usdc_dict.items():
        # create token account
        acc = token.create_associated_token_account(user)
        acc_info = token.get_account_info(acc)
        assert acc_info.mint == usdc_asset.other_data["Pubkey"]
        assert acc_info.owner == user
        assert acc_info.amount == 0

        if amount > 0:
            # mint USDC
            opts = TxOpts(skip_confirmation=False, preflight_commitment=Processed)
            tx = token.mint_to(acc, payer, amount, opts)
            assert_valid_response(tx)
            client.confirm_transaction(tx.value)
            acc_info = token.get_account_info(acc)
            assert acc_info.amount == amount

    return token


@pytest.fixture()
def payer_usdc_account(usdc_token, payer):
    resp = usdc_token.get_accounts_by_owner(payer.pubkey())
    assert_valid_response(resp)
    assert len(resp.value) > 0
    acc = resp.value[0]
    return acc.pubkey


@pytest.fixture()
def hot_wallet_usdc_account(usdc_token, hot_wallet):
    resp = usdc_token.get_accounts_by_owner(hot_wallet)
    assert_valid_response(resp)
    assert len(resp.value) > 0
    acc = resp.value[0]
    return acc.pubkey


@pytest.fixture
def start_ts() -> datetime.datetime:
    """Timestamp of action started"""
    return datetime.datetime(2022, 1, 1, tzinfo=None)


@pytest.fixture
def supported_reserves(usdc_asset) -> List[AssetIdentifier]:
    """Timestamp of action started"""
    return [usdc_asset]
