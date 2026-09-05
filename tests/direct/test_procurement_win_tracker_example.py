"""
Tests for the worked consumer example (examples/procurement_win_tracker.py).
Proves the example gates strictly on state before ever reading `winner`,
rather than trusting that field's all-zero placeholder to mean "no winner"
on every non-AWARDED auction.
"""

from gltest.direct.loader import create_address

from .conftest import install_call_contract_hook

CONTRACT = "examples/procurement_win_tracker.py"
HOUSE_ADDRESS_SEED = "some_sealed_bid_procurement"
WINNER_HEX = "0x" + "33" * 20
ZERO_ADDRESS_HEX = "0x" + "00" * 20


def _house_addr_hex(seed=HOUSE_ADDRESS_SEED):
    addr = create_address(seed)
    return "0x" + (addr if isinstance(addr, bytes) else bytes(addr.as_bytes)).hex()


def _auction_payload(state: str, winner: str = ZERO_ADDRESS_HEX) -> dict:
    return {
        "id": 1,
        "client": "0x" + "11" * 20,
        "spec": "a spec",
        "max_budget": 10000,
        "commit_deadline": "2026-01-01T00:00:00+00:00",
        "reveal_deadline": "2026-01-01T01:00:00+00:00",
        "cancel_deadline": "2026-01-01T02:00:00+00:00",
        "state": state,
        "winner": winner,
        "winning_price": 7000 if state == "AWARDED" else 0,
        "reason": "some reason" if state in ("AWARDED", "VOID") else "",
        "created_at": "2025-12-31T00:00:00+00:00",
        "settled_at": "2026-01-01T01:00:05+00:00" if state != "OPEN" else "",
    }


def test_credit_succeeds_when_auction_was_awarded(direct_deploy, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT, _house_addr_hex())
    install_call_contract_hook(direct_vm, {"get_auction": _auction_payload("AWARDED", WINNER_HEX)})

    c.credit_if_awarded(1)
    assert c.wins_for(WINNER_HEX) == 1


def test_credit_rejects_an_open_auction_rather_than_crediting_the_placeholder_winner(
    direct_deploy, direct_vm, direct_alice
):
    """The exact case this example exists to catch: an auction that has
    never been awarded still returns a (placeholder, all-zero) winner
    field, and that must never be read as a real win."""
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT, _house_addr_hex())
    install_call_contract_hook(direct_vm, {"get_auction": _auction_payload("OPEN")})

    with direct_vm.expect_revert("has not reached AWARDED"):
        c.credit_if_awarded(1)
    assert c.wins_for(ZERO_ADDRESS_HEX) == 0


def test_credit_rejects_a_void_auction(direct_deploy, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT, _house_addr_hex())
    install_call_contract_hook(direct_vm, {"get_auction": _auction_payload("VOID")})

    with direct_vm.expect_revert("has not reached AWARDED"):
        c.credit_if_awarded(1)


def test_credit_rejects_a_cancelled_auction(direct_deploy, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT, _house_addr_hex())
    install_call_contract_hook(direct_vm, {"get_auction": _auction_payload("CANCELLED")})

    with direct_vm.expect_revert("has not reached AWARDED"):
        c.credit_if_awarded(1)


def test_credit_cannot_be_claimed_twice_for_the_same_auction(direct_deploy, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT, _house_addr_hex())
    install_call_contract_hook(direct_vm, {"get_auction": _auction_payload("AWARDED", WINNER_HEX)})

    c.credit_if_awarded(1)
    with direct_vm.expect_revert("already credited"):
        c.credit_if_awarded(1)
