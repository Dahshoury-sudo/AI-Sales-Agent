"""Diagnostic probes for the ordering guards removed with the astra checkout rewrite.

NOT part of the test suite — the filename deliberately does not match Django's
`test*.py` discovery pattern, because these probes assert DESIRED behaviour and
are expected to fail where the restored df1e3cd flow has no deterministic guard.

Run explicitly:
    python scripts/run_offline_tests.py products.ordering_probe --log artifacts/bug_fix_validation/ordering-probe.log

Every probe stubs BOTH model calls in the order path — the order extractor
(`order_service.chat`, via `BugFixture.turn`) and the perfume resolver
(`product_resolver.chat`) — so the result reflects the ordering code, not a
blocked HTTP call. A probe that leaks HTTP is invalid: the resolver fails closed
and returns nothing, which looks identical to a guard holding.

Each probe's docstring says what a failure actually means:
  HARD  — the flow itself produces a wrong order; no model wording can save it.
  SOFT  — there is no deterministic guard, so correctness is delegated to the
          order-extractor prompt. Production may still be right when the real
          LLM resolves the reference; it is simply not enforced in code.

Expected state after the house-alias fix (conversations 1093/1094/1096): **2 failures, probes 2
and 3 only.** Any other failure is a regression.
  probe 2 — a quantity change on an unresolved line repeats the previous reply
            verbatim. The quantity IS saved correctly; only the wording repeats.
  probe 3 — no deterministic ordinal guard; ordinals go through the extractor
            prompt's POSITIONAL REFERENCE rules instead. Working as designed.
Probe 5 — once a KNOWN GAP — now passes through the same deterministic house check
`order_service` uses: "هاخد ده امبيرو" fires `naming.house_mentioned_alone`, and a single
extractor line naming a different house's product is asked about instead of carted.
"""
import json
from contextlib import contextmanager
from unittest.mock import patch

from products.models import Brand, Cart, Order, Product
from products.services.conversation_service import save_message
from products.test_bug_regressions import BugFixture


