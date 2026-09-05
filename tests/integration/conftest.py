import os
import pytest
from gltest import get_contract_factory, get_accounts, get_default_account

CONTRACT_PATH = "sealed_bid_procurement.py"


def _address_from_env() -> str | None:
    return os.environ.get("SEALEDBIDPROCUREMENT_ADDRESS")


@pytest.fixture(scope="session")
def deployed_contract():
    """
    Connects to an already-deployed SealedBidProcurement instead of
    deploying a fresh one. Set SEALEDBIDPROCUREMENT_ADDRESS to the address
    printed by:

        genlayer deploy --contract contracts/sealed_bid_procurement.py

    Tests in this module are skipped (not failed) when the env var is
    absent, so `pytest tests/integration` is safe to run before deploying.
    """
    address = _address_from_env()
    if not address:
        pytest.skip(
            "SEALEDBIDPROCUREMENT_ADDRESS not set - deploy manually first with "
            "`genlayer deploy --contract contracts/sealed_bid_procurement.py` "
            "and export the printed address."
        )
    factory = get_contract_factory(contract_file_path=CONTRACT_PATH)
    return factory.build_contract(contract_address=address)


@pytest.fixture(scope="session")
def client_account():
    return get_default_account()


@pytest.fixture(scope="session")
def bidder_accounts():
    accounts = get_accounts()
    if len(accounts) > 2:
        return accounts[1], accounts[2]
    from gltest import create_account

    return create_account(), create_account()
