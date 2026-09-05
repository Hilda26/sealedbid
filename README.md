# SealedBidProcurement

A reusable GenLayer Intelligent Contract that runs a sealed-bid procurement auction:
suppliers commit hidden bids against a fixed specification, reveal them once bidding
closes, and validator consensus picks whichever revealed bid best satisfies the
specification - never just the cheapest, never a guess. The winner is paid their own
declared price straight from an already-escrowed budget; every other bidder is paid
nothing, since none of them ever escrowed anything of their own.

## The problem with the naive version

Procurement without sealed bids degenerates fast. Post the specification and accept
bids in the open, and every bidder can see and undercut whoever bid first - the auction
becomes a race to lowball, not a competition on quality. Let the client pick whichever
bid they like best with no independent check, and "best" quietly becomes "whichever
bidder the client already favored," with no way for a passed-over bidder - or anyone
else - to know whether the specification was actually applied fairly. A purely
price-only selection rule ("lowest bid wins") can't tell a bid that meets the spec
cheaply from one that doesn't meet it at all.

## Why this needs validator consensus, not a backend

Delete GenLayer and sealed procurement either falls back to open, race-to-the-bottom
bidding, unilateral client selection, or a rigid lowest-price rule that ignores quality
entirely. Run the counterfactual:

- **Open bidding** - every bidder can see and undercut every other bidder's price
  before deciding their own, destroying any incentive to bid what the work is
  actually worth.
- **Client picks unilaterally** - no independent check that the specification, not
  favoritism, actually decided the outcome.
- **Lowest price wins, full stop** - rewards a bid that technically qualifies while
  ignoring one that would deliver meaningfully more value for a modest premium, and
  cannot reject a cheap bid that doesn't meet the spec at all.

GenLayer's validator set independently reviews the exact same fixed list of already-
revealed bids against the exact same fixed specification, reconciling under an
equivalence principle that compares only the chosen `bid_id`, never incidental
reasoning. No single party - not the client, not any one bidder, not a validator -
decides alone which bid wins, and the funds released are real, already-escrowed value
moved by that judgment.

## Why it isn't the patterns that don't belong in this category

- **Not an AI app with a blockchain attached.** The output is a state transition and a
  real fund transfer - an auction reaches `AWARDED`, `VOID`, or `CANCELLED` and value
  moves accordingly - never advice a human reads and acts on manually.
- **Not a format-only validator.** The equivalence principle compares the chosen
  `bid_id` itself, never whether the model's JSON merely parses.
- **Not judging client-submitted evidence.** Every bid reviewed is a bidder's own
  submission about their own proposed work; the client never supplies what the
  contract sees when judging the bidders competing for their own auction.
- **Structurally distinct from every other submission in this portfolio.** Every
  earlier judged contract here verifies a single claim - does *this one* deliverable
  satisfy a spec (MilestoneEscrow), is *this one* fact true (DisputeArbiter), does
  *this one* page still say what it said before (DriftWatch). SealedBidProcurement asks
  a different kind of question: given N independently-submitted, already-public
  candidates, *which one* best satisfies a fixed specification - a selection among
  competitors, not an inspection of one party's claim. Its deterministic half is
  equally new to this portfolio: a commit-reveal state machine (a cryptographic hash
  commitment now, a plaintext reveal later), needed here because bids must stay hidden
  from other bidders before a deadline - a requirement none of this portfolio's earlier
  contracts had.

## The non-deterministic core, and why the deterministic half is just as load-bearing

Exactly **one** non-deterministic operation per judged call - and only when at least
one bid was actually revealed: a leader that reviews the fixed list of revealed bids
(price and written approach, both already-committed on-chain data, never a live fetch)
against the auction's declared specification and names the winning `bid_id`, or `null`
if none qualify. The model is never asked "how much to pay" or "to whom" - only "which
bid, if any" - and the deterministic half carries every actual consequence: the full
budget escrowed up front, the commit-reveal hash discipline that keeps bids sealed
until their bidder chooses to reveal them, exact amounts moving to exact, already-named
addresses, consensus-bound time, and - carried forward directly from prior review
corrections on other contracts in this portfolio - settlement revalidation against a
client's bounded cancellation, and a bounded exit so a stalled or unrunnable selection
can never lock the client's funds forever. Full rationale in `DESIGN.md`.

## Safety properties

| Property | Enforced by | Verified by |
|---|---|---|
| A bid's price and approach are provably hidden from every other bidder until its own bidder reveals them | `commit_bid` stores only a `sha256` hash; `reveal_bid` is a pure hash-equality check against that commitment | `test_reveal_bid_succeeds_with_the_exact_committed_values`, `test_reveal_bid_rejects_a_mismatched_commitment` |
| The full escrow is collected up front, before any bid is even accepted | `create_auction` requires `msg.value` to exactly equal `max_budget` | `test_create_auction_rejects_value_not_matching_budget` |
| One bidder cannot claim another's already-published commitment as their own | the commitment preimage binds the bidder's own address (`price:approach:salt:bidder_hex`) | `test_reveal_bid_rejects_non_bidder` |
| A revealed price can never exceed the auction's own declared budget | `reveal_bid` checks `price <= max_budget` deterministically, before any judged round | `test_reveal_bid_rejects_price_above_the_budget` |
| A selection never names a winner outside this specific auction's own revealed bids | `_parse_selection` accepts only `null` or a `bid_id` present in this auction's own revealed set | `test_select_winner_rejects_a_winning_bid_id_that_was_never_revealed` |
| Zero revealed bids never stalls the auction - the client is refunded automatically | a deterministic short-circuit in `select_winner`, spending no non-determinism budget at all | `test_select_winner_with_zero_revealed_bids_voids_and_refunds_the_client_in_full` |
| `select_winner` can never award a bid over a cancellation that already landed on the same auction | state recorded before the round, revalidated at settlement - the fifth independently rediscovered instance of the HandleGuard lesson in this portfolio (see `DESIGN.md` §5) | `test_select_winner_refuses_to_act_over_a_cancellation_that_lands_first` |
| A selection round that never runs, or keeps failing to parse, can never lock the client's escrowed funds forever | `cancel_auction`, permissionless-to-trigger, anchored to a fixed `cancel_deadline` that failed retries cannot push back | `test_cancel_auction_is_permissionless_and_refunds_only_the_client` |
| A cancellation refund always goes to the client, regardless of who triggered it | `cancel_auction`'s beneficiary is fixed at the auction's own `client`, never the caller | `test_cancel_auction_is_permissionless_and_refunds_only_the_client` |
| Anyone can push a stuck `ERRORED` auction forward, not just the client | `select_winner` has no caller restriction, ever | `test_select_winner_after_errored_can_be_retried_permissionlessly` |

