# -*- coding: utf-8 -*-
"""Measure `confident_catalogue_match` against every stored customer message.

The gating check for the conversation-1106 routing correction. `product_resolver` lets the catalogue
overrule the classifier when a message's own letters unmistakably spell a perfume we stock, and the
only honest way to hold that rule is to re-run this and read what fires.

🔴 **This is not `backtest_placement` with a different name, and the difference is the reason both
exist.** That one measures the scorer as a *veto*: it runs on the rare turn that placed exactly one
row, and a fire withholds something. This one measures it as a *proposal*: it runs on every turn of
three classifications, and a fire sends a customer down a different branch. Same thresholds, same
scorer, different population — so the false-positive rate had to be measured from scratch rather
than inherited.

Read-only against production, and it opens no transaction because it never writes. Keep it that way.

    python -m eval_harness.backtest_name_routing

Four checks:

  1. CONV 1106 — all four Khamrah spellings must fire on a Khamrah row.
  2. ABSTAIN — browse requests, scent requests, a not-stocked name, and a Latin-mixed span must not.
  3. SWEEP — every stored customer message, scored against its own conversation's store. Every fire
     is printed, because the count passing tells you nothing about whether the right ones fired.
  4. VOCABULARY — the `naming._REFERENTIAL` additions that removed the sweep's one false positive.
"""
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from products.models import Conversation, Message, Store  # noqa: E402
from products.services.product_resolver import confident_catalogue_match  # noqa: E402
from products.services.sales import naming  # noqa: E402

# Conversation 1106, verbatim plus the two spellings it did not use. All four must fire: the customer
# typed the base name of a four-variant Lattafa line, and `Khamrah` beats `Khamrah Waha` by 0.208.
CONV_1106 = ("عندكو خمره", "برفان خمره", "عندكو خمرة", "خمره")

# 🔴 Must abstain, each for its own reason. A fire here is a browse request answered with one
# bottle's price, or — for the Lacoste line — a confident match on a perfume the store does not sell.
MUST_ABSTAIN = {
    "عايز عطر حلو للشتا": "browse: a season, no name",
    "عندكو حاجه من ديور": "browse: a house, no perfume",
    "عايز عطر شرقي ثابت": "browse: a family and an attribute",
    "عايز حاجه رجالي فواحه": "browse: gender and projection",
    "عندكو عطور نيش": "browse: a category",
    "عايز هديه لمراتي": "browse: a gift, no name",
    "عايز حاجه شبه Baccarat Rouge بس مش هي": "names a perfume in Latin and asks for NOT it",
    "في لاكوست اسنشال؟": "a real perfume name this store does not stock",
}

# Wants something *like* a perfume. `router` consults `describes_rather_than_names` before scoring,
# so these never reach the ranking — asserted through that predicate rather than through the match.
MUST_READ_AS_DESCRIBING = (
    "عايز عطر ريحته خمره",
    "عايز حاجه زي خمره",
    "في حاجه قريبه من خمره",
    "عايز عطر نوتته خمره",
)

# Added to `_REFERENTIAL` for this change. Each removed or recovered a fire in the sweep below.
VOCABULARY = ("كنت", "كنا", "اكتر", "عنكو", "عنكم", "اكبر", "اصغر")


def _store_for(conversation_id):
    conversation = Conversation.objects.filter(id=conversation_id).first()
    return conversation.store if conversation else None


