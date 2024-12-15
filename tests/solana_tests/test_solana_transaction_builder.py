"""Solana hot wallet transactio builder tests."""

import pytest
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import Transaction
from solders.system_program import create_nonce_account 
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import approve, ApproveParams

from solana_utils import assert_valid_response, OPTS

from tradeexecutor.solana.hot_wallet import HotWallet
from tradeexecutor.solana.tx import HotWalletTransactionBuilder, get_simulation_compute_units


@pytest.fixture()
def nonce_account(client, payer) -> Pubkey:
    """Deploy nonce account.
    """
    account = Keypair()
    nonce_rent = client.get_minimum_balance_for_rent_exemption(80).value

    ixs = create_nonce_account(
        payer.pubkey(),
        account.pubkey(),
        payer.pubkey(),
        nonce_rent
    )
    tx = Transaction.new_with_payer(ixs, payer.pubkey())
    blockhash = client.get_latest_blockhash()
    tx.sign([payer, account], blockhash.value.blockhash)

    resp = client.send_transaction(tx)
    client.confirm_transaction(resp.value)
    return account.pubkey()


@pytest.fixture()
def wallet1(payer) -> HotWallet:
    wallet = HotWallet(payer.pubkey(), payer, None)
    return wallet


@pytest.fixture()
def wallet2(payer, nonce_account) -> HotWallet:
    wallet = HotWallet(payer.pubkey(), payer, nonce_account)
    return wallet


def test_build_token_approve(
    client,
    usdc_token,
    payer,
    payer_usdc_account,
    hot_wallet,
    wallet1,
    wallet2
):
    """Create approve() transaction and execute it."""

    for wallet in [wallet1, wallet2]:

        tx_builder = HotWalletTransactionBuilder(
            client,
            wallet,
        )

        amount_delegated = 500
        approve_params = ApproveParams(
            program_id=TOKEN_PROGRAM_ID,
            source=payer_usdc_account,
            delegate=hot_wallet,
            owner=payer.pubkey(),
            signers=[payer.pubkey()],
            amount=amount_delegated,
        )
        ix = approve(approve_params)

        tx = tx_builder.sign_transaction(
            [ix],
        )

        resp = client.send_raw_transaction(bytes(tx.details), OPTS)
        assert_valid_response(resp)
        client.confirm_transaction(resp.value)

        account_info = usdc_token.get_account_info(payer_usdc_account)
        assert account_info.delegate == hot_wallet
        assert account_info.delegated_amount == amount_delegated


def test_simulation_compute_units(
    client,
    payer,
    payer_usdc_account,
    hot_wallet,
):
    """We can read compute units back from the transaction simulation."""

    approve_params = ApproveParams(
        program_id=TOKEN_PROGRAM_ID,
        source=payer_usdc_account,
        delegate=hot_wallet,
        owner=payer.pubkey(),
        signers=[payer.pubkey()],
        amount=100,
    )
    ix = approve(approve_params)
    cu = get_simulation_compute_units(client, payer, [ix])

    assert cu <= 200_000
