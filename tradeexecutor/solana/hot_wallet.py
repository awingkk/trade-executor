"""Sync model for strategies using a single hot wallet."""
import logging
from typing import Optional

from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.hash import Hash
from solders.instruction import Instruction
from solders.message import Message
from solders.transaction import Transaction 
from solana.rpc.api import Client

logger = logging.getLogger(__name__)


class HotWallet:

    # TODO: Multiple Tokens in ONE HotWallet
    def __init__(self, account: Pubkey, key_pair: Keypair, nonce_account: Optional[Pubkey] = None):
        """Create a hot wallet from a local account."""
        self.account = account
        self.key_pair = key_pair
        self.nonce_account = nonce_account
        self.current_nonce: Optional[int] = None

    def __repr__(self):
        return f"<Hot wallet {str(self.account)}>"

    @property
    def address(self) -> Pubkey:
        """Solana address of the wallet."""
        return self.account

    @property
    def private_key(self) -> Keypair:
        """The private key."""
        return self.key_pair

    def get_token_account(self, token) -> Pubkey:
        return self.address


    def sign_transaction_with_new_nonce(self, tx: list[Instruction], web3: Client) -> Transaction:
        """Signs a transaction and allocates a nonce for it.
        :param tx:
            Solana instructions as a list.

        :return:
            A transaction payload and nonce with used to generate this transaction.
        """
        if self.nonce_account is None:
            return None
        info = web3.get_account_info_json_parsed(self.nonce_account)
        data = info.value.data
        blockhash = data.parsed["info"]["blockhash"]
        msg = Message.new_with_nonce(tx, self.account, self.nonce_account, self.account)
        signed = Transaction([self.key_pair], msg, Hash.from_string(blockhash))
        return signed


    def sign_transaction_with_blockhash(self, tx: list[Instruction], blockhash: Hash) -> Transaction:
        """Signs a list of instructions with blockhash
        :param tx:
            Solana instructions as a list.

        :return:
            A transaction payload to generate this transaction.
        """
        msg = Message.new_with_blockhash(tx, self.account, blockhash)
        signed = Transaction([self.key_pair], msg, blockhash)
        return signed


    def get_native_currency_balance(self, web3: Client) -> int:
        """Get the balance of the native currency (SOL) of the wallet.

        Useful to check if you have enough cryptocurrency for the gas fees.
        """
        balance = web3.get_balance(self.account)
        return balance.value
