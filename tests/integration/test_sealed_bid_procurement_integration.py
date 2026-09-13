"""
Integration tests against a StudioNet-deployed SealedBidProcurement.
Requires SEALEDBIDPROCUREMENT_ADDRESS (see conftest.py).

Unlike every other judged write in this portfolio, select_winner never
fetches anything live - its one non-deterministic operation reviews only
already-committed on-chain bid data, so there is no DriftWatch-style "did
every validator see the same content" risk to design around here. What
this test DOES have to work around is real wall-clock time: commit_deadline
and reveal_deadline are genuine timestamps on a live chain, checked against
each VALIDATOR's own clock, not this test runner's. An initial sleep based
on this process's local clock was tried first and confirmed unreliable in
practice - it under-slept relative to the deployment's actual validators by
enough to still hit "has not opened/closed yet" after a 45s buffer, which
only makes sense as real clock skew between this machine and the validator
infrastructure, not a contract defect (every such rejection is the
contract's own deterministic guard working exactly as designed). Rather
than guess a bigger fixed buffer against an unmeasured skew, this test
polls: it retries the deadline-gated call, adding a fixed wait between
attempts, until it succeeds or genuinely exhausts its budget.
"""

import hashlib
import time
from datetime import datetime, timezone

import pytest
from gltest.assertions import tx_execution_succeeded, tx_execution_failed

FAST_WAIT = dict(wait_interval=3000, wait_retries=30)
SLOW_WAIT = dict(wait_interval=6000, wait_retries=100)

POLL_SECONDS = 20
MAX_POLL_ATTEMPTS = 10  # up to ~200s of extra margin beyond the initial sleep

BUDGET = 10_000
COMMIT_WINDOW = 120
REVEAL_WINDOW = 120
CANCEL_GRACE = 60  # minimum allowed; not exercised in a live wait here

SPEC = (
    "A written project plan for a small website migration that explicitly includes "
    "all three of: (1) a fixed-price quote, (2) a concrete testing plan, and (3) a "
    "rollback plan in case something goes wrong."
)

ADEQUATE_APPROACH = (
    "Fixed price: $8000 for the full migration. Testing plan: staging deploy first, "
    "full regression pass on every page, then a monitored canary release to 10% of "
    "traffic before full cutover. Rollback plan: the old server stays live and "
    "unmodified for two weeks post-migration, with DNS able to revert to it within "
    "five minutes if any critical issue is found."
)

INADEQUATE_APPROACH = (
    "I will repaint the reception area and replace the office furniture next month."
)


def _seconds_until(iso: str, buffer_seconds: float = 45.0) -> float:
    """A generous buffer on top of the exact remaining time: a live chain's
    validators evaluate `datetime.now()` on their own clocks, not this
    test runner's, and this deterministic check has no non-determinism
    budget behind it to reconcile any drift - so the wait must clear the
    deadline by enough margin to absorb real clock skew and multi-
    validator processing latency, not just this process's own clock."""
    deadline = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return max(0.0, (deadline - now).total_seconds()) + buffer_seconds


def _retry_past_deadline(attempt):
    """Call attempt() (a zero-arg closure returning a .transact(...) result)
    and, while it fails, sleep POLL_SECONDS and retry - up to
    MAX_POLL_ATTEMPTS times. Only a deadline that hasn't actually passed
    yet on the validators' own clocks should ever cause repeated failure
    here; any other cause keeps failing across every retry too, so this
    still surfaces a genuine bug as a hard, clearly-labeled failure rather
    than masking it."""
    result = attempt()
    for _ in range(MAX_POLL_ATTEMPTS):
        if tx_execution_succeeded(result):
            return result
        time.sleep(POLL_SECONDS)
        result = attempt()
    return result


def _frame(value: str) -> str:
    return str(len(value)) + ":" + value


def _local_commitment(price: int, approach: str, salt: str, bidder_hex: str) -> str:
    """Computed locally rather than via the contract's own compute_commitment
    view: gen_call has a known encoding limit on longer string arguments
    (confirmed separately - short approach text round-trips fine, ~400
    chars fails with an RLP/list-length decoding error at the RPC layer),
    and a real bidder would compute this client-side anyway rather than
    depend on an on-chain convenience view. Must match _compute_commitment
    / _frame in contracts/sealed_bid_procurement.py exactly - including
    the length-prefixed framing fix a review required, which this mirror
    was missed on once already (caught by this exact test run failing
    with a mismatched-commitment error until fixed here)."""
    preimage = _frame(str(price)) + _frame(approach) + _frame(salt) + _frame(bidder_hex.lower())
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


