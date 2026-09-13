"""
Direct-mode tests for SealedBidProcurement.

Naming convention: each test name states the property being verified, not
the mechanics used to verify it.
"""

from datetime import datetime, timedelta

from .conftest import warp_to, _addr_bytes

CONTRACT = "contracts/sealed_bid_procurement.py"

SPEC = "A responsive marketing landing page matching the provided brand guide, deployed and reachable at a public URL."
BUDGET = 10_000
WINDOW = 3600  # minimum allowed for every one of the three windows

SELECT_PATTERN = r"selecting the winning bid"


def _deploy(direct_deploy, direct_vm, sender):
    direct_vm.sender = sender
    return direct_deploy(CONTRACT)


def _addr_hex(addr) -> str:
    return "0x" + _addr_bytes(addr).hex()


def _create_auction(contract, direct_vm, client, **overrides):
    direct_vm.sender = client
    direct_vm.value = overrides.get("value", overrides.get("max_budget", BUDGET))
    auction_id = contract.create_auction(
        overrides.get("spec", SPEC),
        overrides.get("max_budget", BUDGET),
        overrides.get("commit_window_seconds", WINDOW),
        overrides.get("reveal_window_seconds", WINDOW),
        overrides.get("cancel_grace_seconds", WINDOW),
    )
    direct_vm.value = 0
    return auction_id


def _commit(contract, direct_vm, bidder, auction_id, price, approach, salt):
    commitment = contract.compute_commitment(price, approach, salt, _addr_hex(bidder))
    direct_vm.sender = bidder
    return contract.commit_bid(auction_id, commitment)


def _reveal(contract, direct_vm, bidder, bid_id, price, approach, salt):
    direct_vm.sender = bidder
    contract.reveal_bid(bid_id, price, approach, salt)


def _iso_plus(iso: str, seconds: float) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt + timedelta(seconds=seconds)).isoformat()


def _warp_past_commit_deadline(direct_vm, contract, auction_id, extra=1):
    a = contract.get_auction(auction_id)
    warp_to(direct_vm, _iso_plus(a["commit_deadline"], extra))


def _warp_past_reveal_deadline(direct_vm, contract, auction_id, extra=1):
    a = contract.get_auction(auction_id)
    warp_to(direct_vm, _iso_plus(a["reveal_deadline"], extra))


def _warp_past_cancel_deadline(direct_vm, contract, auction_id, extra=1):
    a = contract.get_auction(auction_id)
    warp_to(direct_vm, _iso_plus(a["cancel_deadline"], extra))


def _mock_winner(direct_vm, bid_id, reason="Best value for the specified work."):
    direct_vm.mock_llm(SELECT_PATTERN, '{"winning_bid_id": %d, "reason": "%s"}' % (bid_id, reason))


def _mock_none_qualify(direct_vm, reason="No bid adequately meets the specification."):
    direct_vm.mock_llm(SELECT_PATTERN, '{"winning_bid_id": null, "reason": "%s"}' % reason)


# ---------------------------------------------------------------------
# Deploy / initial state
# ---------------------------------------------------------------------


def test_fresh_deploy_has_zero_auctions_and_bids(direct_deploy, direct_vm, direct_owner):
    c = _deploy(direct_deploy, direct_vm, direct_owner)
    assert int(c.auction_count()) == 0
    assert int(c.bid_count_total()) == 0


# ---------------------------------------------------------------------
# create_auction - input validation
# ---------------------------------------------------------------------


def test_create_auction_succeeds_and_stores_declared_fields(direct_deploy, direct_vm, direct_alice):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    a = c.get_auction(auction_id)
    assert a["client"].lower() == _addr_hex(direct_alice).lower()
    assert a["spec"] == SPEC
    assert a["max_budget"] == BUDGET
    assert a["state"] == "OPEN"
    assert a["commit_deadline"] < a["reveal_deadline"] < a["cancel_deadline"]