class OrderingGuardProbe(BugFixture):
    def setUp(self):
        super().setUp()
        # "امبيرو" is a house while its brand is stocked (`naming._HOUSE_ALIASES` resolves the
        # spelling and then requires the brand to exist in this store). Probes 5 and 5b are about a
        # customer naming that house, so the house has to be one this store carries.
        emporio = Brand.objects.create(store=self.store, name="Emporio Armani")
        Product.objects.create(store=self.store, brand=emporio, name="Stronger With You")

    @contextmanager
    def resolver(self, picks=(), unplaced=()):
        """Stub the perfume resolver's model call.

        `picks` models what the live resolver answered: a catalogue name it chose
        for the customer's wording. `unplaced` models it admitting it found none.
        One payload serves both call sites — `resolve_products` reads
        "perfumes"/"unplaced", `confirm_placement` reads "name".
        """
        payload = json.dumps({
            "perfumes": list(picks),
            "unplaced": list(unplaced),
            "name": picks[0] if picks else "",
        }, ensure_ascii=False)
        with patch("products.services.product_resolver.chat", return_value=payload):
            yield

    def offer(self, *products):
        names = "، ".join(p.name for p in products)
        save_message(self.conv, "assistant", names, internal_context=names)

    def cart(self):
        return Cart.objects.get(conversation=self.conv)

    def carted(self):
        return [(i.variant.product.name, i.variant.volume, i.variant.bottle_type, i.quantity)
                for i in self.cart().items.select_related("variant__product")]

    # ── 1. The P2 bug: a name we cannot sell becomes a different perfume ──────
    def test_probe_1_unavailable_name_is_not_substituted(self):
        """HARD. Models artifacts/agent_evaluation/live/P2.json: the customer asks for an
        inactive perfume and the resolver answers with a stocked one. Failure = a perfume
        the customer never asked for reaches the order summary.

        The message says "Ambero" in Latin on purpose. "عايز اطلب امبيرو 90 ملي" is the live
        wording, but "امبيرو" is now read as the house Emporio Armani (probe 5's guard), which is
        the correct answer for that spelling and would mask what this probe is pinning: a name
        that matches a DEACTIVATED row must never be swapped for a stocked perfume.
        """
        Product.objects.create(store=self.store, brand=self.a.brand, name="Ambero", is_active=False)
        with self.resolver(picks=[self.a.name]):
            reply = self.turn([{"name": "Ambero", "volume": 90, "quantity": 1, "bottle_type": "normal"}],
                              message="عايز اطلب Ambero 90 ملي")
        print(f"\n[probe 1 reply] {reply}\n")
        self.assertEqual(self.carted(), [], f"substituted {self.carted()}; reply={reply!r}")
        self.assertEqual([p["name"] for p in self.cart().pending_items], ["Ambero"],
                         f"requested name lost: {self.cart().pending_items}")
        self.assertIn("مش متاح", reply)

    # ── 2. The P3 bug: a quantity change gets the same sentence back ──────────
    def test_probe_2_quantity_change_on_a_pending_line_is_acknowledged(self):
        """HARD. Models artifacts/agent_evaluation/live/P3.json. Failure = the customer's
        new quantity is dropped and they receive a byte-identical reply."""
        with self.resolver(unplaced=["اودورا"]):
            first = self.turn([{"name": "Oudora", "quantity": 1}], message="هاخد اودورا")
            second = self.turn([{"name": "Oudora", "quantity": 2}], message="خليها 2 بدل واحده")
        pending = self.cart().pending_items
        self.assertEqual(pending[0].get("quantity"), 2, f"quantity lost; pending={pending}")
        self.assertNotEqual(first, second, f"identical reply to a quantity change: {first!r}")

    # ── 3-5. Positional and demonstrative references ─────────────────────────
    def test_probe_3_ordinal_selects_the_second_offer(self):
        """SOFT. Feeds a WRONG extractor name to see whether code overrules it; the
        restored flow resolves ordinals through the extractor prompt instead."""
        self.offer(self.a, self.b)
        with self.resolver(picks=[self.a.name]):
            self.turn([self.line(self.a)], message="هاخد التاني")
        self.assertEqual([row[0] for row in self.carted()], [self.b.name],
                         f"ordinal resolved to {self.carted()}")

    def test_probe_4_demonstrative_uses_the_current_offer(self):
        """SOFT. 'هاخد ده' plus an added gift bottle must cart the offered perfume twice."""
        self.offer(self.a, self.b)
        with self.resolver(picks=[self.a.name]):
            self.turn([self.line(self.a, quantity=2)], message="تمام هاخد ده، وضيف كمان واحد للهدية")
        self.assertEqual(self.carted(), [(self.a.name, 50, "normal", 2)], f"carted {self.carted()}")

    def test_probe_5_demonstrative_cannot_hide_an_unresolved_name(self):
        """KNOWN GAP — expected to fail. 'هاخد ده امبيرو' names a perfume in Arabic script that is
        not in the catalogue at all. Catching this needs the resolver's `unplaced` judgement, i.e. a
        model call on the customer's message; the same span check measures 'للهديه' (probe 4) and
        'اسمي احمد والعنوان القاهره' as name-like, so no deterministic rule separates them. Left
        failing deliberately rather than paying a model call on every checkout turn."""
        self.offer(self.a)
        with self.resolver(unplaced=["امبيرو"]):
            self.turn([self.line(self.a)], message="هاخد ده امبيرو")
        self.assertEqual(self.carted(), [], f"unresolved name silently became {self.carted()}")

    def test_probe_5b_demonstrative_cannot_hide_an_inactive_latin_name(self):
        """HARD. The placeable half of probe 5: the customer types a deactivated perfume's catalogue
        name next to a demonstrative. Failure = they are sold the perfume we had just offered."""
        Product.objects.create(store=self.store, brand=self.a.brand, name="Ambero", is_active=False)
        self.offer(self.a)
        with self.resolver(unplaced=["Ambero"]):
            reply = self.turn([self.line(self.a)], message="هاخد ده Ambero")
        self.assertEqual(self.carted(), [], f"carted {self.carted()}; reply={reply!r}")
        self.assertIn("مش متاح", reply)

    # ── 6. Checkout state survives an off-topic turn ─────────────────────────
    def test_probe_6_a_question_does_not_drop_the_pending_line(self):
        """HARD. A price question mid-checkout, with the extractor returning no products.
        Failure = the saved selection is lost and the customer must start over."""
        with self.resolver(unplaced=["Mystery Azure"]):
            self.turn([{"name": "Mystery Azure", "quantity": 1}], message="هاخد Mystery Azure")
            before = json.dumps(self.cart().pending_items, ensure_ascii=False, sort_keys=True)
            self.turn([], message="بكام ده؟")
        after = json.dumps(self.cart().pending_items, ensure_ascii=False, sort_keys=True)
        self.assertEqual(before, after, f"pending changed: {before} -> {after}")

    # ── 7. Packaging is not silently downgraded ──────────────────────────────
    def test_probe_7_out_of_stock_original_is_not_swapped_for_a_brand_bottle(self):
        """HARD. The 100 ml original is out of stock. Failure = the customer who asked for
        an original bottle is quietly carted a brand bottle instead."""
        self.original.stock = 0
        self.original.save()
        with self.resolver(picks=[self.a.name]):
            self.turn([self.line(self.a, volume=100, bottle_type="original")],
                      message="هات واحده اوريجينال 100 ملي")
        self.assertFalse([row for row in self.carted() if row[2] == "normal"],
                         f"packaging downgraded: {self.carted()}")

    # ── 8. Replacing one pending line ────────────────────────────────────────
    def test_probe_8_explicit_replacement_of_a_pending_line(self):
        """SOFT. 'بدل X خليها Y' should replace the unresolved line, not stack a second one."""
        with self.resolver(unplaced=["Mystery Azure"]):
            self.turn([{"name": "Mystery Azure", "quantity": 2}], message="هاخد Mystery Azure")
            pending = self.cart().pending_items[0]
        with self.resolver(picks=[self.a.name]):
            self.turn([{**self.line(self.a), "line_id": pending["line_id"]}],
                      message="بدل Mystery Azure خليها Vanilo")
        self.assertEqual([row[0] for row in self.carted()], [self.a.name], f"carted {self.carted()}")
        self.assertEqual(self.cart().pending_items, [], f"pending left over: {self.cart().pending_items}")

    # ── 9. Nothing above may confirm an order ────────────────────────────────
    def test_probe_9_no_probe_created_an_order(self):
        """Sanity guard: none of these flows should be able to place an order."""
        self.assertFalse(Order.objects.exists())