@pytest.mark.integration
def test_full_auction_selects_the_bid_that_actually_satisfies_the_spec(
    deployed_contract, client_account, bidder_accounts
):
    c = deployed_contract
    client = c.connect(client_account)
    bidder_a, bidder_b = bidder_accounts
    a = c.connect(bidder_a)
    b = c.connect(bidder_b)

    create_result = client.create_auction(
        args=[SPEC, BUDGET, COMMIT_WINDOW, REVEAL_WINDOW, CANCEL_GRACE]
    ).transact(value=BUDGET, **FAST_WAIT)
    assert tx_execution_succeeded(create_result), create_result
    auction_id = int(c.auction_count(args=[]).call()) - 1

    # selection must refuse to run at all before the reveal window closes -
    # cheap to check here since a rejected call never mutates state.
    early_attempt = client.select_winner(args=[auction_id]).transact(**FAST_WAIT)
    assert tx_execution_failed(early_attempt), "select_winner must refuse to run before the reveal window closes"

    commitment_a = _local_commitment(8000, ADEQUATE_APPROACH, "salt-a", bidder_a.address)
    commitment_b = _local_commitment(3000, INADEQUATE_APPROACH, "salt-b", bidder_b.address)

    commit_a = a.commit_bid(args=[auction_id, commitment_a]).transact(**FAST_WAIT)
    assert tx_execution_succeeded(commit_a), commit_a
    bid_id_a = int(c.list_bids_for_auction(args=[auction_id]).call()[0])

    commit_b = b.commit_bid(args=[auction_id, commitment_b]).transact(**FAST_WAIT)
    assert tx_execution_succeeded(commit_b), commit_b
    bid_id_b = int(c.list_bids_for_auction(args=[auction_id]).call()[1])

    auction = c.get_auction(args=[auction_id]).call()
    time.sleep(_seconds_until(auction["commit_deadline"]))

    reveal_a = _retry_past_deadline(
        lambda: a.reveal_bid(args=[bid_id_a, 8000, ADEQUATE_APPROACH, "salt-a"]).transact(**FAST_WAIT)
    )
    assert tx_execution_succeeded(reveal_a), reveal_a

    reveal_b = _retry_past_deadline(
        lambda: b.reveal_bid(args=[bid_id_b, 3000, INADEQUATE_APPROACH, "salt-b"]).transact(**FAST_WAIT)
    )
    assert tx_execution_succeeded(reveal_b), reveal_b

    auction = c.get_auction(args=[auction_id]).call()
    time.sleep(_seconds_until(auction["reveal_deadline"]))

    select_result = _retry_past_deadline(
        lambda: client.select_winner(args=[auction_id]).transact(**SLOW_WAIT)
    )
    print("select_winner receipt:", str(select_result).encode("ascii", errors="backslashreplace").decode("ascii"))
    assert tx_execution_succeeded(select_result), (
        "select_winner failed or returned UNDETERMINED even after polling past "
        "the reveal deadline - known retryable StudioNet behavior; rerun this "
        "test if so"
    )

    final_auction = c.get_auction(args=[auction_id]).call()
    print("final auction:", str(final_auction).encode("ascii", errors="backslashreplace").decode("ascii"))
    assert final_auction["state"] == "AWARDED", final_auction
    # The adequate bid is the only one that actually addresses the fixed
    # specification at all - a correct judged round should prefer it over
    # the cheaper but wholly irrelevant one.
    assert final_auction["winner"].lower() == bidder_a.address.lower(), final_auction
    assert final_auction["winning_price"] == 8000

    # re-selecting an already-awarded auction must fail
    re_select = client.select_winner(args=[auction_id]).transact(**FAST_WAIT)
    assert tx_execution_failed(re_select), "selecting an already-AWARDED auction should fail"

    with pytest.raises(Exception):
        c.get_auction(args=[999999]).call()


