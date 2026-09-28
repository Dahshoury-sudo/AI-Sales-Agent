r"""Re-measure the placement guard's thresholds against every conversation in production.

`product_resolver._verify_placement` decides whether to withhold a perfume we already placed, on four
numbers — `_MIN_COVERAGE`, `_GAP`, `_PICK_RATIO`, `_MIN_CHARS` — that are not derivable from anything.
They were fitted to real customer spans, and the module comment above them says to re-measure here
before trusting them against a different catalogue. This is that measurement, committed rather than
thrown away, because the caveats guarantee it will be needed again: the thresholds move with catalogue
size, with the resolver model, and with however Egyptians are spelling French names that season.

What it does: walk every stored turn where the pipeline placed **exactly one** perfume as the answer,
re-derive the span from the customer's message the way the guard does, score the catalogue, and
report what the rule would have done. Nothing is written and no model is called — the placements
already happened and their outcome is in `Message.internal_context`.

    $env:PYTHONIOENCODING = "utf-8"
    & .\.venv\Scripts\python.exe -m eval_harness.backtest_placement

🔴 **Read-only against production.** `DJANGO_SETTINGS_MODULE` is the real settings module, so this
reads the live Railway Postgres exactly as `runner.py` does — but unlike `runner.py` it opens no
transaction, because it never writes. Keep it that way.

**The eight known true positives** are listed in `KNOWN_BAD` and asserted by name. They are the turns
that started this: one pair of perfumes, `الترامل`/`التراميل`/`الترا ميل` answered "Terre d'Hermes"
across conversations 991, 1021, 1035, 1040 and 1041. Seven of the eight were found *by* an earlier
version of this script and never reported by anyone — which is the argument for keeping it.

**The false-positive side is the one that matters more**, and it is everything else this prints. A
guard that withholds a correct placement costs every customer who spells a name unusually an extra
round-trip, on a store we cannot inspect. Two rows are checked by name for that reason:
`ڤيرزاتشي ايروس` (conversation 772's regression floor) and `سترينجر وذ يو انتنسلي` (conversation 726 —
a correct placement, and the tightest *separation* in the corpus).

The last section is the one to read when changing a threshold: **the frontier**, the highest-coverage
correct placements in the corpus. `_disagreement`'s docstring quotes its top figure (0.588) against the
tightest true positive (0.833) as the whole justification for `_MIN_COVERAGE`. If that margin has
closed, the threshold is no longer supported by the data and neither is the docstring.
"""

import os
import re
import sys
from collections import Counter, defaultdict

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from products.models import Message, Product  # noqa: E402
from products.services import product_resolver as resolver  # noqa: E402
from products.services.sales import naming  # noqa: E402

# The eight turns this guard was built for — seven distinct texts, because `موجود الترامل` was asked
# in two different conversations. Conversation ids are recorded for the reader; matching is on the
# customer's text, because a conversation can be pruned and the defect is a property of the span, not
# of the row it happens to live in.
KNOWN_BAD = {
    "طب الترامل",          # 991
    "موجود الترامل",       # 1021, 1035
    "التر امل",            # 1021
    "بقول الترا ميل",      # 1035
    "عندكو الترا ميل ؟",   # 1035
    "عندكو الترامل",       # 1040
    "في التراميل ؟",       # 1041
}

# Correct placements that must survive the guard, asserted by name. `متاح`/`عندكو` + the full
# spelling (conversations 723 and 727) is the clean form of the Stronger With You line — the top row
# IS the placed row at coverage 0.778, so what protects it is the `best.pk == pick.pk` short-circuit
# rather than any threshold. That distinction matters: a future change that keeps the thresholds but
# reorders those two tests would break this without moving a single number.
MUST_NOT_FLAG = {
    "ڤيرزاتشي ايروس": "conv772 — Eros is stocked; the phonetic winner is a different row",
    "متاح سترينجر وذ يو انتنسلي": "conv723 — correctly placed; best IS the pick at cov 0.778",
    "عندكو سترينجر وذ يو انتنسلي": "conv727 — same, the other spelling in the suite",
}

# 🔴 Wrong placements this guard does NOT catch, kept so the recall gap stays visible and honest.
# Both are conversation 726, both answered *Afnan 9PM* when the customer named Stronger With You
# Intensely — the first asking its price outright. They fall below `_MIN_COVERAGE` only because
# `انااا`, `بتكلم`, `دلوقتي` and `عله` are missing from `naming._REFERENTIAL` and stay in the span.
# That is a vocabulary fix, not a threshold fix; see `_disagreement`'s recall note. If one of these
# ever starts flagging, that is an improvement — update this list, do not silence it.
KNOWN_MISSES = {
    "انااا بتكلم دلوقتي سعر سترينجر وذ يو انتنسلي عامل كام",
    "انااا عله سترينجر وذ يو انتنسلي",
}

_NAME_LINE = re.compile(r"^Name \(الاسم الصحيح\): (.+)$", re.MULTILINE)

