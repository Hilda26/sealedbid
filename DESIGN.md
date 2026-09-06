# Design — SealedBidProcurement

## 1. Non-determinism budget

Exactly **one** non-deterministic operation per `select_winner` call, and only
when at least one bid was revealed:

- A single `gl.eq_principle.prompt_comparative` block whose leader reviews the
  fixed list of already-revealed bids (price and written approach, both
  already-committed on-chain data) against the auction's fixed specification
  and either names the winning `bid_id` or returns `null`.

When zero bids were revealed, `select_winner` refunds the client and settles
the auction deterministically, spending no non-determinism budget at all -
the same zero-cost short-circuit MilestoneEscrow's `resolve_milestone` never
needed but this contract's genuinely-possible "nobody bid" case does.

## 2. Why this is a new shape, not a relabeled copy of anything else in this
   portfolio

Every earlier judged contract in this portfolio verifies a single claim: does
*this one* deliverable satisfy a spec (MilestoneEscrow), is *this one* fact
true (DisputeArbiter), does *this one* page still say what it said before
(DriftWatch). `SealedBidProcurement` asks a structurally different question:
given N independently-submitted, already-public candidates, *which one* best
satisfies a fixed specification. That is a selection among competitors, not
a verification of one party's claim - closer to a judged tournament than a
judged inspection.

The deterministic half is equally new: no earlier contract needed to hide
data from other participants before a deadline, so none of them needed a
commit-reveal state machine. `commit_bid` stores only a `sha256` hash of a
bidder's price, approach, salt, and their own address; `reveal_bid` later
recomputes that hash from the plaintext values and rejects any mismatch. A
bidder who reveals early, or a validator/observer watching the mempool,
cannot get an advantage from another bidder's real price before that bidder
chooses to reveal it - the entire point of sealing.

## 3. What stays deterministic

- Every auction's specification and maximum budget - declared once, at
  creation, immutable afterward, exactly like every other contract in this
  portfolio's "spec never changes after the parties agree to it" discipline.
- The full budget is escrowed up front, at creation - never a per-bid
  deposit, and never a client able to solicit bids without the funds to pay
  the winner already locked.
- The commit-reveal check itself: `reveal_bid` is a pure hash-equality
  comparison against the bidder's own prior commitment, with no model
  involved. A revealed price above the declared `max_budget` is rejected
  deterministically before it ever reaches a judged round.
- Binding the bidder's own address into the commitment preimage
  (`price:approach:salt:bidder_hex`) - without it, a bidder could copy
  another bidder's published commitment hash verbatim as their own,
  effectively "front-running" a bid whose contents they haven't actually
  seen (since the commitment alone reveals nothing) but could still cause
  ambiguity about who is entitled to reveal which values. Binding the
  address makes each commitment only revealable by the bidder who made it.
- Output sanitization: `_parse_selection` accepts only a `winning_bid_id`
  that is `null` or one of *this specific auction's* own revealed bid ids -
  a hallucinated or reused id from anywhere else is rejected outright, the
  same discipline `_parse_review`'s closed verdict set enforces elsewhere in
  this portfolio.
- Every fund transfer: exactly the winning bid's own already-declared price
  moves to exactly that bid's own already-committed bidder, and exactly the
  remainder moves back to the already-named client. The model is never asked
  "how much" or "to whom" - only "which bid_id, if any."

## 4. Time: exactly one consensus-bound value, never a local wall-clock read
   inside a judged method

The StructuredDataOracle correction, applied here from the first version.
`select_winner`'s leader closure is the only place this contract reads a
clock inside a method that also runs an `eq_principle` round; that single
`observed_at` rides in the accepted envelope and becomes `settled_at`.

`create_auction`, `commit_bid`, `reveal_bid`, and `cancel_auction` contain no
nondet call at all and plainly call `datetime.now()`/`_now_iso()` directly -
matching the established, unreviewed-negatively pattern already used
throughout this portfolio (MilestoneEscrow's `submit_milestone` and
`cancel_milestone`, DisputeArbiter's and ReputationRegistry's every
`timeout_*` method). The distinction that matters is not "does this method
touch a clock" but "does this method also run an `eq_principle` round" -
only there does a second, uncoordinated clock read have anything to diverge
against.

## 5. A fifth instance of the HandleGuard lesson: select vs. cancel

The same shape MilestoneEscrow's review corrected for `resolve_milestone`
vs. `cancel_milestone` appears here between `select_winner` and
`cancel_auction`: the judged round takes real time, and the client's
bounded, deterministic escape hatch can fire on the *same auction* while
that round is still in flight. Without a fix, `select_winner` starts →
`cancel_auction` fires, refunding the client and marking the auction
`CANCELLED` → the round returns a winner → the contract pays that bidder
anyway would double-spend the escrowed budget. The fix is the same pattern,
verbatim:

```python
state_at_round_start = a.state
...
raw_result = gl.eq_principle.prompt_comparative(leader, SELECT_PRINCIPLE)
...
if a.state != state_at_round_start:
    return   # something else already wrote the authoritative outcome
```

**A stated limitation, not a claimed proof**, exactly as documented for the
same lesson in MilestoneEscrow and DriftWatch: the genuine in-flight
interleaving cannot be mechanically constructed in gltest's synchronous
direct mode. What direct-mode testing does prove is the outer boundary this
guard reduces to - an auction already `CANCELLED` before `select_winner` is
even called is correctly refused
(`test_select_winner_refuses_to_act_over_a_cancellation_that_lands_first`).
The guard's logic does not distinguish "changed before the call" from
"changed during the call," so proving the former is a genuine, if partial,
proof of the latter's mechanism.

## 6. The DisputeArbiter lesson: a bounded, client-only exit

`cancel_auction` gives the client's escrowed funds a bounded,
permissionless-to-trigger exit if selection never runs, or keeps failing to
parse - the same fund-lock shape DisputeArbiter's review corrected, and the
same two choices already established elsewhere in this portfolio:

- **Client-only beneficiary, not the caller.** Anyone may trigger the
  cancellation once the timeout objectively holds, but the refund always
  goes to the client who escrowed the funds.
- **Anchored to a fixed point, not the most recent attempt.**
  `cancel_deadline` is computed once, at creation, from the declared window
  lengths, and never moves - a failed `select_winner` retry cannot push it
  back by spamming doomed attempts.

`cancel_auction` is eligible from `OPEN` (nobody ever ran a selection, or
nobody bid at all) and from `ERRORED` (selection ran but its output kept
failing to parse); it is never eligible from `AWARDED`, `VOID`, or
`CANCELLED`, since each of those already resolved the escrow one way or
another and there is nothing left to reclaim.

## 6a. The SourceConsensus lesson, checked and confirmed structurally
   inapplicable

SourceConsensus's own review correction addressed a prior successful round's
stale per-source labels surviving visible after a later round failed to
parse. The analogous field here would be `reason` (or `winner` /
`winning_price`) appearing to describe a completed selection after a later
`select_winner` attempt errored. That cannot happen: `a.reason` is written
only in the two branches that follow a genuinely parsed selection (the
`VOID`-via-null branch and the `AWARDED` branch), and is never touched on
the `ERRORED` path, so it simply stays at its creation-time default (`""`)
through any number of failed retries. The same is true of `winner` and
`winning_price`, which are only ever written in the `AWARDED` branch itself.
An auction that cycles `ERRORED` → retried → `ERRORED` again can never show
a prior attempt's selection as though it still applied - structurally
unable to go stale, not defended against after the fact.

## 6b. The DriftWatch lesson, checked point by point rather than assumed
   inapplicable

A later review of DriftWatch found two real gaps: a judged verdict's
associated payload (its replacement summary) could disagree between
validators while the verdict itself agreed, and the accepted round
timestamp was excluded from cross-validator comparison but otherwise left
completely unconstrained despite controlling a cooldown and permanent
stored history. Both concerns were checked directly against this contract
rather than assumed away, since `select_winner`'s judged output has the
same two-field shape (`winning_bid_id` + `reason`) DriftWatch's did
(`verdict` + `summary`).

**The payload gap does not apply here, for a structural reason DriftWatch
didn't have available to it.** DriftWatch's replacement summary *was* the
thing being stored as new ground truth (`pending_summary`, later adoptable
as the anchor), sourced entirely from the model's own free text - so an
unbound summary really could let two validators agree on the verdict while
disagreeing on what became true. Here, the only value that drives any
consequence - which bid gets paid, and how much - is `winning_bid_id`,
which the equivalence principle already binds exactly (validators must
name the identical `bid_id` or both return `null`), and which
`_parse_selection` independently re-validates against this auction's own
`valid_bid_ids` after the round. The paid amount is never taken from
anything the model writes - it is looked up as `winning_bid.price`,
already-committed on-chain data from `reveal_bid`, long before this round
ever ran. There is no path by which the model's own prose could move a
different amount or a different recipient than what the deterministic bid
record already fixed, so there is no analogous "agreed verdict, disagreed
substance" gap to close. `reason` is excluded from equivalence
deliberately, on purpose, for the same reason `last_reason` (MilestoneEscrow),
`resolution_reason` (DisputeArbiter, ReputationRegistry) all are: it is
pure explanatory text no code path ever reads, so requiring validators to
agree on its exact wording would manufacture spurious disagreement for
zero safety benefit - unlike DriftWatch's summary, it is not load-bearing.

