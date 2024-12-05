"""Solana deployment fixtures.
"""

import datetime
from typing import List
import base64, base58, json
import shutil

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
def usdc() -> AssetIdentifier:
    """Mock some assets"""
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    key = Pubkey.from_string(usdc_mint)
    usdc_hex = "0x" + bytes(key).hex()
    a = AssetIdentifier(102, usdc_hex, "USDC", 6)
    a.other_data["Pubkey"] = key
    return a


@pytest.fixture
def sol() -> AssetIdentifier:
    """Mock some assets"""
    sol_mint = "So11111111111111111111111111111111111111112"
    key = Pubkey.from_string(sol_mint)
    sol_hex = "0x" + bytes(key).hex()
    a = AssetIdentifier(102, sol_hex, "SOL", 18)
    a.other_data["Pubkey"] = key
    return a


@pytest.fixture()
def usdc_account_file(payer, tmp_path_factory):
    """Change the mint authority of USDC mint account to payer, and save the account to temp file.
    """
    usdc = json.load(open("tests/solana/usdc.json"))
    data = bytearray(base64.b64decode(usdc["account"]["data"][0]))
    data[4:4+32] = base58.b58decode(str(payer.pubkey()))
    usdc["account"]["data"][0] = base64.b64encode(data).decode("utf8")

    fn = str(tmp_path_factory.mktemp("account").joinpath("usdc.json"))
    json.dump(usdc, open(fn, "w"))
    return fn


@pytest.fixture()
def validator(payer, hot_wallet, usdc_account_file, tmp_path_factory) -> TestValidatorLaunch:
    """Set up the Test Validator, and request airdrop to two account.

    Also perform the Test Validator state reset for each test.
    """
    ledger_path = str(tmp_path_factory.getbasetemp().joinpath("test-ledger-trade-execute"))

    validator = launch_test_validator(
        fork_url="https://solana-mainnet.g.alchemy.com/v2/Re6f1F37EmnRzugFxQK-k0-aRzdLx5bE",
        ledger=ledger_path,
        account_file=usdc_account_file,
    )
    client = Client("http://127.0.0.1:8899", commitment=Confirmed)

    # airdrop
    tx = client.request_airdrop(payer.pubkey(), 1_000_000_000)
    client.confirm_transaction(tx.value)
    balance = client.get_balance(payer.pubkey())
    assert balance.value == 1_000_000_000

    tx = client.request_airdrop(hot_wallet, 1_000_000_000)
    client.confirm_transaction(tx.value)
    balance = client.get_balance(hot_wallet)
    assert balance.value == 1_000_000_000

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
def usdc_token(client, payer, hot_wallet, usdc) -> Token:
    token = Token(client, usdc.other_data["Pubkey"], TOKEN_PROGRAM_ID, payer)
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
        assert acc_info.mint == usdc.other_data["Pubkey"]
        assert acc_info.owner == user
        assert acc_info.amount == 0

        if amount > 0:
            # mint USDC
            opts = TxOpts(skip_confirmation=False, preflight_commitment=Processed)
            tx = token.mint_to(acc, payer, amount, opts)
            client.confirm_transaction(tx.value)
            acc_info = token.get_account_info(acc)
            assert acc_info.amount == amount

    return token


@pytest.fixture()
def payer_usdc_account(usdc_token, payer):
    resp = usdc_token.get_accounts_by_owner(payer.pubkey())
    assert len(resp.value) > 0
    acc = resp.value[0]
    return acc.pubkey


@pytest.fixture()
def hot_wallet_usdc_account(usdc_token, hot_wallet):
    resp = usdc_token.get_accounts_by_owner(hot_wallet)
    assert len(resp.value) > 0
    acc = resp.value[0]
    return acc.pubkey


@pytest.fixture
def start_ts() -> datetime.datetime:
    """Timestamp of action started"""
    return datetime.datetime(2022, 1, 1, tzinfo=None)


@pytest.fixture
def supported_reserves(usdc) -> List[AssetIdentifier]:
    """Timestamp of action started"""
    return [usdc]
