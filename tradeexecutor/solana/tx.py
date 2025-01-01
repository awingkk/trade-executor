"""Solana transaction construction.

- Base class :py:class:`TransactionBuilder` for different transaction building mechanisms

- The default :py:class:`HotWalletTransactionBuilder`
"""
import datetime
import logging
from decimal import Decimal
from abc import abstractmethod, ABC
from typing import List, Optional
from time import time, sleep

from hexbytes import HexBytes

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import Instruction
from solders.transaction import Transaction
from solders.message import Message
from solders.commitment_config import CommitmentLevel
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.rpc.requests import SendVersionedTransaction
from solders.rpc.responses import SendTransactionResp
from solana.rpc.api import Client

from tradeexecutor.solana.hot_wallet import HotWallet

from tradeexecutor.state.blockhain_transaction import BlockchainTransaction
from tradeexecutor.state.pickle_over_json import encode_pickle_over_json
from tradeexecutor.state.types import Percent
from tradingstrategy.chain import ChainId

logger = logging.getLogger(__name__)



def get_simulation_compute_units(web3: Client, signer: Keypair, ix_list: list[Instruction]) -> int:
    blockhash = web3.get_latest_blockhash().value.blockhash
    tx = Transaction.new_signed_with_payer(ix_list, signer.pubkey(), [signer], blockhash)
    resp = web3.simulate_transaction(tx)
    return resp.value.units_consumed


def get_fee_estimate(web3: Client, ix_list: list[Instruction]) -> int:
    msg = Message(ix_list)
    resp = web3.get_fee_for_message(msg)
    # getRecentPrioritizationFees
    # print(resp)
    return resp.value


class TransactionBuilder(ABC):
    """Base class for different transaction builders.

    We can build different transactions depending if we execute them directly from the hot wallet,
    or through a smart contract:

    - Hot wallet based, see :py:class:`HotWalletTransactionBuilder`

    - Vault based, see :py:class:`tradeexecutor.enzyme.tx.EnzymeTransactionBuilder`

    Life cycle:

    - TransactionBuilder is created once at the application startup

    """

    def __init__(self, web3: Client):
        self.web3 = web3
        self.chain_id = ChainId.solana

    def get_internal_slippage_tolerance(self) -> Percent | None:
        """Get the slippage tolerance configured for the asset receiver.

        - Vaults have their own security rules against slippage tolerance

        - Any vault slippage tolerance must be higher than trade slippage tolerance

        :return:
            E.g. 0.9995 for 5 BPS slippage tolerance
        """
        return None

    # TODO : Untested
    def fetch_fee_suggestion(self, ix_list: list[Instruction]) -> int:
        """Calculate the suggested fee based on a policy."""
        return get_fee_estimate(self.web3, ix_list)

    # TODO : Untested
    def broadcast(self, tx: BlockchainTransaction) -> HexBytes:
        """Broadcast the transaction.

        Push the transaction to the peer-to-peer network / MEV relay
        to be includedin the

        Sets the `tx.broadcasted_at` timestamp.

        :return:
            Transaction hash, or `tx_hash`
        """
        signed_tx = self.serialise_to_broadcast_format(tx)
        tx.broadcasted_at = datetime.datetime.utcnow()
        return self.web3.send_transaction(signed_tx)

    # TODO : Untested
    @staticmethod
    def serialise_to_broadcast_format(tx: "BlockchainTransaction") -> Transaction:
        """Prepare a transaction as a format ready to broadcast.

        - We pass this as Transaction
          as the logging output will have more information to
          diagnose broadcasting issues
        """
        signed_tx = tx.get_tx_object()
        return signed_tx

    # TODO : Untested
    @staticmethod
    def decode_signed_bytes(tx: "BlockchainTransaction") -> Transaction:
        """Get raw transaction data out from the signed tx bytes."""
        return Transaction.from_byte(tx.signed_bytes)

    # TODO : Untested
    @staticmethod
    def broadcast_and_wait_transactions_to_complete(
            web3: Client,
            txs: List[BlockchainTransaction],
            confirmation_block_count=0,
            max_timeout=datetime.timedelta(seconds=90),
            poll_delay=datetime.timedelta(seconds=1),
            revert_reasons=False,
    ):
        """Watch multiple transactions executed at parallel.

        Modifies the given transaction objects in-place
        and updates block inclusion and succeed status.

        .. note ::

            This method is designed to be used only in unit testing
            as a shortcut.

        """

        # Log what we are doing
        for tx in txs:
            logger.info("Broadcasting and executing transaction %s", tx)

        logger.info("Waiting %d txs to confirm", len(txs))
        assert isinstance(confirmation_block_count, int)

        signed_txs = [SendVersionedTransaction(HotWalletTransactionBuilder.serialise_to_broadcast_format(t)) for t in txs]

        now_ = datetime.datetime.utcnow()
        for tx in txs:
            tx.broadcasted_at = now_

        reqs = tuple(signed_txs)
        parsers = tuple([SendTransactionResp] * len(signed_txs))
        resps = web3._provider.make_batch_request(  # pylint: disable=protected-access
            reqs, parsers
        )

        # tx sig -> BlockchainTransaction map
        for i in range(len(txs)):
            tx_sigs_map = {resps[i].value : txs[i]}

        now_ = datetime.datetime.utcnow()
        timeout = time() + max_timeout
        commitment = CommitmentLevel.Confirmed
        commitment_rank = int(commitment)
        sigs = [resp.value for resp in resps]

        # Update persistant status of transactions
        # based on the result read from the chain
        while len(sigs) > 0 and time() < timeout:
            resps = web3.get_signature_statuses(sigs)
            sigs_new = []
            for i in range(len(sigs)):
                sig = sigs[i]
                rv = resps.value[i]

                if rv is None or rv.confirmation_status is None or int(rv.confirmation_status) < commitment_rank:
                    sigs_new.append(sig)
                    continue

                status = rv.status
                reason = None
                if not status.err:
                    if revert_reasons:
                        # reason = fetch_transaction_revert_reason(web3, tx_hash)
                        pass
                tx = tx_sigs_map[sig]
                tx.set_confirmation_information(
                    now_,
                    status.slot,
                    "", # receipt["blockHash"].hex(),
                    0, # receipt.get("effectiveGasPrice", 0),
                    0, # receipt["gasUsed"],
                    status.err is None,
                    reason
                )

            sigs = sigs_new
            sleep(poll_delay)


    @abstractmethod
    def init(self):
        """Initialise the transaction builder.

        Called on application startup.
        """

    @abstractmethod
    def sign_transaction(
            self,
            ix_list: list[Instruction],
            compute_unit_limit: Optional[int] = None,
            priority_fee_suggestion: Optional[int] = None,
            notes: str = "",
    ) -> BlockchainTransaction:
        """Createa a signed tranaction and set up tx broadcast parameters.

        :param ix_list:
            A list of Solana Instructions.

        :param compute_unit_limit:
            Max compute unit limit per this transaction.

            The transaction will fail if the compute unit limit is exceeded.

            If set to `None` then it is up to the signed to figure it out
            based on the function hints.

        :param priority_fee_suggestion:
            What priority fee will be used.

        :return:
            Prepared BlockchainTransaction instance.

            This transaction object can be stored in the persistent state.
        """

    @abstractmethod
    def get_token_delivery_address(self) -> Pubkey:
        """Get the target address for token delivery.

        Where do Dex should send the tokens after a swap.
        """

    @abstractmethod
    def get_token_account(self, token_pubkey: Pubkey) -> Pubkey:
        """Get the address that holds token supply"""

    @abstractmethod
    def get_fees_wallet_address(self) -> Pubkey:
        """Get the address that holds native token for fees"""

    @abstractmethod
    def get_fees_wallet_balance(self) -> int:
        """Get the balance of the native currency of the wallet.

        Useful to check if you have enough cryptocurrency for the fees.
        """