def test_create_auction_rejects_empty_spec(direct_deploy, direct_vm, direct_alice):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    with direct_vm.expect_revert("spec must be"):
        _create_auction(c, direct_vm, direct_alice, spec="")


def test_create_auction_rejects_non_positive_budget(direct_deploy, direct_vm, direct_alice):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    with direct_vm.expect_revert("max_budget must be positive"):
        _create_auction(c, direct_vm, direct_alice, max_budget=0, value=0)


def test_create_auction_rejects_value_not_matching_budget(direct_deploy, direct_vm, direct_alice):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    with direct_vm.expect_revert("sent value must exactly equal"):
        _create_auction(c, direct_vm, direct_alice, value=BUDGET - 1)


def test_create_auction_rejects_commit_window_too_short(direct_deploy, direct_vm, direct_alice):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    with direct_vm.expect_revert("commit_window_seconds must be in"):
        _create_auction(c, direct_vm, direct_alice, commit_window_seconds=10)


def test_create_auction_rejects_reveal_window_too_long(direct_deploy, direct_vm, direct_alice):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    with direct_vm.expect_revert("reveal_window_seconds must be in"):
        _create_auction(c, direct_vm, direct_alice, reveal_window_seconds=31 * 24 * 3600)


def test_create_auction_rejects_cancel_grace_too_short(direct_deploy, direct_vm, direct_alice):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    with direct_vm.expect_revert("cancel_grace_seconds must be in"):
        _create_auction(c, direct_vm, direct_alice, cancel_grace_seconds=10)


# ---------------------------------------------------------------------
# commit_bid
# ---------------------------------------------------------------------


def test_commit_bid_rejects_the_client_bidding_on_their_own_auction(direct_deploy, direct_vm, direct_alice):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    commitment = c.compute_commitment(5000, "my approach", "salt1", _addr_hex(direct_alice))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("may not bid on their own auction"):
        c.commit_bid(auction_id, commitment)


