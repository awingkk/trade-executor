"""Sync model for strategies using a single hot wallet."""
import logging
from typing import Optional

from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solana.rpc.api import Client

logger = logging.getLogger(__name__)


class HotWallet:

    # TODO: Multiple Tokens in ONE HotWallet
    def __init__(self, account: Pubkey, key_pair: Keypair):
        """Create a hot wallet from a local account."""
        self.account = account
        self.key_pair = key_pair
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

    def sync_nonce(self, web3: Client):
        """Initialise the current nonce from the on-chain data."""
        # TODO: nonce on Solana
        # self.current_nonce = web3.eth.get_transaction_count(self.account.address)
        self.current_nonce = 0
        logger.info("Synced nonce for %s to %d", self.account, self.current_nonce)