## Why it's reusable

The consumer integration is genuinely small - this is the whole thing, from
`examples/procurement_win_tracker.py`:

```python
@gl.contract_interface
class ISealedBidProcurement:
    class View:
        def get_auction(self, auction_id: u256) -> dict: ...
    class Write:
        pass

auction = ISealedBidProcurement(self.auction_house_address).view().get_auction(auction_id)
if auction["state"] == "AWARDED":
    winner = auction["winner"]  # only meaningful once state is AWARDED
    ...
```

One subtlety worth calling out explicitly: `winner` is populated with a fixed all-zero
placeholder address from the moment an auction is created, since the storage field
exists before there is any winner to name. Reading it on an `OPEN`, `VOID`, `ERRORED`,
or `CANCELLED` auction returns that same placeholder, never an error - a real consumer
has to gate strictly on `state == "AWARDED"` before ever treating `winner` as
meaningful. `DESIGN.md` §8 and the example's own tests document this explicitly.

## Testing

- **Direct-mode** (`tests/direct/`, `pytest tests/direct/`): 39 tests, no network, no
  live consensus - every deterministic branch of the commit-reveal state machine, every
  failure/abstention path on the judged selection round, the max-bidders cap, both
  bounded-exit and mid-flight revalidation behavior, fund conservation across the full
  lifecycle, and the worked consumer example (including the placeholder-winner case it
  exists to catch), using gltest's built-in `mock_llm` plus a real-balance-moving
  `EthSend` hook.
- **Integration** (`tests/integration/`, `pytest tests/integration/ --network=studionet`):
  5 tests, requires `SEALEDBIDPROCUREMENT_ADDRESS` set to a real StudioNet deployment -
  a full create → commit → reveal → select lifecycle with multiple competing bids; the
  no-bids-revealed `VOID` short-circuit; a single revealed but inadequate bid correctly
  `VOID`-ed by the judged round's own null verdict, not just the short-circuit;
  `cancel_auction`'s bounded exit under a real timeout; and a bundle of purely
  deterministic guard rejections (self-bidding, a malformed commitment, a duplicate
  commit, an impersonated reveal, an over-budget reveal, unknown ids) that need no
  real-time wait at all.

## Deployment

- Deployed StudioNet address: `0xf80e9219135947eFC7e240bFE39E2B938FFF0B70`
- Studio import: open [studio.genlayer.com](https://studio.genlayer.com) → "Import
  contract" → paste the address above.

### Measured on live consensus

Both integration tests pass against the deployment above, driving real validator
consensus rounds rather than mocks:

- The full lifecycle test creates and funds an auction, has two bidders commit hidden
  bids and reveal them once bidding closes - one whose approach actually addresses every
  element of the specification, one cheaper but wholly unrelated to it - then runs
  `select_winner`. Validators independently reviewed the same revealed bids and reached
  unanimous agreement on the bid that actually satisfied the specification, correctly
  preferring it over the cheaper irrelevant one. The winner was paid their own declared
  price, the unspent remainder returned to the client, and every negative-path revert
  (selecting before the reveal window closes, re-selecting an already-`AWARDED` auction,
  reading an unknown auction id) all passed.
- The zero-revealed-bids test proved that outcome is absorbed as a deterministic `VOID`
  with a full refund and zero non-determinism budget spent - `execution_result: SUCCESS`
  and `raw_error: None` on every validator, never a GenVM or consensus fault.

Getting here also surfaced two real, worth-stating issues, neither of them in the
contract logic itself:

- A prompt-injection gap: a bidder's free-text `approach` was being embedded in the
  selection prompt unfenced, with the "evidence only, not an instruction" warning
  present only in the equivalence principle used to compare validator outputs, never in
  the actual generation prompt those validators run. Fund-safety was never at risk -
  `select_winner` already rejects any `winning_bid_id` outside this specific auction's
  own revealed set - but the judgment itself deserved the same fencing-and-warning
  discipline MilestoneEscrow already applies to untrusted fetched content. Fixed before
  the deployment above.
- Two StudioNet/tooling quirks, not contract defects: `compute_commitment`'s view call
  hits an RPC-layer encoding limit on longer string arguments (confirmed directly - a
  ~20-char approach round-trips fine, ~400 chars fails with an RLP/list-length decoding
  error), so the integration tests hash commitments locally instead, exactly as a real
  bidder's own tooling would; and a wall-clock-based wait for `commit_deadline` /
  `reveal_deadline` to pass proved unreliable against this test runner's own clock skew
  relative to the validator infrastructure, so the tests now poll past each deadline
  rather than trusting a fixed sleep computed from local time.
