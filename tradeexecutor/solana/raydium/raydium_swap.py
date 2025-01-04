"""Raydium swap helper functions.

- :ref:`Read full tutorial <raydium>`_.
"""
import warnings
from typing import Callable
import logging

from solders.instruction import Instruction
from solders.keypair import Keypair

from soldexpy.common.direction import Direction
from soldexpy.common.unit import Unit
from soldexpy.raydium_pool import RaydiumPool
from soldexpy.solana_tx_util.swap_transaction_builder import SwapTransactionBuilder


logger = logging.getLogger(__name__)


def swap_with_slippage_protection(
    raydium_pool: RaydiumPool,
    *,
    payer: Keypair,
    pool_fees: list[int],
    max_slippage: float = 15,
    amount_in: int | None = None,
    amount_out: int | None = None,
) -> list[Instruction]:
    """Helper function to prepare a swap from quote token to base token (buy base token with quote token)
    with price estimation and slippage protection baked in.

    :ref:`Read full tutorial <raydium>`_.

    :param raydium_pool: an instance of `RaydiumPool`
    :param payer: Payer's Keypair
    :param pool_fees:
        List of all pools' trading fees in the path as raw_fee.

        Expressed as BPS * 100, or 1/1,000,000 units.

        For example if your swap is directly between two pools, e.g, WETH-USDC 5 bps, and not routed through additional pools,
        `pool_fees` would be `[500]`.

    :param amount_in: How much of the quote token we want to pay, this has to be `None` if `amount_out` is specified
    :param amount_out: How much of the base token we want to receive, this has to be `None` if `amount_in` is specified

    :param max_slippage:
        Max slippage express in BPS.

        The default is 15 BPS (0.15%)

    :return: Prepared swap instructions which can be used directly to build transaction
    """
    for fee in pool_fees:
        assert fee > 0, "fee must be non-zero"

    if not amount_in and not amount_out:
        raise ValueError("amount_in is specified, amount_out has to be None")

    if max_slippage < 0:
        raise ValueError("max_slippage has to be equal or greater than 0")

    if max_slippage == 0:
        warnings.warn("max_slippage is set to 0, this can potentially lead to reverted transaction. It's recommended to set use default max_slippage instead (0.1 bps) to ensure successful transaction")

    max_slippage_ratio = max_slippage / 10000.0

    '''
    path = [quote_token.address, base_token.address]
    if intermediate_token:
        path = [quote_token.address, intermediate_token.address, base_token.address]
    encoded_path = encode_path(path, pool_fees)

    if len(path) - 1 != len(pool_fees):
        raise ValueError(f"Expected {len(path) - 1} pool fees, got {len(pool_fees)}")
    '''

    if amount_in:
        if amount_out is not None:
            raise ValueError("amount_in is specified, amount_out has to be None")

        # get min amount out
        _, _, estimated_min_amount_out, _ = raydium_pool.get_price(
            amount_in, Direction.SPEND_QUOTE_TOKEN, Unit.BASE_TOKEN, False
        )

        # Because slippage tolerance errors are very annoying to diagnose,
        # try to capture as much possible diagnostics data to logs
        logger.info(
            "exactInput() amount in: %s, estimated_min_amount_out: %s, slippage tolerance: %f BPS, fees: %s, path: %s",
            amount_in,
            estimated_min_amount_out,
            max_slippage,
            pool_fees,
            "",
        )

        amount_out = estimated_min_amount_out * (1 - max_slippage_ratio)
        # convert to tx format
        amount_in = raydium_pool.convert_quote_token_amount_to_tx_format(amount_in)
        amount_out = raydium_pool.convert_base_token_amount_to_tx_format(amount_out)

        swap_transaction_builder = SwapTransactionBuilder(raydium_pool.client, raydium_pool, payer)
        swap_transaction_builder.append_buy(amount_in, amount_out, True)

        return swap_transaction_builder.instructions

    '''
    elif amount_out:
        if amount_in is not None:
            raise ValueError("amount_out is specified, amount_in has to be None")

        estimated_max_amount_in: int = price_helper.get_amount_in(
            amount_out=amount_out,
            path=path,
            fees=pool_fees,
            slippage=max_slippage,
        )

        logger.info("exactInput() amount out: %s, estimated_max_amount_in: %s", amount_out, estimated_max_amount_in)

        return router.functions.exactOutput(
            (
                encoded_path,
                recipient_address,
                deadline,
                amount_out,
                estimated_max_amount_in,
            )
        )
    '''