# Context blocks that mean the rows below are NOT the perfume the customer asked about. A turn
# carrying any of them is not a placement and must not be scored as one: the whole corpus would
# otherwise fill up with alternatives offered beside a denial.
_NOT_A_PLACEMENT = (
    "بدائل مقترحة",                                    # alternatives beside a denial
    "العطور اللي تحت دي اللي كنا بنتكلم عنها",          # the referent label
    "PENDING_LOOKUP:",
    "ABSENCE_DENIED",
    "NAME_UNREADABLE",
)


def placements():
    """Every stored turn that priced exactly one perfume as the answer to an Arabic message.

    Yields `(conversation_id, customer_text, product_name)`. The pairing is reply-to-previous-user-
    message, which is how the transcript reads: the assistant row carries the context that names what
    was placed, and the row before it carries what the customer actually typed.
    """
    rows = (
        Message.objects
        .filter(internal_context__contains="Name (الاسم الصحيح)")
        .order_by("conversation_id", "id")
        .values_list("conversation_id", "id", "internal_context")
    )
    asked = defaultdict(dict)
    for conversation_id, message_id, content in (
        Message.objects.filter(role="user").values_list("conversation_id", "id", "content")
    ):
        asked[conversation_id][message_id] = content

    for conversation_id, message_id, context in rows:
        if any(marker in context for marker in _NOT_A_PLACEMENT):
            continue
        names = _NAME_LINE.findall(context)
        if len(names) != 1:
            continue
        earlier = [mid for mid in asked[conversation_id] if mid < message_id]
        if not earlier:
            continue
        yield conversation_id, asked[conversation_id][max(earlier)], names[0].strip()


