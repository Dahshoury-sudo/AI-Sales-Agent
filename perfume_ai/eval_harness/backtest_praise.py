# -*- coding: utf-8 -*-
"""Measure what the praise vocabulary and the prefix stripping do to every stored message.

Committed rather than thrown away, for the reason `backtest_placement.py` is: the wordlists in
`naming` will be extended again, and the only honest way to extend them is to re-run this and read
the messages that changed. A word added here is not a tuning knob — `may_name_a_perfume` going
blind to a real perfume name is the conversation-738 bug, and the catalogue it has to stay clear of
grows with every tenant.

Read-only against production, and it opens no transaction because it never writes. Keep it that
way.

    python -m eval_harness.backtest_praise

Three checks, in the order they matter:

  1. COLLISIONS — does any praise or past-purchase word equal a token of a real product name, in
     any store? A collision means the gate is blind to that perfume for good.
  2. NAMES — a fixed list of spans that must keep naming something, including the ones the
     measurement caught. "عندك برفان واي" is the important one.
  3. FLIPS — every stored customer message that stops naming anything. These have to be read, not
     counted: the count passing tells you nothing about whether the right messages flipped.
"""
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from products.models import Message, Product  # noqa: E402
from products.services.sales import naming  # noqa: E402

# Spans that must keep naming something. Each is either a real stored message or a real transcript
# turn, and each one is here because it would be a silent regression.
MUST_SURVIVE = {
    # 🔴 The regression the corpus caught. "واي" is how this dialect spells the letter Y, and this
    # catalogue holds a perfume called Y. Stripping the "و" leaves "اي", which IS referential, so a
    # stem floor of 2 would have deleted a real name. This is why `_MIN_STEM` is 3.
    "عندك برفان واي": "واي",
    # Ultra Male, in the four spellings conversations 991/1021/1035/1040/1041 used. The praise
    # filtering must not touch them, and the article must survive on the token itself because
    # `phonetic_ranking` scores the customer's own letters.
    "موجود الترامل": "الترامل",
    "في التراميل ؟": "التراميل",
    "الترامل تحفه": "الترامل",
    # A name beside praise: the praise goes, the name stays.
    "اشتريت منكم امبيرو وعجبني": "امبيرو",
    # Ordinary names, Arabic and Latin, including the letter-spaced one `_fuse_spelled_out` rescues.
    "طب لامال لكريز": "لامال",
    "عايز سوفاج": "سوفاج",
    "عندكم عطر اسمه بلاك اوركيد؟": "اوركيد",
    "طب في زار كولد": "كولد",
    "سترينجر وذ يو انتنسلي": "انتنسلي",
    "B m w": "bmw",
}

# The two turns this work exists for.
CONV_1105 = ("البرفان تحفه وثباته ممتاز", "البرفان الي جبته من عندكو واو بجد")


def _identifying_tokens_before(text):
    """What `identifying_tokens` returned before praise vocabulary and prefix stripping existed.

    Reproduced here rather than remembered, so the "what changed" list below stays computable for as
    long as this script exists. Exact membership against `_REFERENTIAL` only — no `_STOPWORDS` probe,
    no praise, no past purchase, no article or conjunction peeled off.
    """
    return {
        token
        for token in naming.tokens(text)
        if token not in naming._REFERENTIAL
        and not naming._is_chase_token(token)
        and not token.isdigit()
    }


def main():
    failures = []

    print("=" * 78)
    print("1. COLLISIONS — praise vocabulary against every product name in every store")
    print("=" * 78)
    names = [n for n in Product.objects.values_list("name", flat=True).distinct() if n]
    catalogue = set()
    for name in names:
        catalogue |= naming.tokens(name)
    vocabulary = naming._PRAISE | naming._PAST_PURCHASE
    collisions = sorted(catalogue & vocabulary)
    print(f"   {len(names)} distinct product names, {len(catalogue)} name tokens, "
          f"{len(vocabulary)} vocabulary words")
    arabic_names = [n for n in names if naming._ARABIC_LETTER.search(n)]
    print(f"   product names written in Arabic script: {len(arabic_names)}")
    if collisions:
        failures.append(f"vocabulary collides with catalogue names: {collisions}")
        print(f"   ❌ COLLISION: {collisions}")
    else:
        print("   ✅ none — no praise or past-purchase word is a product-name token")

    print()
    print("=" * 78)
    print("2. CONVERSATION 1105 — both turns must name nothing")
    print("=" * 78)
    for message in CONV_1105:
        found = naming.identifying_tokens(message)
        praise = sorted(naming.praise_tokens(message))
        if found:
            failures.append(f"1105 turn still names something: {message!r} -> {found}")
            print(f"   ❌ {message!r} -> {sorted(found)}")
        else:
            print(f"   ✅ names nothing, praise={praise}  {message!r}")

    print()
    print("=" * 78)
    print("3. NAMES — spans that must keep naming something")
    print("=" * 78)
    for message, required in MUST_SURVIVE.items():
        found = naming.identifying_tokens(message)
        if required in found:
            print(f"   ✅ {required:10} in {sorted(found)}")
        else:
            failures.append(f"lost a real name: {message!r} -> {sorted(found)}")
            print(f"   ❌ LOST {required!r} from {message!r} -> {sorted(found)}")

    print()
    print("=" * 78)
    print("4. FLIPS — messages this change stopped reading as a name, to be READ not counted")
    print("=" * 78)
    rows = [t for t in Message.objects.filter(role="user").values_list("content", flat=True) if t]
    flipped, narrowed = {}, 0
    for text in rows:
        before, after = _identifying_tokens_before(text), naming.identifying_tokens(text)
        if before == after:
            continue
        if after:
            narrowed += 1
            continue
        flipped[" ".join(text.split())] = sorted(before)

    print(f"   {len(rows)} stored customer messages")
    print(f"   {narrowed} lost filler but still name something (the span just got cleaner)")
    print(f"   {len(flipped)} distinct now name NOTHING — these are the behaviour change:")
    print()
    print("   🔴 Read every one. A count passing tells you nothing; if any of these names a")
    print("      perfume, a wordlist swallowed it and the gate is blind to it for good.")
    print()
    for text, before in sorted(flipped.items()):
        print(f"     {text[:64]!r}")
        print(f"         was: {before}")

    print()
    print("=" * 78)
    if failures:
        print(f"❌ {len(failures)} FAILURE(S)")
        for failure in failures:
            print(f"   - {failure}")
        return 1
    print("✅ collisions clean, both 1105 turns name nothing, every required name survived")
    return 0


if __name__ == "__main__":
    sys.exit(main())