def main():
    failures = []

    print("=" * 78)
    print("1. CONVERSATION 1106 — every Khamrah spelling must fire")
    print("=" * 78)
    store = _store_for(1106)
    if store is None:
        failures.append("conversation 1106 is not in this database")
        print("   ❌ conversation 1106 not found")
    else:
        print(f"   store = {store}")
        for message in CONV_1106:
            if naming.describes_rather_than_names(message):
                failures.append(f"1106 turn read as describing: {message!r}")
                print(f"   ❌ {message!r} reads as a scent request")
                continue
            match = confident_catalogue_match(message, store)
            if match is None or "khamrah" not in match.name.lower():
                failures.append(f"1106 turn did not fire on a Khamrah: {message!r} -> {match}")
                print(f"   ❌ {message!r} -> {match.name if match else None}")
            else:
                print(f"   ✅ {message!r:18} -> {match.name}")

    print()
    print("=" * 78)
    print("2. ABSTAIN — a fire here is a browse request answered with one perfume")
    print("=" * 78)
    for message, why in MUST_ABSTAIN.items():
        scoped = _store_for(1012) if "لاكوست" in message else store
        if scoped is None:
            continue
        match = None
        if not naming.describes_rather_than_names(message):
            match = confident_catalogue_match(message, scoped)
        if match is not None:
            failures.append(f"fired on {message!r} -> {match.name} ({why})")
            print(f"   ❌ {message!r} -> {match.name}   [{why}]")
        else:
            print(f"   ✅ abstained   [{why}]  {message!r}")

    print()
    for message in MUST_READ_AS_DESCRIBING:
        if naming.describes_rather_than_names(message):
            print(f"   ✅ reads as describing   {message!r}")
        else:
            failures.append(f"not read as describing: {message!r}")
            print(f"   ❌ NOT read as describing   {message!r}")

    print()
    print("=" * 78)
    print("3. VOCABULARY — the `_REFERENTIAL` additions this change made")
    print("=" * 78)
    missing = [word for word in VOCABULARY if word not in naming._REFERENTIAL]
    if missing:
        failures.append(f"vocabulary additions reverted: {missing}")
        print(f"   ❌ no longer in _REFERENTIAL: {missing}")
    else:
        print(f"   ✅ all {len(VOCABULARY)} present")
    polluted = "كنت محتاج اعرف عنكو اكتر"
    if naming.identifying_tokens(polluted):
        failures.append(f"{polluted!r} still names something: {naming.identifying_tokens(polluted)}")
        print(f"   ❌ {polluted!r} -> {sorted(naming.identifying_tokens(polluted))}")
    else:
        print(f"   ✅ {polluted!r} names nothing (was the sweep's only false positive)")

    print()
    print("=" * 78)
    print("4. SWEEP — every stored customer message, against its own store")
    print("=" * 78)
    rows = (Message.objects.filter(role="user")
            .values_list("content", "conversation__store_id", "conversation_id"))
    by_store = {}
    for content, store_id, conversation_id in rows:
        if content and store_id:
            by_store.setdefault(store_id, []).append((content, conversation_id))
    stores = {store_id: Store.objects.get(id=store_id) for store_id in by_store}

    scanned, fires, per_store = 0, {}, {}
    for store_id, items in by_store.items():
        hits = 0
        for content, conversation_id in items:
            scanned += 1
            if naming.describes_rather_than_names(content):
                continue
            match = confident_catalogue_match(content, stores[store_id])
            if match is not None:
                hits += 1
                key = (conversation_id, " ".join(content.split())[:58])
                fires[key] = match.name
        per_store[store_id] = (len(items), hits)

    print(f"   scanned {scanned} messages across {len(by_store)} stores; "
          f"{len(fires)} distinct fires ({100 * len(fires) / max(scanned, 1):.1f}%)")
    print()
    print("   🔴 Read every line. A count passing proves nothing — the question is whether each")
    print("      fire is a perfume name. One that is not is a browse request about to be answered")
    print("      with a single bottle's price list.")
    print()
    for (conversation_id, text), name in sorted(fires.items(), key=lambda row: row[1]):
        print(f"     {name:30} [{conversation_id}] {text!r}")

    print()
    print("   fires per store — the crossing rate moves with catalogue size:")
    for store_id, (total, hits) in sorted(per_store.items()):
        rows_in_store = stores[store_id].products.filter(is_active=True).count()
        print(f"     store {store_id}: {rows_in_store} rows, {total} messages, "
              f"{hits} fired ({100 * hits / max(total, 1):.1f}%)")

    print()
    print("=" * 78)
    if failures:
        print(f"❌ {len(failures)} FAILURE(S)")
        for failure in failures:
            print(f"   - {failure}")
        return 1
    print("✅ 1106 fires on every spelling, every abstention held, vocabulary intact")
    print("   The sweep's fires are listed above and are not self-validating — read them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