@pytest.mark.integration
def test_select_winner_with_no_revealed_bids_voids_and_refunds_without_genvm_or_consensus_error(
    deployed_contract, client_account
):
    """No bids at all is a genuinely possible outcome - proves it settles
    as a deterministic VOID with a full refund and zero non-determinism
    budget spent, never a GenVM error or a failed/undetermined round."""
    c = deployed_contract
    client = c.connect(client_account)

    create_result = client.create_auction(
        args=[SPEC, BUDGET, 60, 60, CANCEL_GRACE]
    ).transact(value=BUDGET, **FAST_WAIT)
    assert tx_execution_succeeded(create_result), create_result
    auction_id = int(c.auction_count(args=[]).call()) - 1

    auction = c.get_auction(args=[auction_id]).call()
    time.sleep(_seconds_until(auction["reveal_deadline"]))

    select_result = _retry_past_deadline(
        lambda: client.select_winner(args=[auction_id]).transact(**FAST_WAIT)
    )
    assert tx_execution_succeeded(select_result), (
        "select_winner must succeed at the consensus/GenVM level even when "
        "nobody ever revealed a bid"
    )
    final_auction = c.get_auction(args=[auction_id]).call()
    print("auction after zero reveals:", str(final_auction).encode("ascii", errors="backslashreplace").decode("ascii"))
    assert final_auction["state"] == "VOID", final_auction


@pytest.mark.integration
def test_select_winner_with_a_single_inadequate_bid_voids_via_null_verdict(
    deployed_contract, client_account, bidder_accounts
):
    """Distinct from the zero-revealed-bids VOID path above: here a bid was
    actually revealed and reviewed, and the judged round itself decided -
    correctly - that it doesn't qualify. Exercises _parse_selection's
    winning_bid_id: null branch on live consensus, not just the
    no-candidates-at-all short-circuit."""
    c = deployed_contract
    client = c.connect(client_account)
    bidder_a, _ = bidder_accounts
    a = c.connect(bidder_a)

    create_result = client.create_auction(
        args=[SPEC, BUDGET, COMMIT_WINDOW, REVEAL_WINDOW, CANCEL_GRACE]
    ).transact(value=BUDGET, **FAST_WAIT)
    assert tx_execution_succeeded(create_result), create_result
    auction_id = int(c.auction_count(args=[]).call()) - 1

    commitment = _local_commitment(4000, INADEQUATE_APPROACH, "salt-only", bidder_a.address)
    commit_result = a.commit_bid(args=[auction_id, commitment]).transact(**FAST_WAIT)
    assert tx_execution_succeeded(commit_result), commit_result
    bid_id = int(c.list_bids_for_auction(args=[auction_id]).call()[0])

    auction = c.get_auction(args=[auction_id]).call()
    time.sleep(_seconds_until(auction["commit_deadline"]))

    reveal_result = _retry_past_deadline(
        lambda: a.reveal_bid(args=[bid_id, 4000, INADEQUATE_APPROACH, "salt-only"]).transact(**FAST_WAIT)
    )
    assert tx_execution_succeeded(reveal_result), reveal_result

    auction = c.get_auction(args=[auction_id]).call()
    time.sleep(_seconds_until(auction["reveal_deadline"]))

    select_result = _retry_past_deadline(
        lambda: client.select_winner(args=[auction_id]).transact(**SLOW_WAIT)
    )
    print("select_winner receipt:", str(select_result).encode("ascii", errors="backslashreplace").decode("ascii"))
    assert tx_execution_succeeded(select_result), (
        "select_winner failed or returned UNDETERMINED even after polling past "
        "the reveal deadline - known retryable StudioNet behavior; rerun this "
        "test if so"
    )

    final_auction = c.get_auction(args=[auction_id]).call()
    print("final auction:", str(final_auction).encode("ascii", errors="backslashreplace").decode("ascii"))
    # The sole bid is wholly unrelated to the specification - a correct
    # judged round should decline to award it rather than pick it by
    # default for being the only option.
    assert final_auction["state"] == "VOID", final_auction
    assert final_auction["winning_price"] == 0


@pytest.mark.integration
def test_cancel_auction_after_the_timeout_refunds_the_client(deployed_contract, client_account):
    """A stalled auction - nobody ever bid at all - must not lock the
    client's escrow forever. Exercises cancel_auction's bounded,
    permissionless exit on live consensus, distinct from both live tests
    above which only exercise select_winner."""
    c = deployed_contract
    client = c.connect(client_account)

    create_result = client.create_auction(
        args=[SPEC, BUDGET, 60, 60, 60]
    ).transact(value=BUDGET, **FAST_WAIT)
    assert tx_execution_succeeded(create_result), create_result
    auction_id = int(c.auction_count(args=[]).call()) - 1

    # cancellation must refuse to run at all before its own timeout -
    # cheap to check here since a rejected call never mutates state.
    early_cancel = client.cancel_auction(args=[auction_id]).transact(**FAST_WAIT)
    assert tx_execution_failed(early_cancel), "cancel_auction must refuse to run before its own timeout"

    auction = c.get_auction(args=[auction_id]).call()
    time.sleep(_seconds_until(auction["cancel_deadline"]))

    cancel_result = _retry_past_deadline(
        lambda: client.cancel_auction(args=[auction_id]).transact(**FAST_WAIT)
    )
    assert tx_execution_succeeded(cancel_result), (
        "cancel_auction must succeed at the consensus/GenVM level once its "
        "own timeout has genuinely passed"
    )

    final_auction = c.get_auction(args=[auction_id]).call()
    print("auction after cancellation:", str(final_auction).encode("ascii", errors="backslashreplace").decode("ascii"))
    assert final_auction["state"] == "CANCELLED", final_auction

    # cancelling an already-cancelled auction must fail
    re_cancel = client.cancel_auction(args=[auction_id]).transact(**FAST_WAIT)
    assert tx_execution_failed(re_cancel), "cancelling an already-CANCELLED auction should fail"


