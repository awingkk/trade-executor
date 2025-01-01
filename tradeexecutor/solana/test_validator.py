"""Solana Test Validator integration.

This module provides Python integration for Solana Test Validator.

- `Solana Test Validator <https://solana.com/developers/guides/getstarted/solana-test-validator>`
  is a local emulator for the Solana blockchain

- Solana Test Validator is designed to provide developers with a private and controlled environment for building and testing Solana programs without the need to connect to a public testnet or mainnet.

- Solana Test Validator is a is part of the Solana CLI tool suite

The code was originally from web3-ethereum-defi/eth_defi/provider/anvil.py
"""

import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from subprocess import DEVNULL, PIPE
from typing import Optional

import psutil
import requests
from httpx import ConnectError

# TODO: Move to Non-ETH package
from eth_defi.utils import is_localhost_port_listening, shutdown_hard

from solana.rpc.api import Client
from solana.rpc.commitment import Processed
from solana.exceptions import SolanaRpcException

logger = logging.getLogger(__name__)



def _launch(cmd: str, args_list: list) -> tuple[psutil.Popen, list[str]]:
    """Launches the RPC client.

    Args:
        cmd: command string to execute as subprocess"""
    if sys.platform == "win32" and not cmd.split(" ")[0].endswith(".cmd"):
        if " " in cmd:
            cmd = cmd.replace(" ", ".cmd ", 1)
        else:
            cmd += ".cmd"
    cmd_list = cmd.split(" ")
    cmd_list.extend(args_list)
    final_cmd_str = " ".join(cmd_list)
    logger.info("Launching Test Validator: %s", final_cmd_str)
    out = DEVNULL if sys.platform == "win32" else PIPE
    env = os.environ.copy()
    return psutil.Popen(cmd_list, stdout=out, stderr=out, env=env), cmd_list


@dataclass
class TestValidatorLaunch:
    """Control Test Validator processes launched on background.

    Comes with a helpful :py:meth:`close` method when it is time to put Test Validator rest.
    """

    #: Which port was bound by the Test Validator
    port: int

    #: Used command-line to spin up Test Validator
    cmd: list[str]

    #: Where does Test Validator listen to JSON-RPC
    json_rpc_url: str

    #: UNIX process that we opened
    process: psutil.Popen

    def close(self, log_level: Optional[int] = None, block=True, block_timeout=30) -> tuple[bytes, bytes]:
        """Close the background Test Validator process.

        :param log_level:
            Dump Test Validator messages to logging

        :param block:
            Block the execution until Test Validator is gone

        :param block_timeout:
            How long time we try to kill Test Validator until giving up.

        :return:
            Test Validator stdout, stderr as string
        """
        stdout, stderr = shutdown_hard(
            self.process,
            log_level=log_level,
            block=block,
            block_timeout=block_timeout,
            check_port=self.port,
        )
        logger.info("Test Validator shutdown %s", self.json_rpc_url)
        return stdout, stderr


def launch_test_validator(
    fork_url: Optional[str] = None,
    ledger: str = "~/test-ledger",
    account_file: str = None,
    clone_accounts: list[str] = [],
    clone_programs: list[str] = [],
    cmd="solana-test-validator",
    port: int = 8899,
    launch_wait_seconds=20.0,
    attempts=1,
    test_request_timeout=3.0,
) -> TestValidatorLaunch:
    """Creates Test Validator unit test backend or mainnet fork.

    When called, a subprocess is started on the background.
    To stop this process, call :py:meth:`solana.test_validator.TestValidatorLaunch.close`.

    This function waits `launch_wait_seconds` in order to `solana-test-validator` process to start and complete the chain fork.

    Always reset the ledger to genesis. (--reset)

    :param fork_url:
        HTTP JSON-RPC URL of the network we want to fork. (--url)
        If not given launch an empty test backend.

    :param ledger:
        When starting the test validator, you can specify a different directory for the ledger data. (--ledger)

    :param account_file:
        Load an account from the provided JSON file (see `solana account --help` on how to dump an account to file). (--account)
        Files are searched for relatively to CWD and tests/fixtures. 

    :param cmd:
        Override `solana-test-validator` command. If not given we look up from `PATH`.

    :param port:
        Localhost port we bind for solana-test-validator JSON-RPC. (--rpc-port)

    :param launch_wait_seconds:
        How long we wait test validator to start until giving up

    :param attempts:
        How many attempts we do to start test validator.

    :param test_request_timeout:
        Set the timeout fro the JSON-RPC requests that attempt to determine if test validator was successfully launched.

    """

    attempts_left = attempts
    process = None
    final_cmd = None
    latest_slot = 0
    client = None

    # Give helpful error message
    validator = shutil.which("solana-test-validator")
    assert validator is not None, f"solana-test-validator command not in PATH {os.environ.get('PATH')}"

    # Find a free port
    assert not is_localhost_port_listening(port), f"localhost port {port} occupied.\n" f"You might have a zombie solana-test-validator process around.\nRun to kill: kill -SIGKILL $(lsof -ti:{port})"

    url = f"http://localhost:{port}"

    args = [
        "--rpc-port", str(port),
        "--url", fork_url,
        "--reset",
        "--ledger", ledger,
    ]
    if account_file is not None:
        args.extend(["--account", "-", account_file])
    for a in clone_accounts:
        args.extend(["--clone", a])
    for p in clone_programs:
        args.extend(["--clone-upgradeable-program", p])

    latest_slot = version = None

    while attempts_left > 0:
        process, final_cmd = _launch(
            cmd,
            args,
        )
        time.sleep(3.0)

        # Wait until Test Validator is responsive
        timeout = time.time() + launch_wait_seconds

        # Use shorter read timeout here - otherwise requests will wait > 10s if something is wrong
        client = Client(url, timeout=test_request_timeout)
        while time.time() < timeout:
            try:
                # See if RPC works
                version = client.get_version().value.solana_core
                latest_slot = client.get_slot(Processed).value
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
                # logger.info("Test Validator not ready, got exception %s", e.error_msg)
                # requests.exceptions.ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))
                time.sleep(0.1)
                continue
            except SolanaRpcException as e:
                if isinstance(e.__cause__, ConnectError):
                    time.sleep(0.1)
                    continue
                # logger.info("Test Validator got exception: " + e.error_msg)
                raise

        if latest_slot is None:
            logger.error("Could not read the latest block from Test Validator %s within %f seconds, shutting down and dumping output", url, launch_wait_seconds)
            stdout, stderr = shutdown_hard(
                process,
                log_level=logging.ERROR,
                block=True,
                check_port=port,
            )

            if len(stdout) == 0:
                attempts_left -= 1
                if attempts_left > 0:
                    logger.info("Test Validator did not start properly, try again, attempts left %d", attempts_left)
                    continue

            raise AssertionError(f"Could not read block hash from Test Validator after the launch {cmd}: at {url}, stdout is {len(stdout)} bytes, stderr is {len(stderr)} bytes")
        else:
            # We have a successful launch
            break

    logger.info(f"Test Validator network version {version}, the latest_slot is {latest_slot}, JSON-RPC is {url}")

    return TestValidatorLaunch(port, final_cmd, url, process)
