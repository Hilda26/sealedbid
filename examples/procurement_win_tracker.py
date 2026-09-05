# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *

# ---------------------------------------------------------------------------
# ProcurementWinTracker - a worked consumer of the SealedBidProcurement
# primitive.
#
# Any contract that wants to reward a supplier's track record - a
# marketplace ranking, a tier unlocked by past wins - can read a
# SealedBidProcurement auction's settled state instead of building its own
# commit-reveal or bid-judging machinery. This example contains none of
# SealedBidProcurement's hashing, revealing, or selection logic; it only
# reads the state SealedBidProcurement already settled.
#
# One subtlety worth naming: SealedBidProcurement's `winner` field is
# always populated with a fixed all-zero placeholder address at auction
# creation, since the storage field exists before there is any winner to
# name. That placeholder stays in the field for the entire life of an
# auction that never gets AWARDED - reading `winner` on an OPEN, VOID,
# ERRORED, or CANCELLED auction returns that same placeholder, never an
# error, so a consumer that reads `winner` without first checking `state
# == "AWARDED"` would silently credit the zero address as though it had
# won. credit_if_awarded therefore gates strictly on state before it ever
# looks at winner.
# ---------------------------------------------------------------------------


@gl.contract_interface
class ISealedBidProcurement:
    class View:
        def get_auction(self, auction_id: u256) -> dict: ...

    class Write:
        pass


class ProcurementWinTracker(gl.Contract):
    auction_house_address: Address
    credited: TreeMap[u256, bool]
    wins_by_supplier: TreeMap[str, u256]

    def __init__(self, auction_house_address: str):
        addr = auction_house_address if isinstance(auction_house_address, Address) else Address(auction_house_address)
        self.auction_house_address = addr

    @gl.public.write
    def credit_if_awarded(self, auction_id: u256) -> None:
        if auction_id in self.credited:
            raise gl.vm.UserError("already credited for this auction")

        house = ISealedBidProcurement(self.auction_house_address)
        auction = house.view().get_auction(auction_id)

        if auction["state"] != "AWARDED":
            raise gl.vm.UserError("auction has not reached AWARDED")

        supplier_key = auction["winner"].lower()
        current = self.wins_by_supplier.get(supplier_key, u256(0))
        self.wins_by_supplier[supplier_key] = u256(int(current) + 1)
        self.credited[auction_id] = True

    @gl.public.view
    def wins_for(self, supplier: str) -> u256:
        return self.wins_by_supplier.get(supplier.lower(), u256(0))