**The timestamp gap does not apply behaviorally either, but was hardened
anyway.** `observed_at`/`settled_at` gates nothing in this contract: every
timing decision that actually controls behavior - `commit_deadline`,
`reveal_deadline`, `cancel_deadline` - is computed once, deterministically,
from `create_auction`'s own local clock read at creation, and never changes
again. `select_winner`'s own eligibility check
(`state in (OPEN, ERRORED)`) carries no cooldown at all, so nothing ever
reads `settled_at` to decide whether a later call is allowed. An
implausible leader-proposed `observed_at` could therefore only ever produce
a cosmetically wrong `settled_at` value, never a fund-safety or
availability issue. That said, the fix costs nothing to apply defensively:
`select_winner` now also rejects a round whose `observed_at` does not fall
strictly after the auction's own immutable `created_at`, checked against
already-committed on-chain state rather than any local clock - the same
shape as DriftWatch's fix, applied here even though nothing in this
contract's own behavior required it.

Worth being precise rather than overclaiming: given `MIN_WINDOW_SECONDS`
forces every window to be a positive duration, `select_winner`'s own outer
gate (`now >= reveal_deadline`, itself always strictly after
`created_at`) already guarantees this new check can never actually fire
through the public API today - every validator must independently observe
that gate passing before any of them ever reaches the judged round, so
`observed_at` is provably always later than `created_at` by construction.
The check is retained anyway as a hard invariant rather than leaning on
that gate alone holding forever (for instance, across a future version
that ever allowed a zero-length window) - free to check, correct as an
assertion of intent, and not claimed to be reachable by any test today.

## 7. Storage layout

```
Auction:
  id: u256
  client: Address
  spec: str                                  # immutable
  max_budget: u256                           # immutable
  commit_deadline: str                       # immutable, set at creation
  reveal_deadline: str                       # immutable, set at creation
  cancel_deadline: str                       # immutable, set at creation
  state: str                                  # OPEN | AWARDED | VOID | ERRORED | CANCELLED
  winner: Address                             # all-zero placeholder until AWARDED
  winning_price: u256
  reason: str
  created_at: str
  settled_at: str                             # consensus-bound when set by select_winner

Bid:
  id: u256
  auction_id: u256
  bidder: Address
  commitment: str                             # sha256 hex digest, set at commit
  revealed: bool
  price: u256                                 # 0 until revealed
  approach: str                               # "" until revealed
  revealed_at: str
```

`auctions: TreeMap[u256, Auction]` and `bids: TreeMap[u256, Bid]`, each keyed
by an incrementing counter - the same registry pattern as the rest of the
portfolio. `auction_bid_ids: TreeMap[u256, DynArray[u256]]` indexes bids by
auction, mirroring `engagement_milestone_ids` in MilestoneEscrow.
`auction_bidder_to_bid: TreeMap[u256, TreeMap[str, u256]]` is a nested map
enforcing one commitment per bidder per auction - the same nested-`TreeMap`
idiom the portfolio's own scaffold example (`football_bets.py`) already
established for a per-address-per-key lookup.

## 8. The consumer interface

```python
@gl.contract_interface
class ISealedBidProcurement:
    class View:
        def get_auction(self, auction_id: u256) -> dict: ...
    class Write:
        pass
```

**Pull, not push.** See `examples/procurement_win_tracker.py` for a worked
consumer, and the subtlety documented there: `winner` carries a fixed
all-zero placeholder on every auction that never reached `AWARDED`, so a
real consumer must gate on `state == "AWARDED"` before ever reading it.

## 9. Trust model

| Role | Powers | Cannot |
|---|---|---|
| Client | Declare the specification, budget, and every window length once, at creation, funding the full budget up front; trigger `cancel_auction` (though anyone may) | Cannot edit the spec or budget after creation; cannot bid on their own auction; cannot pick the winner themselves - only consensus can; cannot reclaim funds before the declared cancel timeout |
| Bidder | Commit one hidden bid per auction, then reveal it inside the reveal window | Cannot see another bidder's price or approach before that bidder reveals it; cannot change a bid after committing to it (the hash binds every value); cannot reveal a price above the auction's own declared budget |
| Anyone (permissionless) | Call `select_winner` or `cancel_auction` | Cannot select a winner other than whichever bid_id consensus names (or none); cannot redirect a payout to themselves - `select_winner`'s payout always goes to the winning bid's own bidder and the refund always goes to the client, `cancel_auction`'s refund always goes to the client |

## 10. Latency budget

- `create_auction`, `commit_bid`, `reveal_bid`, `cancel_auction`: pure
  deterministic writes, ~20-40s on StudioNet.
- `select_winner`: one consensus round, one `exec_prompt` over already-known
  on-chain data - no live fetch involved, so structurally lighter than every
  other judged write in this portfolio (all of which fetch a live page or
  external source first). Comparable in shape to a version of
  StructuredDataOracle's `check_feed` with the fetch step removed.