def main():
    catalogues = {}
    for product in Product.objects.filter(is_active=True).select_related("brand"):
        catalogues.setdefault(product.store_id, []).append(product)

    # name -> the active rows carrying it, so the store can be recovered without a query per turn.
    by_name = defaultdict(list)
    for store_id, rows in catalogues.items():
        for product in rows:
            by_name[product.name].append(product)

    corpus = list(placements())
    flagged, frontier, examined, skipped = [], [], 0, Counter()
    per_store, flags_per_store = Counter(), Counter()

    for conversation_id, text, placed_name in corpus:
        candidates = by_name.get(placed_name)
        if not candidates:
            skipped["placed row no longer active"] += 1
            continue
        # A name can exist in more than one store. Score against each store that has it and take the
        # first — the turn belongs to exactly one of them and the conversation does not say which,
        # but a name shared across stores is shared with the same spelling, so the span scores the
        # same way against either catalogue.
        pick = candidates[0]
        products = catalogues[pick.store_id]

        span = naming.arabic_span(text)
        if not span or resolver._HAS_LATIN.search(span):
            skipped["no Arabic-only span"] += 1
            continue
        latin = naming.transliterate(span)
        if not latin:
            skipped["span transliterates to nothing"] += 1
            continue

        examined += 1
        per_store[pick.store_id] += 1
        ranking = naming.phonetic_ranking(text, pick.store, products=products)
        if len(ranking) < 2:
            continue

        best_score, matched, best = ranking[0]
        second = ranking[1][0]
        pick_score = next((s for s, _, r in ranking if r.pk == pick.pk), 0.0)
        coverage = matched / len(latin)
        row = (conversation_id, text, span, pick.name, pick_score,
               best.name, best_score, second, matched, coverage)

        if resolver._disagreement(text, pick, pick.store, products):
            flags_per_store[pick.store_id] += 1
            flagged.append(row)
        elif best.pk != pick.pk and matched >= resolver._MIN_CHARS:
            # A correct placement that got as far as the coverage clause. Turns where the top row IS
            # the pick are excluded: they short-circuit before any threshold is consulted and so say
            # nothing about where one can sit. Same for M below `_MIN_CHARS`.
            frontier.append(row)

    print(f"examined {examined} single-product Arabic placements "
          f"across {len(per_store)} stores")
    for reason, count in skipped.most_common():
        print(f"  skipped {count}: {reason}")
    print()

    print(f"FLAGGED {len(flagged)} ({len(flagged) / max(examined, 1):.1%} of turns)")
    for row in sorted(flagged, key=lambda r: -r[9]):
        (conversation_id, text, span, pick_name, pick_score,
         best_name, best_score, second, matched, coverage) = row
        verdict = "KNOWN BAD ✅" if text.strip() in KNOWN_BAD else "review ⚠️"
        print(f"  [{conversation_id}] {verdict} {text!r}")
        print(f"      span={span!r} -> {naming.transliterate(span)!r}  "
              f"placed={pick_name!r} {pick_score:.3f}  "
              f"best={best_name!r} {best_score:.3f}  2nd={second:.3f}  "
              f"gap={best_score - second:.3f}  M={matched}  cov={coverage:.3f}")
    print()

    # Catalogue size is the variable the thresholds are most sensitive to (`_disagreement` records
    # the bootstrap), so the rate is reported per store rather than pooled.
    print("flag rate per store — catalogue size is the variable that moves it:")
    for store_id, count in per_store.most_common():
        rate = flags_per_store[store_id] / count
        print(f"  store {store_id}: {len(catalogues.get(store_id, []))} rows, "
              f"{count} placements, {flags_per_store[store_id]} flagged ({rate:.1%})")
    print()

    # ── the frontier: how much room the coverage threshold actually has ─────
    #
    # These are placements that reached the coverage clause and were rejected by it. The highest of
    # them is the closest a turn we leave alone comes to being withheld, and the margin between it
    # and the lowest true positive is the entire empirical case for `_MIN_COVERAGE`. Rows in
    # `KNOWN_MISSES` are marked: they are in this list because the guard misses them, not because
    # they are correct, and reading them as headroom would be exactly backwards.
    top = sorted(frontier, key=lambda r: -r[9])[:8]
    true_positive_cov = [row[9] for row in flagged if row[1].strip() in KNOWN_BAD]
    clean = [row for row in top if row[1].strip() not in KNOWN_MISSES]
    print(f"frontier — the {len(top)} placements closest to the coverage line "
          f"(of {len(frontier)} that reach it):")
    for row in top:
        conversation_id, text, _, pick_name, _, best_name, _, second, matched, coverage = row
        tag = " 🔴 KNOWN MISS" if text.strip() in KNOWN_MISSES else ""
        print(f"  [{conversation_id}] cov={coverage:.3f} M={matched:<3} {text!r}{tag}")
        print(f"      placed={pick_name!r}  best={best_name!r}")
    if clean and true_positive_cov:
        worst = clean[0][9]
        print(f"\n  worst correct placement {worst:.3f}  |  "
              f"_MIN_COVERAGE {resolver._MIN_COVERAGE}  |  "
              f"tightest true positive {min(true_positive_cov):.3f}")
        if not (worst < resolver._MIN_COVERAGE <= min(true_positive_cov)):
            print("  🔴 _MIN_COVERAGE is no longer inside that margin — re-fit it before shipping.")
    print()

    # ── the named assertions ────────────────────────────────────────────────
    ok = True
    flagged_text = {row[1].strip() for row in flagged}
    in_corpus = {text.strip() for _, text, _ in corpus}
    missed = sorted(name for name in KNOWN_BAD
                    if name in in_corpus and name not in flagged_text)
    absent = sorted(name for name in KNOWN_BAD if name not in in_corpus)
    if missed:
        ok = False
        print("❌ known-bad turns the rule did NOT flag:")
        for name in missed:
            print(f"     {name!r}")
    else:
        print("✅ every known-bad turn present in the corpus was flagged")
    if absent:
        # Not a failure: `placements()` only sees turns still in the database, and the pipeline has
        # changed since some of these were recorded. Printed so a shrinking corpus is visible rather
        # than quietly turning this check into a no-op.
        print(f"   ({len(absent)} known-bad turns are no longer in the corpus: "
              f"{', '.join(repr(name) for name in absent)})")

    for span_text, why in MUST_NOT_FLAG.items():
        if any(span_text in row[1] for row in flagged):
            ok = False
            print(f"❌ FALSE POSITIVE on {span_text!r} — {why}")
        elif any(span_text in text for text in in_corpus):
            print(f"✅ {span_text!r} not flagged ({why})")
        else:
            # Distinguished deliberately: a check that passes because the turn is gone is not
            # evidence about the rule, and silently reading as a pass is how a floor rots.
            print(f"⚪ {span_text!r} is NOT in the corpus — this check proved nothing ({why})")

    review = [row for row in flagged if row[1].strip() not in KNOWN_BAD]
    if review:
        print(f"\n⚠️  {len(review)} flag(s) above are NOT known-bad. Each one is either a defect "
              f"nobody reported or a false positive; read the reply before shipping a threshold.")

    # Recall, stated rather than left to be inferred from a clean-looking report.
    caught = sorted(name for name in KNOWN_MISSES if name in flagged_text)
    still_missed = sorted(name for name in KNOWN_MISSES
                          if name in in_corpus and name not in flagged_text)
    if caught:
        print(f"\n🎉 {len(caught)} turn(s) from KNOWN_MISSES now flag — recall improved. "
              f"Move them out of that list:")
        for name in caught:
            print(f"     {name!r}")
    if still_missed:
        print(f"\nℹ️  {len(still_missed)} known wrong placement(s) still slip through, as documented "
              f"in `_disagreement`. Not a failure — recall is not what this rule claims:")
        for name in still_missed:
            print(f"     {name!r}")

    print(f"\nthresholds in force: cov>={resolver._MIN_COVERAGE} gap>={resolver._GAP} "
          f"pick<={resolver._PICK_RATIO}*best chars>={resolver._MIN_CHARS}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