class HotWalletTransactionBuilder(TransactionBuilder):
    """Create transactions from the hot wallet and store them in the state.

    Creates trackable transactions. TransactionHelper is initialised
    at the start of the each cycle.

    Transaction builder can prepare multiple transactions in one batch.
    For all tranactions, we use the previously prepared gas price information.
    """

    def __init__(self,
                 web3: Client,
                 hot_wallet: HotWallet
                 ):
        super().__init__(web3)
        self.hot_wallet = hot_wallet

    def init(self):
        pass

    def get_token_delivery_address(self) -> Keypair:
        """Get the target address for token delivery."""
        return self.hot_wallet.account

    def get_token_account(self, token_pubkey: Pubkey) -> Pubkey:
        """Get the address that holds token supply"""
        # TODO: multi tokens in one wallet
        return self.hot_wallet.account

    def get_fees_wallet_address(self) -> Pubkey:
        """Get the address that holds native token for fees"""
        return self.hot_wallet.address

    def get_fees_wallet_balance(self) -> int:
        """Get the balance of the native currency of the wallet.

        Useful to check if you have enough cryptocurrency for the fees.
        """
        return self.hot_wallet.get_native_currency_balance(self.web3)

    def sign_transaction(
            self,
            ix_list: list[Instruction],
            compute_unit_limit: Optional[int] = 200_000,
            priority_fee_suggestion: Optional[int] = None,
            notes: str = "",
    ) -> BlockchainTransaction:
        """Sign a transaction with the hot wallet private key."""

        ix_list_ = []
        has_compute_budget = False
        for ix in ix_list:
            if ix.program_id == Pubkey.from_string("ComputeBudget111111111111111111111111111111"):
                has_compute_budget = True

        if not has_compute_budget:
            if compute_unit_limit > 1_400_000:
                compute_unit_limit = 1_400_000
            ix_list_.append(set_compute_unit_limit(compute_unit_limit))

            if priority_fee_suggestion is not None:
                ix_list_.append(set_compute_unit_price(priority_fee_suggestion))
            else:
                # priority_fee_suggestion = self.fetch_fee_suggestion(ix_list)
                # priority_fee_suggestion -= 5000
                # ix_list_new.append(set_compute_unit_price(priority_fee_suggestion))
                priority_fee_suggestion = 0
        else:
            priority_fee_suggestion = 0

        ix_list_.extend(ix_list)

        if self.hot_wallet.nonce_account is None:
            blockhash = self.web3.get_latest_blockhash().value.blockhash
            signed_tx = self.hot_wallet.sign_transaction_with_blockhash(ix_list_, blockhash)
        else:
            signed_tx = self.hot_wallet.sign_transaction_with_new_nonce(ix_list_, self.web3)

        logger.info(
            "Signed transactions using priority fee %d for %s, tx's nonce is %d, cu limit %d",
            priority_fee_suggestion,
            "fn_name",
            0,
            compute_unit_limit,
        )

        signed_bytes = bytes(signed_tx)

        return BlockchainTransaction(
            chain_id=self.chain_id,
            from_address=self.hot_wallet.address,
            contract_address=None,
            function_selector=None,
            transaction_args=None,
            args=None,
            wrapped_args=None,
            signed_bytes=signed_bytes.hex(),
            signed_tx_object=encode_pickle_over_json(signed_tx),
            tx_hash=str(signed_tx.verify_and_hash_message()),
            nonce=None,
            details=signed_tx,
            asset_deltas=[],
            notes=notes,
        )