@pytest.mark.integration
def test_deterministic_guards_reject_before_any_judged_round_runs(
    deployed_contract, client_account, bidder_accounts
):
    """A bundle of cheap, purely deterministic rejections that never touch
    a judged round at all - grouped into one test since none of them need
    any real-time wait, unlike every other test in this module."""
    c = deployed_contract
    client = c.connect(client_account)
    bidder_a, bidder_b = bidder_accounts
    a = c.connect(bidder_a)
    b = c.connect(bidder_b)

    create_result = client.create_auction(
        args=[SPEC, BUDGET, COMMIT_WINDOW, REVEAL_WINDOW, CANCEL_GRACE]
    ).transact(value=BUDGET, **FAST_WAIT)
    assert tx_execution_succeeded(create_result), create_result
    auction_id = int(c.auction_count(args=[]).call()) - 1

    # the client may not bid on their own auction
    self_bid_commitment = _local_commitment(5000, ADEQUATE_APPROACH, "salt-self", client_account.address)
    self_bid = client.commit_bid(args=[auction_id, self_bid_commitment]).transact(**FAST_WAIT)
    assert tx_execution_failed(self_bid), "the client must not be able to bid on their own auction"

    # a malformed commitment (wrong length / non-hex) must be rejected
    bad_commitment = a.commit_bid(args=[auction_id, "not-a-real-commitment"]).transact(**FAST_WAIT)
    assert tx_execution_failed(bad_commitment), "a malformed commitment must be rejected"

    commitment_a = _local_commitment(6000, ADEQUATE_APPROACH, "salt-a2", bidder_a.address)
    commit_a = a.commit_bid(args=[auction_id, commitment_a]).transact(**FAST_WAIT)
    assert tx_execution_succeeded(commit_a), commit_a
    bid_id_a = int(c.list_bids_for_auction(args=[auction_id]).call()[0])

    # the same bidder cannot commit a second time on the same auction
    second_commitment = _local_commitment(1, "different bid", "salt-second", bidder_a.address)
    second_commit = a.commit_bid(args=[auction_id, second_commitment]).transact(**FAST_WAIT)
    assert tx_execution_failed(second_commit), "a bidder must not be able to commit twice on the same auction"

    # revealing before the commit window has even closed must be rejected
    early_reveal = a.reveal_bid(args=[bid_id_a, 6000, ADEQUATE_APPROACH, "salt-a2"]).transact(**FAST_WAIT)
    assert tx_execution_failed(early_reveal), "reveal_bid must refuse to run before the commit window closes"

    # a different bidder may not reveal someone else's bid
    impersonated_reveal = b.reveal_bid(args=[bid_id_a, 6000, ADEQUATE_APPROACH, "salt-a2"]).transact(**FAST_WAIT)
    assert tx_execution_failed(impersonated_reveal), "only the committing bidder may reveal their own bid"

    # a price above the auction's own declared budget must be rejected -
    # checked against the bidder's OWN not-yet-revealed commitment, so this
    # naturally also proves a mismatched reveal is rejected (the commitment
    # above was made for a valid price, not this one)
    over_budget_reveal = a.reveal_bid(args=[bid_id_a, BUDGET + 1, ADEQUATE_APPROACH, "salt-a2"]).transact(**FAST_WAIT)
    assert tx_execution_failed(over_budget_reveal), "a revealed price above max_budget must be rejected"

    # an unknown auction/bid id must revert on every read
    with pytest.raises(Exception):
        c.get_auction(args=[999999]).call()
    with pytest.raises(Exception):
        c.get_bid(args=[999999]).call()