def test_commit_bid_rejects_malformed_commitment(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("hex digest"):
        c.commit_bid(auction_id, "not-hex-and-wrong-length")


def test_commit_bid_rejects_a_second_commitment_from_the_same_bidder(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    _commit(c, direct_vm, direct_bob, auction_id, 5000, "approach one", "salt1")
    commitment2 = c.compute_commitment(6000, "approach two", "salt2", _addr_hex(direct_bob))
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("already committed"):
        c.commit_bid(auction_id, commitment2)


def test_commit_bid_rejects_after_the_commit_window_closes(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    _warp_past_commit_deadline(direct_vm, c, auction_id)
    commitment = c.compute_commitment(5000, "approach", "salt", _addr_hex(direct_bob))
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("commit window has closed"):
        c.commit_bid(auction_id, commitment)


def test_commit_bid_enforces_the_max_bidders_cap(direct_deploy, direct_vm, direct_alice, direct_accounts):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    for i, bidder in enumerate(direct_accounts):  # exactly 10 - the cap
        _commit(c, direct_vm, bidder, auction_id, 1000 + i, f"approach {i}", f"salt{i}")

    from gltest.direct.loader import create_address

    eleventh = create_address("eleventh_bidder")
    commitment = c.compute_commitment(9999, "late approach", "salt-late", _addr_hex(eleventh))
    direct_vm.sender = eleventh
    with direct_vm.expect_revert("maximum number of bidders"):
        c.commit_bid(auction_id, commitment)


# ---------------------------------------------------------------------
# reveal_bid
# ---------------------------------------------------------------------


def test_reveal_bid_rejects_before_the_commit_window_closes(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, 5000, "approach", "salt")
    with direct_vm.expect_revert("has not opened yet"):
        _reveal(c, direct_vm, direct_bob, bid_id, 5000, "approach", "salt")


def test_reveal_bid_succeeds_with_the_exact_committed_values(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, 5000, "a careful approach", "s@lt-1")
    _warp_past_commit_deadline(direct_vm, c, auction_id)
    _reveal(c, direct_vm, direct_bob, bid_id, 5000, "a careful approach", "s@lt-1")
    b = c.get_bid(bid_id)
    assert b["revealed"] is True
    assert b["price"] == 5000
    assert b["approach"] == "a careful approach"


def test_reveal_bid_rejects_non_bidder(direct_deploy, direct_vm, direct_alice, direct_bob, direct_owner):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_commit_deadline(direct_vm, c, auction_id)
    with direct_vm.expect_revert("only the committing bidder"):
        _reveal(c, direct_vm, direct_owner, bid_id, 5000, "approach", "salt")


def test_reveal_bid_rejects_a_second_reveal_of_the_same_bid(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_commit_deadline(direct_vm, c, auction_id)
    _reveal(c, direct_vm, direct_bob, bid_id, 5000, "approach", "salt")
    with direct_vm.expect_revert("already been revealed"):
        _reveal(c, direct_vm, direct_bob, bid_id, 5000, "approach", "salt")


def test_reveal_bid_rejects_after_the_reveal_window_closes(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_reveal_deadline(direct_vm, c, auction_id)
    with direct_vm.expect_revert("reveal window has closed"):
        _reveal(c, direct_vm, direct_bob, bid_id, 5000, "approach", "salt")


def test_reveal_bid_rejects_price_above_the_budget(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, BUDGET + 1, "approach", "salt")
    _warp_past_commit_deadline(direct_vm, c, auction_id)
    with direct_vm.expect_revert("at most the auction's max_budget"):
        _reveal(c, direct_vm, direct_bob, bid_id, BUDGET + 1, "approach", "salt")


def test_reveal_bid_rejects_a_mismatched_commitment(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_commit_deadline(direct_vm, c, auction_id)
    with direct_vm.expect_revert("do not match the original commitment"):
        _reveal(c, direct_vm, direct_bob, bid_id, 5001, "approach", "salt")  # price changed after committing


# ---------------------------------------------------------------------
# Commitment encoding - a review found that the original colon-joined
# preimage (f"{price}:{approach}:{salt}:{bidder}") was ambiguous, since
# approach and salt are free text that may themselves contain colons: two
# genuinely different (approach, salt) pairs could join to the exact same
# string, letting a bidder reveal either interpretation against the same
# commitment. The fix length-prefixes every field before concatenating
# (see _frame in the contract). These tests prove the fix directly,
# reconstructing the exact collision the old scheme had and confirming it
# no longer occurs.
# ---------------------------------------------------------------------


def test_compute_commitment_no_longer_collides_across_a_colon_boundary_shift(
    direct_deploy, direct_vm, direct_alice
):
    """Reconstructs the exact vulnerability the review described: under
    the old plain colon-joined preimage, approach="A:B", salt="C" and
    approach="A", salt="B:C" both joined to the identical string
    "...A:B:C..." - two genuinely different (approach, salt) pairs
    producing the same hash. With length-prefixed framing they must now
    differ."""
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    bidder_hex = _addr_hex(direct_alice)

    commitment_a = c.compute_commitment(5000, "A:B", "C", bidder_hex)
    commitment_b = c.compute_commitment(5000, "A", "B:C", bidder_hex)
    assert commitment_a != commitment_b


def test_reveal_bid_rejects_the_colliding_alternate_interpretation_of_a_committed_colon(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    """End-to-end proof, not just the isolated hash function: a bidder who
    committed to approach="A:B", salt="C" cannot reveal the OLD scheme's
    colliding alternate interpretation (approach="A", salt="B:C") - it
    must be rejected as a mismatched commitment, exactly as any other
    wrong reveal would be."""
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, 5000, "A:B", "C")
    _warp_past_commit_deadline(direct_vm, c, auction_id)
    with direct_vm.expect_revert("do not match the original commitment"):
        _reveal(c, direct_vm, direct_bob, bid_id, 5000, "A", "B:C")

    # the true, originally-committed values still reveal correctly
    _reveal(c, direct_vm, direct_bob, bid_id, 5000, "A:B", "C")
    b = c.get_bid(bid_id)
    assert b["revealed"] is True
    assert b["approach"] == "A:B"


# ---------------------------------------------------------------------
# select_winner - the judged path
# ---------------------------------------------------------------------


def test_select_winner_rejects_before_the_reveal_window_closes(direct_deploy, direct_vm, direct_alice, direct_bob):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    _commit(c, direct_vm, direct_bob, auction_id, 5000, "approach", "salt")
    with direct_vm.expect_revert("has not closed yet"):
        c.select_winner(auction_id)


def test_select_winner_with_zero_revealed_bids_voids_and_refunds_the_client_in_full(
    direct_deploy, direct_vm_with_transfers, direct_alice, direct_bob
):
    vm = direct_vm_with_transfers
    c = _deploy(direct_deploy, vm, direct_alice)
    auction_id = _create_auction(c, vm, direct_alice)
    _commit(c, vm, direct_bob, auction_id, 5000, "approach", "salt")  # never revealed
    _warp_past_reveal_deadline(vm, c, auction_id)
    c.select_winner(auction_id)
    a = c.get_auction(auction_id)
    assert a["state"] == "VOID"
    assert vm._balances.get(_addr_bytes(direct_alice), 0) == BUDGET
    assert vm._balances.get(_addr_bytes(direct_bob), 0) == 0


def test_select_winner_with_no_adequate_bid_voids_and_refunds_the_client(
    direct_deploy, direct_vm_with_transfers, direct_alice, direct_bob
):
    vm = direct_vm_with_transfers
    c = _deploy(direct_deploy, vm, direct_alice)
    auction_id = _create_auction(c, vm, direct_alice)
    bid_id = _commit(c, vm, direct_bob, auction_id, 5000, "an inadequate approach", "salt")
    _warp_past_commit_deadline(vm, c, auction_id)
    _reveal(c, vm, direct_bob, bid_id, 5000, "an inadequate approach", "salt")
    _warp_past_reveal_deadline(vm, c, auction_id)
    _mock_none_qualify(vm)
    c.select_winner(auction_id)
    a = c.get_auction(auction_id)
    assert a["state"] == "VOID"
    assert vm._balances.get(_addr_bytes(direct_alice), 0) == BUDGET
    assert vm._balances.get(_addr_bytes(direct_bob), 0) == 0


def test_select_winner_awards_the_chosen_bid_and_refunds_the_difference(
    direct_deploy, direct_vm_with_transfers, direct_alice, direct_bob, direct_owner
):
    vm = direct_vm_with_transfers
    c = _deploy(direct_deploy, vm, direct_alice)
    auction_id = _create_auction(c, vm, direct_alice)
    bid_bob = _commit(c, vm, direct_bob, auction_id, 6000, "bob's approach", "salt-bob")
    bid_owner = _commit(c, vm, direct_owner, auction_id, 7000, "owner's approach", "salt-owner")
    _warp_past_commit_deadline(vm, c, auction_id)
    _reveal(c, vm, direct_bob, bid_bob, 6000, "bob's approach", "salt-bob")
    _reveal(c, vm, direct_owner, bid_owner, 7000, "owner's approach", "salt-owner")
    _warp_past_reveal_deadline(vm, c, auction_id)
    _mock_winner(vm, bid_owner, reason="Better fit for the spec despite the higher price.")
    c.select_winner(auction_id)

    a = c.get_auction(auction_id)
    assert a["state"] == "AWARDED"
    assert a["winner"].lower() == _addr_hex(direct_owner).lower()
    assert a["winning_price"] == 7000
    assert vm._balances.get(_addr_bytes(direct_owner), 0) == 7000
    assert vm._balances.get(_addr_bytes(direct_bob), 0) == 0
    assert vm._balances.get(_addr_bytes(direct_alice), 0) == BUDGET - 7000  # the unspent remainder


def test_select_winner_on_unparseable_output_errors_not_a_winner(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_commit_deadline(direct_vm, c, auction_id)
    _reveal(c, direct_vm, direct_bob, bid_id, 5000, "approach", "salt")
    _warp_past_reveal_deadline(direct_vm, c, auction_id)
    direct_vm.mock_llm(SELECT_PATTERN, "not json at all, sorry")
    c.select_winner(auction_id)
    assert c.get_auction(auction_id)["state"] == "ERRORED"


def test_select_winner_rejects_a_winning_bid_id_that_was_never_revealed(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    c = _deploy(direct_deploy, direct_vm, direct_alice)
    auction_id = _create_auction(c, direct_vm, direct_alice)
    bid_id = _commit(c, direct_vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_commit_deadline(direct_vm, c, auction_id)
    _reveal(c, direct_vm, direct_bob, bid_id, 5000, "approach", "salt")
    _warp_past_reveal_deadline(direct_vm, c, auction_id)
    _mock_winner(direct_vm, 999999)  # a bid id that doesn't exist in this auction
    c.select_winner(auction_id)
    assert c.get_auction(auction_id)["state"] == "ERRORED"


def test_select_winner_after_errored_can_be_retried_permissionlessly(
    direct_deploy, direct_vm_with_transfers, direct_alice, direct_bob, direct_owner
):
    vm = direct_vm_with_transfers
    c = _deploy(direct_deploy, vm, direct_alice)
    auction_id = _create_auction(c, vm, direct_alice)
    bid_id = _commit(c, vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_commit_deadline(vm, c, auction_id)
    _reveal(c, vm, direct_bob, bid_id, 5000, "approach", "salt")
    _warp_past_reveal_deadline(vm, c, auction_id)
    vm.mock_llm(SELECT_PATTERN, "garbage")
    c.select_winner(auction_id)
    assert c.get_auction(auction_id)["state"] == "ERRORED"

    vm.clear_mocks()
    _mock_winner(vm, bid_id)
    vm.sender = direct_owner  # permissionless retry, not even the client
    c.select_winner(auction_id)
    assert c.get_auction(auction_id)["state"] == "AWARDED"


def test_select_winner_refuses_to_act_over_a_cancellation_that_lands_first(
    direct_deploy, direct_vm_with_transfers, direct_alice, direct_bob, direct_owner
):
    """
    A narrower proof of the mid-flight revalidation than the full race
    (which real time and an async consensus round make impossible to
    interleave synchronously in direct mode - see DESIGN.md): if an
    auction is cancelled before select_winner is even called, the outer
    state gate alone must already refuse to act on it, exactly as it
    would if cancellation had landed between the round completing and
    settlement running. The fifth independently rediscovered instance of
    the HandleGuard lesson in this portfolio.
    """
    vm = direct_vm_with_transfers
    c = _deploy(direct_deploy, vm, direct_alice)
    auction_id = _create_auction(c, vm, direct_alice)
    bid_id = _commit(c, vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_commit_deadline(vm, c, auction_id)
    _reveal(c, vm, direct_bob, bid_id, 5000, "approach", "salt")
    _warp_past_cancel_deadline(vm, c, auction_id)

    vm.sender = direct_owner  # permissionless trigger
    c.cancel_auction(auction_id)
    assert c.get_auction(auction_id)["state"] == "CANCELLED"

    _mock_winner(vm, bid_id)
    with vm.expect_revert("not eligible for selection"):
        c.select_winner(auction_id)
    # the client's refund from cancellation is untouched by the blocked attempt
    assert vm._balances.get(_addr_bytes(direct_alice), 0) == BUDGET
    assert vm._balances.get(_addr_bytes(direct_bob), 0) == 0


# ---------------------------------------------------------------------
# cancel_auction - the client's bounded, permissionless exit
# ---------------------------------------------------------------------


def test_cancel_auction_rejects_before_the_cancel_timeout_elapses(direct_deploy, direct_vm_with_transfers, direct_alice, direct_bob):
    vm = direct_vm_with_transfers
    c = _deploy(direct_deploy, vm, direct_alice)
    auction_id = _create_auction(c, vm, direct_alice)
    with vm.expect_revert("has not passed yet"):
        c.cancel_auction(auction_id)


def test_cancel_auction_is_permissionless_and_refunds_only_the_client(
    direct_deploy, direct_vm_with_transfers, direct_alice, direct_bob, direct_owner
):
    vm = direct_vm_with_transfers
    c = _deploy(direct_deploy, vm, direct_alice)
    auction_id = _create_auction(c, vm, direct_alice)
    _commit(c, vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_cancel_deadline(vm, c, auction_id)
    vm.sender = direct_owner  # permissionless trigger
    c.cancel_auction(auction_id)
    a = c.get_auction(auction_id)
    assert a["state"] == "CANCELLED"
    assert vm._balances.get(_addr_bytes(direct_alice), 0) == BUDGET
    assert vm._balances.get(_addr_bytes(direct_bob), 0) == 0


def test_cancel_auction_rejects_from_a_terminal_state(direct_deploy, direct_vm_with_transfers, direct_alice, direct_bob):
    vm = direct_vm_with_transfers
    c = _deploy(direct_deploy, vm, direct_alice)
    auction_id = _create_auction(c, vm, direct_alice)
    bid_id = _commit(c, vm, direct_bob, auction_id, 5000, "approach", "salt")
    _warp_past_commit_deadline(vm, c, auction_id)
    _reveal(c, vm, direct_bob, bid_id, 5000, "approach", "salt")
    _warp_past_reveal_deadline(vm, c, auction_id)
    _mock_winner(vm, bid_id)
    c.select_winner(auction_id)
    assert c.get_auction(auction_id)["state"] == "AWARDED"

    with vm.expect_revert("not eligible for cancellation"):
        c.cancel_auction(auction_id)


# ---------------------------------------------------------------------
# Fund conservation / unknown ids
# ---------------------------------------------------------------------


def test_full_lifecycle_conserves_total_value(direct_deploy, direct_vm_with_transfers, direct_alice, direct_bob, direct_owner):
    vm = direct_vm_with_transfers
    c = _deploy(direct_deploy, vm, direct_alice)
    auction_id = _create_auction(c, vm, direct_alice)
    bid_bob = _commit(c, vm, direct_bob, auction_id, 6000, "bob's approach", "salt-bob")
    bid_owner = _commit(c, vm, direct_owner, auction_id, 4000, "owner's approach", "salt-owner")
    _warp_past_commit_deadline(vm, c, auction_id)
    _reveal(c, vm, direct_bob, bid_bob, 6000, "bob's approach", "salt-bob")
    _reveal(c, vm, direct_owner, bid_owner, 4000, "owner's approach", "salt-owner")
    _warp_past_reveal_deadline(vm, c, auction_id)
    _mock_winner(vm, bid_owner)
    c.select_winner(auction_id)

    total_out = (
        vm._balances.get(_addr_bytes(direct_alice), 0)
        + vm._balances.get(_addr_bytes(direct_bob), 0)
        + vm._balances.get(_addr_bytes(direct_owner), 0)
    )
    assert total_out == BUDGET
    assert c.get_auction(auction_id)["state"] == "AWARDED"


def test_operations_on_unknown_auction_id_revert(direct_deploy, direct_vm, direct_owner):
    c = _deploy(direct_deploy, direct_vm, direct_owner)
    with direct_vm.expect_revert("unknown auction_id"):
        c.get_auction(999)


def test_operations_on_unknown_bid_id_revert(direct_deploy, direct_vm, direct_owner):
    c = _deploy(direct_deploy, direct_vm, direct_owner)
    with direct_vm.expect_revert("unknown bid_id"):
        c.get_bid(999)
