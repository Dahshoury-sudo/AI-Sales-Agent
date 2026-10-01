"""Match a perfume name the model produced against a name the catalogue actually holds.

Three separate defects traced to the same missing primitive:

  * `similar_to="9pm by Afnan"` found nothing, because `_resolve_reference` matched with
    `name__icontains=<whole string>` and the row is called "Afnan 9PM". So the similarity
    engine fell back to notes the model *guessed* while the real notes sat in the same
    database.
  * `exclude_names=["9pm by Afnan"]` excluded nothing for the same reason, leaving the
    perfume the customer asked for an alternative to sitting in the candidate pool.
  * `similar_to="Ambiro"` (one letter wrong, for "Ambero") failed both of the above and
    was then persisted to conversation.preferences, poisoning every later turn.

`product_resolver.resolve_products` already resolves names tolerantly, but it costs an
LLM call and is prompt-driven. These call sites are on the hot path and need a cheap,
deterministic, testable match — so the rule is stated here once instead of being
re-approximated at each site.
"""

import re

from ..static_faq_service import normalize_arabic

# Words that carry no identifying information, so an overlap on them alone is not a
# match. Deliberately excludes "homme", "femme", "intense", "extrait" and the like: those
# ARE identifying (Dior Homme is not Dior Sauvage), and stripping them made
# "Dior Homme Intense" indistinguishable from the bare brand word "Dior".
_STOPWORDS = frozenset({
    "eau", "de", "la", "le", "du", "des", "parfum", "perfume", "edp", "edt",
    "cologne", "pour", "by", "the",
    "عطر", "برفان", "بارفان", "بتاع", "من",
})


def _fuse_spelled_out(raw):
    """Join runs of three or more single-character tokens into one word.

    "B m w" and "س و ف ا ج" are a customer spelling a name out letter by letter, and every letter
    is a token of length 1 — so the length filter in `tokens` below discarded all of them and the
    message tokenised to nothing at all. That is not a harmless miss. `may_name_a_perfume` then
    reads the turn as naming no perfume, `product_info` never calls the resolver, no pending record
    is written, and the previously offered perfume is handed to the model as the answer with its
    prices attached. Conversation 1021 turn 28 is "B m w", answered "عطر Le Male ... الـ 100 ملي
    سعره 600 جنيه" — a price for a perfume the customer had not asked about.

    Three is the threshold rather than two because one stray letter beside real words is ordinary
    and must keep tokenising to nothing: this catalogue holds a perfume named "Y", so
    "Y Eau de Parfum" has to stay empty. Three in a row is someone spelling. Measured against every
    message in conversation 1021 plus every Arabic string in `eval_harness/scenarios*.py`
    — 457 spans — the only one this fuses is the "B m w" that caused the bug.

    Note what this does NOT fix: a one-character name on its own. "في Y" still tokenises to
    nothing, because "في" is referential and "Y" is one letter with no run to join. That hole is
    closed by `carries_unreadable_content` below, not here.
    """
    fused, run = [], []
    for token in raw:
        if len(token) == 1:
            run.append(token)
            continue
        fused.extend(["".join(run)] if len(run) >= 3 else run)
        run = []
        fused.append(token)
    fused.extend(["".join(run)] if len(run) >= 3 else run)
    return fused


def tokens(text):
    """Identifying tokens of a name, normalised and stripped of filler.

    Punctuation is replaced with whitespace rather than left attached. Splitting on
    whitespace alone left "Sauvage؟" as a single token, so "بكام Dior Sauvage؟" — an entirely
    ordinary way to ask a price — matched no product at all, while the same message with a
    space before the "؟" matched fine. That silently defeated every deterministic call site:
    `mentioned_in` on the order-cancel branch, `match_product` as the resolver's post-filter,
    and the named-perfume guard in `product_info`.

    `\\W` covers Arabic punctuation (؟ ، ؛) as well as Latin, and Python's `\\w` includes
    Arabic letters and digits, so names carrying numbers ("Afnan 9PM", "XJ 1861 Naxos",
    "Baccarat Rouge 540") tokenise unchanged.

    A run of three or more single letters is fused first — see `_fuse_spelled_out` — so a name the
    customer spelled out survives the length filter below instead of vanishing.
    """
    cleaned = re.sub(r"\W+", " ", normalize_arabic(text or ""), flags=re.UNICODE)
    return {
        token
        for token in _fuse_spelled_out(cleaned.split())
        if len(token) > 1 and token not in _STOPWORDS
    }


# Asking for an answer we already promised, rather than about a perfume. Split out of
# `_REFERENTIAL` below because two callers need it for opposite reasons: the gate needs these
# words to count as naming nothing, and `product_info` needs to recognise them positively, to tell
# a customer collecting a promise ("طب اتأكدلي") from one asking the price of what was offered
# alongside it ("بكام؟"). Both messages are referential and both resolve to the same perfume, so
# nothing else in this module separates them — and answering the second with "مش موجود عندنا"
# would deny a perfume the customer never asked about while ignoring the question they did ask.
#
# Verb forms and question particles only; no name in this catalogue tokenises to any of them.
_CHASING = frozenset({
    "اتاكدلي", "اتاكد", "تاكدلي", "اتاكدت", "تاكدت", "لقيت", "لقيتلي", "عرفت",
})


# The same vocabulary as stems, because the frozenset above is a wordlist over a dialect that suffixes
# freely, and every form nobody happened to type is a silent *double* failure: `chasing_a_promise`
# misses it, and the unmatched token then survives `identifying_tokens`, so `may_name_a_perfume` fires
# and the resolver runs on a chase verb and returns whatever was last offered. 835 turn 2 is
# "ها لقيتو ؟" and 836 turn 3 is "ماشي اعرفلي"; the first was answered with Dior Homme Sport's price
# list, the second by repeating two price lists the customer already had. Only "لقيت" and "عرفت" were
# ever in the set, so one suffix each was enough to break both.
#
# Matched as a *prefix* with at most three trailing characters — never as a substring. "لقي" appears
# inside "القيمة" and "القيود", which are ordinary price words and must not read as chases; anchoring
# at the start excludes them because they begin with "ال". The bound keeps the family to real
# inflections ("لقيتو", "لقيتلي", "اعرفلي", "شوفتلي", "دورتلي").
#
# 🔴 The warning on `_REFERENTIAL` below applies here with more force, because a stem covers a whole
# family rather than one word: every entry has to be unambiguously a verb. Bare "دور" is excluded for
# exactly that reason — it is a plausible Arabic spelling of Dior, and swallowing it would blind the
# gate to Dior Sauvage, Dior Homme Sport and Dior Homme Intense for good. Only "دورت", carrying the
# past-tense ت, is listed. "لقي" and "عرف" are likewise reachable only through their affixed forms.
#
# "اعرف" and "تعرف" are excluded bare for a subtler version of the same problem: they are request
# verbs, not collection verbs. "عايز اعرف اسعار بلو دي شانيل" is an opening question, and reading it
# as a chase would answer it with a denial of whatever was still pending instead of the prices it asks
# for — exactly what this function's callers guard against. Only the benefactive "اعرفلي" / "تعرفلي"
# — *find out for me* — is a chase, so that is what is listed.
#
# Checked against all 46 catalogue names: no transliteration of any of them starts with any stem here.
_CHASE_STEMS = ("لقيت", "عرفت", "اعرفلي", "تعرفلي", "اتاكد", "تاكد", "شوفت", "شوف", "دورت")

_MAX_CHASE_SUFFIX = 3


def _is_chase_token(token):
    """True when one token is a chase verb, in any of its ordinary inflections.

    The single place `_CHASING` and `_CHASE_STEMS` are read together, so the two callers that need
    this judgement — `chasing_a_promise` and `identifying_tokens` — cannot drift apart. They failed
    835 turn 2 together and have to be fixed together: recognising the chase without also keeping the
    verb out of the identifying tokens would still leave "لقيتو" looking like an unplaceable name.
    """
    return token in _CHASING or any(
        token.startswith(stem) and len(token) - len(stem) <= _MAX_CHASE_SUFFIX
        for stem in _CHASE_STEMS
    )


def chasing_a_promise(text):
    """True when the message asks for an answer that was promised rather than about a perfume.

    Conversation 799 turn 2 is "اتأكدلي منه" and 798 turn 2 is "ها لقيت اي ؟" — go on, check;
    so, what did you find. Both were answered with the price list of the perfume that had been
    offered alongside the promise, because a referential message resolves to whatever was last
    on the table and nothing distinguished "give me the answer you owe me" from "tell me about
    this one". `product_info` requires this before it will treat an open lookup as chased.

    Token-wise rather than substring-wise, via `_is_chase_token`: a chase is a whole word, and
    "القيمة كام" is a price question that happens to contain the letters of one.
    """
    return any(_is_chase_token(token) for token in tokens(text))


# The other shape of "you owe me an answer": insisting, without a collection verb and without
# naming anything. Kept out of `_CHASING` on purpose — the comment above refuses these bare forms,
# and that refusal is right for a set `identifying_tokens` also reads, because
# "عايز اعرف اسعار بلو دي شانيل" has to stay an opening question. What makes them safe *here* is
# the caller and not the words: `product_info` consults this only when a deferral is already open,
# the message names nothing, and no product came back. Under those three conditions "عايز اعرف"
# cannot be an opening question — there is nothing left for it to open.
_INSISTING = frozenset({
    "اعرف", "اعرفه", "اعرفها", "نعرف", "تعرف", "تعرفه", "تعرفها",
})


def insisting_on_a_promise(text):
    """True when a nameless message presses for an answer we owe, without a collection verb.

    Conversation 915 turn 13 is "اه عايز اعرف اسعاره": the customer had been told
    "لحظة أتأكدلك منه" about لادور بخور and came back for it. `chasing_a_promise` is False there by
    design — only the benefactive "اعرفلي" is a chase — so the turn was not read as the customer
    returning, the promise was repeated word for word, and the router handed the conversation to a
    human. That is the 816/817 dead end reached by a different route.

    Exact whole-token membership, so an inflection nobody listed is False. That is the direction to
    fail in: a miss costs the denial one more turn, while a false positive denies a perfume the
    customer never asked about.

    Deliberately no price vocabulary. "بكام؟" after a deferral asks about the perfume volunteered
    *alongside* the promise, not about the promise — the distinction `_CHASING`'s own comment exists
    to protect, and the one `test_a_price_question_after_a_deferral_is_not_a_chase` pins.
    """
    return any(token in _INSISTING for token in tokens(text))


# Pointers meaning "more than one of the perfumes you just named", as opposed to the singular "ده".
# Split out of `_REFERENTIAL` for the same reason `_CHASING` is: two callers need this subset for
# opposite reasons. The gate needs these words to count as naming nothing, and
# `product_info._referent_from_conversation` needs to recognise them positively — a plural pointer is
# the one case where the referent has to reach past the newest reply, because conversation 842
# introduced its two perfumes on two separate turns and "بكام الاتنين" meant both of them.
#
# 🔴 Exact membership only. Deliberately NOT stem-matched or edit-distance-matched the way
# `_CHASE_STEMS` is: a false positive here is the conversation-738 failure — `may_name_a_perfume`
# going blind to a perfume the customer actually named — so every entry is a literal that has been
# checked against the catalogue rather than a pattern that might swallow one. A plural typo nobody
# listed falls back to singular behaviour, which is the safe direction to fail in.
#
# Written in `normalize_arabic` form, like `_REFERENTIAL` itself, because `tokens` normalises before
# comparing.
_PLURAL_POINTERS = frozenset({
    "دول", "دو", "الاتنين", "التلاته", "كلهم", "كلهما", "الكل", "عطرين", "العطرين",
    # Observed in production: the definite article "ال" typed "لا" (conversation 841 turn 3,
    # "بكام لااتنين ؟"). Listed as its own literal rather than reached by tolerating a typo.
    "لااتنين",
})

# The vocabulary a customer uses to ask about a perfume already on the table, rather than to
# name a new one. Written in `normalize_arabic` form — "زجاجه" not "زجاجة", "متاكد" not "متأكد" —
# because `tokens` normalises before comparing. Words already in `_STOPWORDS` ("عطر", "برفان",
# "من") never reach this set.
#
# 🔴 Never add a word here that could be part of a perfume name, transliterated or not. "سبورت",
# "هوم", "بلو", "دارك" and "مليون" are all name components in this catalogue and are all absent
# on purpose. Adding one would make `may_name_a_perfume` blind to that perfume for good.
_REFERENTIAL = frozenset({
    # Pointers and pronouns. The plurals matter as much as the singulars: 835 turn 4 is
    # "طب عاملين كام دو" — how much are *those* — and with "دو" and "عاملين" both unlisted the message
    # read as naming a perfume, so the price question about what we had just offered went unanswered.
    # "الي" is "اللي" with one ل, and is the spelling conversation 1105 turn 2 used
    # ("البرفان الي جبته من عندكو"). The prefix stripping in `_probe_forms` cannot reach it —
    # dropping "ال" would leave a single letter, below the stem floor — so it is listed outright.
    "ده", "دي", "دا", "دول", "دو", "هو", "هي", "هما", "اللي", "الي", "منه", "منها", "منهم",
    "بتاعه", "بتاعها",
    # Question words. "اي" is the short spelling of "ايه" beside it, and "ها" is the particle
    # that opens "ها لقيت اي ؟" — without both, that message still tripped the gate on its
    # function words alone, so whether conversation 798 turn 2 was handled correctly came down
    # to what the resolver happened to return for a message naming no perfume at all.
    "بكام", "كام", "ايه", "اي", "ها", "ليه", "امتي", "فين", "هل", "عامل", "عامله", "عاملين",
    "ازاي",
    # Price, size and bottle vocabulary.
    "سعر", "سعره", "سعرها", "الاسعار", "اسعار", "حجم", "حجمه", "الحجم",
    "الاحجام", "احجام", "ملي", "ml", "زجاجه", "الزجاجه", "اوريجينال",
    "الاوريجينال", "علبه", "بوكس",
    # Attribute vocabulary.
    "ريحه", "ريحته", "ريحتها", "ثبات", "ثباته", "ثباتها", "فوحان", "فوحانه",
    "نوتات", "نوتاته", "مكونات", "مكوناته", "مناسب", "مناسبه", "ينفع", "يناسب",
    "موسم", "موسمه",
    # Availability and doubt.
    "متوفر", "متوفره", "موجود", "موجوده", "متاح", "متاكد", "بجد", "مش", "ولا",
    "فيه", "في", "عندكم", "عندك", "عندكو", "لسه",
    # Wanting and knowing. Conversation 915 turn 13 is "اه عايز اعرف اسعاره" — the customer
    # insisting on a price we had promised to go and check. With "عايز", "اعرف" and "اسعاره" all
    # unlisted, that message read as naming a perfume: the gate said yes, the resolver was asked
    # to place three verbs, and `named_but_unresolved` went True — which vetoes the chase carry in
    # `product_info`, so the promise was repeated verbatim instead of turning into a denial and
    # the router handed the conversation to a human. The resolver's answer was not even stable:
    # [] in production, six already-offered perfumes on replay, so the turn had two different
    # wrong outcomes depending on the call. "اسعار" and "الاسعار" were already listed above; the
    # possessive forms were the gap.
    "عايز", "عايزه", "عاوز", "عاوزه", "محتاج", "محتاجه", "ممكن",
    "اعرف", "اعرفه", "اعرفها", "تعرف", "نعرف",
    "اسعاره", "اسعارها",
    # Past-tense framing of the same wanting, plus the second-person-plural "about you".
    # "كنت محتاج اعرف عنكو اكتر" — "I wanted to know more about you" — was the single false
    # positive in the whole name-routing sweep (2961 stored messages): with "كنت", "اكتر" and
    # "عنكو" all unlisted they survived into `arabic_span`, padded it to "كنت عنكو اكتر", and
    # scored 0.727 coverage against *Silver Mountain Water*. Adding them removes that fire and
    # *recovers* a real one — "كنت عاوز سترونجر ويذ يو انتنسلي" had its own name diluted by the
    # same "كنت" and scored below the floor. Same direction as the conv726 caveat in
    # `product_resolver._disagreement`: the recall is in the vocabulary, not in the thresholds.
    "كنت", "كنا", "اكتر", "عنكو", "عنكم", "اكبر", "اصغر",
    # Discourse particles and confirmations.
    "طب", "طيب", "بقول", "بقولك", "قول", "قولي", "ماشي", "تمام", "ايوه", "اه",
    "لا", "كمان", "برضه", "بس", "خلاص", "يعني", "امال",
    # Quantifiers and ordinals.
    "كل", "واحد", "واحده", "لوحده", "لوحدها", "الاتنين", "التلاته", "التاني",
    "الاول", "الاخير", "بعض", "تاني", "تانيه",
# A message that only chases, or only points, names nothing — so the gate must not fire on either.
}) | _CHASING | _PLURAL_POINTERS


def refers_to_several(text):
    """Does this message point at more than one of the perfumes we just named?

    "بكام الاتنين" and "طب عاملين كام دو" — how much are *the two*, how much are *those*. Both name
    no perfume, so the answer rests entirely on the referent, and both mean the referent is plural.
    `product_info` needs that distinction twice: to widen the referent window past the newest reply
    (conversation 842 introduced one perfume per turn, so the newest reply alone held half the
    answer), and to require the reply to cover every row it was given.

    Exact membership, per `_PLURAL_POINTERS`. False on an unlisted plural typo, which costs the
    widening and nothing else — the caller falls back to the singular behaviour it has always had.
    """
    return any(token in _PLURAL_POINTERS for token in tokens(text))


# Praise. A customer telling us the perfume is wonderful is not naming one, but until this set
# existed every one of these words survived `identifying_tokens` and `may_name_a_perfume` said yes.
# Conversation 1105 is the cost: "البرفان تحفه وثباته ممتاز" and "البرفان الي جبته من عندكو واو بجد"
# were both answered "ممكن تكتب لي اسم العطر تاني بشكل أوضح؟" — a happy customer asked to spell out
# a name they had never typed, twice, with the compliment never acknowledged.
#
# 🔴 The warning on `_REFERENTIAL` applies unchanged: never add a word that could be part of a
# perfume name. Checked against all 213 distinct product names in every store — no praise word here
# equals a name token, and no product name in this catalogue is written in Arabic script at all, so
# an Arabic praise word structurally cannot *be* a name. "نار" is deliberately absent even though
# "العطر نار" is ordinary Egyptian praise: it is short, and short spans are exactly the ones a future
# catalogue could collide with. A miss costs one turn; a collision blinds the gate for good.
#
# Written in `normalize_arabic` form, like `_REFERENTIAL` — "تحفه" not "تحفة".
_PRAISE = frozenset({
    "تحفه", "تحف", "ممتاز", "ممتازه", "جميل", "جميله", "حلو", "حلوه", "رهيب", "جامد",
    "واو", "عجبني", "عجبتني", "عجبتنى", "تسلم", "ايدك", "روعه", "فظيع", "خطير", "قمه",
    "محترم", "برافو", "هايل", "جننت", "يجنن", "جنان", "مبسوط", "فخم", "اسطوره",
    # Thanks. In this corpus "شكرا" is overwhelmingly a farewell ("لا شكرا", "تمام شكرا مش عايز"),
    # which `router._is_goodbye_loop` already owns — it is here only so it stops counting as a name,
    # and `appreciation.detect` deliberately refuses to treat it alone as a compliment.
    "شكرا", "شكرن", "متشكر", "متشكره",
})

# Having bought or tried it already. The other half of conversation 1105 turn 2, where "جبته" is the
# last token standing once the praise and the article are gone — without it that message still reads
# as naming something unreadable. These are also what `objection._PAST_PURCHASE` matches on, but as
# phrases rather than tokens; the two are kept separate because that one decides whether a complaint
# is about something already owned, and this one only decides whether a word could be a name.
_PAST_PURCHASE = frozenset({
    "جبت", "جبته", "جبتها", "اشتريت", "اشتريته", "اشتريتها",
    "خدت", "خدته", "خدتها", "جربت", "جربته", "جربتها", "استخدمت",
})


# Shortest stem a prefix may be stripped down to. Three, and the reason is a real customer message:
# "عندك برفان واي" — "واي" is how this dialect spells the letter **Y**, and this catalogue holds a
# perfume called Y. Stripping the "و" leaves "اي", which *is* in `_REFERENTIAL`, so a floor of two
# would delete a real perfume name from the gate's view. That is the conversation-738 failure exactly.
# At three, "واي" is left alone while "وثباته" -> "ثباته" still resolves.
_MIN_STEM = 3

_PREFIXES = ("ال", "و")


def _probe_forms(token):
    """The token, plus what it looks like with the article and the conjunction peeled off.

    Membership in `_REFERENTIAL`, `_STOPWORDS` and the sets above is exact-string, so every one of
    them was blind to the two commonest prefixes in the dialect. "برفان" is a stopword but "البرفان"
    was not; "ثباته" is referential but "وثباته" was not; "سعر" is listed but "السعر" and "والسعر"
    were not. Filler therefore walked straight through the gate wearing an article, which is how a
    message made entirely of praise came to look like an unreadable perfume name.

    🔴 These forms are for *testing* only — the caller keeps the original token. "الترامل" is probed
    as "ترامل", matches nothing, and survives as "الترامل", which is the spelling `phonetic_ranking`
    and the resolver need to see. Rewriting the emitted token instead would quietly change the span
    every downstream matcher scores.

    Both prefixes, in either order, so "والسعر" reaches "سعر" through "السعر". Each strip is guarded
    by `_MIN_STEM`.
    """
    forms, pending = {token}, [token]
    while pending:
        current = pending.pop()
        for prefix in _PREFIXES:
            if not current.startswith(prefix):
                continue
            stem = current[len(prefix):]
            if len(stem) >= _MIN_STEM and stem not in forms:
                forms.add(stem)
                pending.append(stem)
    return forms


def _is_filler(token):
    """True when a token is a way of talking about a perfume rather than a way of naming one.

    The single place every "this is not a name" vocabulary is read, so the prefix handling applies to
    all of them at once and they cannot drift apart.
    """
    return any(
        probe in _REFERENTIAL
        or probe in _STOPWORDS
        or probe in _PRAISE
        or probe in _PAST_PURCHASE
        or _is_chase_token(probe)
        # "90 ملي", "50" — a size, not a name. Names carrying digits ("Afnan 9PM",
        # "XJ 1861 Naxos") tokenise with their words attached, so this cannot swallow one.
        or probe.isdigit()
        for probe in _probe_forms(token)
    )


def praise_tokens(text):
    """The praise words in a message, if any. The vocabulary lives here, beside the other wordlists.

    `sales.appreciation` reads this rather than keeping a second copy, so a word added for the gate's
    benefit also teaches the detector, and the two can never disagree about what counts as praise.
    """
    return {token for token in tokens(text) if _probe_forms(token) & _PRAISE}


def identifying_tokens(text):
    """The tokens of a message that could belong to a perfume name.

    `tokens` minus the vocabulary `_REFERENTIAL` and `_CHASING` collect, minus bare digits — the
    residue left once every way of asking *about* a perfume has been stripped out. Split out because
    two callers need the same residue for opposite questions and must not drift:
    `may_name_a_perfume` asks whether it is non-empty, and `re_asks` asks whether two messages
    share it.

    `_CHASING` is excluded for the reason its own comment gives — verb forms and question particles
    only, and no name in this catalogue tokenises to any of them. Leaving it in made a bare "اتأكد"
    look like an unplaceable name: `may_name_a_perfume` said yes, the resolver came back empty,
    `named_but_unresolved` went True, and that flag vetoes the chase carry in `product_info`. So the
    one message that is nothing *but* a chase could not be read as one. 816 turn 4 is that message,
    and it was answered "لحظة أتأكدلك منه" one turn after being told the perfume is not stocked.

    Excluded through `_is_chase_token` rather than set membership, so an inflection nobody listed is
    excluded too. That is the other half of 835 turn 2: "لقيتو" was not in `_CHASING`, so it survived
    as an identifying token, `may_name_a_perfume` read "ها لقيتو ؟" as naming something, and the
    resolver was asked to place a verb.

    Every wordlist is consulted through `_is_filler`, which tests the token with the article and the
    conjunction peeled off as well as bare — see `_probe_forms` for why that is a test and not a
    rewrite. The token that survives is always the one the customer typed.
    """
    return {token for token in tokens(text) if not _is_filler(token)}


def may_name_a_perfume(text):
    """Could this message be naming a perfume, as opposed to asking about one already offered?

    A gate, not a matcher: it answers "is it worth looking a name up at all", and it exists
    because `mentioned_in` cannot answer that question. `mentioned_in` needs the catalogue's
    Latin tokens to appear in the text, so an Arabic-script name matches nothing — and
    `product_info` was reading that empty result as proof the customer had named nothing, then
    answering about whatever it had offered on the previous turn. A customer asked
    "طب اكوا دي جيو ؟" and was told about Y Eau de Parfum, a different perfume from a different
    brand that happened to be the previous recommendation (conversation 738).

    Deliberately liberal, and the asymmetry is the whole point:

      * False on a message that DOES name a perfume is the conversation-738 bug — the gate goes
        blind and the previous perfume is answered about instead.
      * True on a message that names nothing is the cheaper failure, but it is no longer free.

    🔴 It used to be free, and this docstring used to say so: a false alarm cost one resolver call
    that came back empty, and the caller fell back to the referent it would have used anyway. That
    stopped being true when `product_info`'s `named_but_unresolved` grew its `(resolver_ran and not
    products)` clause. A resolver call that places nothing now *is* the evidence that the customer
    named something unreadable, so a false alarm produces "please write the name again" — a question
    the customer cannot answer, because they never typed a name. Conversation 1105 is that failure:
    praise read as a name, twice in a row.

    So the wordlists do have to be reasonably complete, and a word missing from them is a wrong
    answer rather than a slow one. They are still far more dangerous to extend carelessly than to
    leave short — see the 🔴 on `_REFERENTIAL` — but "it is only latency" is no longer the reason to
    relax about a gap.
    """
    return bool(identifying_tokens(text))


# Asking for something *like* a perfume rather than *for* it. "عايز عطر ريحته خمره" — a perfume that
# smells boozy — names Khamrah and does not ask about it, and the difference is the whole turn: one
# wants a shortlist, the other wants one bottle's price.
#
# 🔴 Not a tokenisation rule, and that is why it is a separate collection rather than more entries in
# `_REFERENTIAL`. Every word here is *also* legitimate filler around a real name — "عندكو عطر ريحته
# حلوه؟" names nothing, so these already count as filler for the gate's purposes. What they add is a
# positive signal in the other direction, and that signal has exactly one consumer:
# `router`'s catalogue correction, which must not drag a scent request onto `product_info`.
# Putting them in `_REFERENTIAL` would do nothing for this job, because by the time a span exists the
# words are already gone.
#
# Substring-matched rather than token-matched, because two entries are multi-word ("قريب من") and
# because the one-letter pointers "زي" and "شبه" have to be whitespace-bounded or they fire inside
# ordinary words. The caller pads the message with spaces first.
_DESCRIBING = (
    "ريحته", "ريحتها", "ريحه",
    # Every inflection, because this dialect suffixes freely and a form nobody listed is a browse
    # request answered with one bottle's price. "نوتاته" is already in `_REFERENTIAL`; the possessive
    # singulars were the gap, and "عايز عطر نوتته خمره" is the shape that found it.
    "نوته", "نوتة", "نوتات", "نوتته", "نوتتها", "نوتاته",
    " زي ", " شبه ", "قريب من", "قريبه من", "يشبه", "تشبه", "شبيه", "شبيهه",
)


def describes_rather_than_names(text):
    """True when the message wants something *like* a perfume, not that perfume.

    Measured against the five phrasings a customer actually used for this in production plus the
    synthetic forms: "عايز عطر ريحته خمره", "عايز حاجه زي خمره", "في حاجه قريبه من خمره" are all
    True, and "عندكو خمره" / bare "خمره" are both False.

    Deliberately crude, and the failure direction is chosen. A false positive costs the catalogue
    correction one turn — the classifier's own answer stands, which is what happens today anyway. A
    false negative answers a browse request with one perfume's price list. So when in doubt this
    says True, and that is also why it is consulted *before* the catalogue is scored rather than
    after: a scent request should not even reach the ranking.
    """
    if not text:
        return False
    padded = f" {normalize_arabic(text)} "
    return any(marker in padded for marker in _DESCRIBING)


# Arabic single letters are particles — "ف أماكن تاني", "حاجه ب 500", "ديور و شانيل". Both of those
# first two are real customer messages, and the catalogue holds Latin names only, so no perfume here
# can ever be spelled with one Arabic letter. Matching on the block rather than listing و ف ب ل ك
# keeps an unlisted particle out too.
_ARABIC_LETTER = re.compile(r"[؀-ۿ]")

# The same rule on the Latin side: the only one-letter words English has. "a" was by far the most
# common standalone single character in the corpus measured below — 50 of 56 spans, every one of them
# the article. Almost all of those arrive in a message that also names something readable ("do u have
# a dior"), and the guard at the top of `carries_unreadable_content` already returns on those before
# reaching here; what is left for this set is the message whose identifying residue is *empty* and
# whose leftover is the article alone. Cheap, and it keeps the two alphabets reasoning the same way.
# Nothing else is excluded: "X" and "Y" stay, because "Y" is a perfume in this catalogue.
_SINGLE_LETTER_WORDS = frozenset({"a", "i"})


def carries_unreadable_content(text):
    """Single characters the customer typed as words of their own, which `tokens` threw away.

    The companion to `may_name_a_perfume`, for the case it cannot see. `tokens` drops every token
    shorter than two characters, so a one-character name leaves nothing behind — and because `في`,
    `طب`, `عندك`, `ممكن` and `اعرف` are all in `_REFERENTIAL`, the words around it are subtracted too.
    "في Y" therefore has no identifying tokens at all, exactly like "بكام ده" does. `product_info`
    reads that emptiness as proof the customer named nothing and answers about whatever it offered
    last turn — with prices. This catalogue really does hold a perfume called Y.

    So the distinction this draws is not "does the message name a perfume" but the narrower one the
    caller actually needs: *did the message contain something we could not read*. Empty means the turn
    is a genuine reference and the referent is the legitimate subject; non-empty means ask the
    customer to retype rather than guess.

      "في Y"          -> {'y'}        "بكام ده"        -> set()
      "طب Y"          -> {'y'}        "Terre d'Hermes" -> set()
      "B m w"         -> set()        "خليها 2 بدل واحده" -> set()

    Three exclusions, each measured against every customer message in conversation 1021 and every
    Arabic string in `eval_harness/scenarios*.py` rather than guessed at:

      * **Not a fragment of a longer word.** `\\W+` shears apostrophes, so "Terre d'Hermes" splits to
        "d" + "hermes" and "L'Eau" to "l" + "eau". Those are the most common single Latin letters in
        the corpus and they are not unreadable at all. Hence the whitespace-word test: only a
        character the customer typed *alone* counts.
      * **Not Arabic**, and **not a one-letter English word** — see the two definitions above.
      * **Not a digit**, including Arabic-Indic ٢٣٨ — "خليها 2 بدل واحده", "حاجه ب 500", "إلا ٣" are
        quantities and sizes.

    And nothing `_fuse_spelled_out` already joined: "B m w" is now readable as "bmw", so reporting it
    unreadable would undo that fix.

    Finally, and it is the load-bearing one: **only when the message has no identifying tokens at
    all.** An unreadable character next to a name we *can* read is not this bug — "عندك Y ولا Terre
    d Hermes" resolves Terre d'Hermes and should answer about it, and "do u have a dior" resolves
    Dior. Both would otherwise be sent down the retype path over one stray letter, and "u" for *you*
    is ordinary typing, not a perfume.

    That also removes the need to guess at chat shorthand. The condition here is not "is this
    character a word" — it is the exact state that caused the bug: a message whose identifying
    residue is empty, so `may_name_a_perfume` is False, so the resolver is never called and the
    previous turn's rows become the answer. When something else in the message did survive
    tokenising, the resolver runs and `unplaced` reports the rest; this function has no work to do.
    """
    if identifying_tokens(text):
        return set()

    normalized = normalize_arabic(text or "")
    standalone = set()
    for word in normalized.split():
        bare = re.sub(r"^\W+|\W+$", "", word, flags=re.UNICODE)
        if re.fullmatch(r"\w", bare, flags=re.UNICODE):
            standalone.add(bare)
    if not standalone:
        return set()
    cleaned = re.sub(r"\W+", " ", normalized, flags=re.UNICODE)
    unfused = {token for token in _fuse_spelled_out(cleaned.split()) if len(token) == 1}
    return {
        char
        for char in standalone & unfused
        if not char.isdigit()
        and not _ARABIC_LETTER.match(char)
        and char not in _SINGLE_LETTER_WORDS
        and char not in _STOPWORDS
        and char not in _REFERENTIAL
    }


# Egyptian phonetic spelling, one Arabic letter at a time. Not a transliteration standard and not
# trying to be one — the job is to put a customer's Arabic and a catalogue's Latin into the same
# alphabet closely enough that `difflib` can say whether they are the same word. Choices that look
# wrong against a standard and are right against how people here actually type: ج→g (Cairene, so
# "جنتل مان" reaches "Gentleman"), ق→k, ع→a ("سعرهم"→"sarhm"), and the emphatics folded onto their
# plain partners because nobody hears the difference when spelling a French name.
#
# 🔴 Deliberately lossy, and the collisions matter when reading a score: ز/ذ/ظ all become z, س/ص
# both s, ت/ط both t, ك/ق both k, and ج/چ both g — so "چاكي" and "جاكي" are one string here.
# `normalize_arabic` has already folded ة→ه before this runs, and ه is h, so every feminine filler
# word ends in h ("حاجه"→"hagh", "ريحه"→"ryhh"). That is where junk matches come from; it is the
# reason the caller's rule is a separation gap rather than an absolute score.
_ARABIC_TO_LATIN = {
    "ا": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "g", "ح": "h", "خ": "kh",
    "د": "d", "ذ": "z", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s",
    "ض": "d", "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f", "ق": "k",
    "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h", "و": "w", "ي": "y",
    # Persian/Egyptian extras people reach for when an Arabic letter has no sound for it.
    "پ": "p", "چ": "g", "ژ": "j", "ڤ": "v", "ک": "k", "گ": "g", "ی": "y",
    # Hamza carries no sound of its own once the alef variants are normalised away.
    "ء": "", "ؤ": "w", "ئ": "y",
}


def transliterate(span):
    """An Arabic span rewritten in Latin letters, for comparison against catalogue spellings.

    Per word, because the definite article has to come off and only the front of a word can carry
    it: "الترامل" is "al" + "traml", and leaving the article on costs two characters of pure noise
    against "ultramale". Stripped only when something is left worth keeping, so a short word that
    merely begins with those two letters survives whole.

    Anything with no entry in the map is dropped rather than passed through — diacritics are already
    gone, and a stray emoji or punctuation mark would otherwise count as a mismatched character.
    Latin letters and digits the customer typed themselves are kept, since they are already in the
    target alphabet; in practice the caller refuses to score a span that contains any.
    """
    out = []
    for word in normalize_arabic(span or "").split():
        letters = "".join(
            _ARABIC_TO_LATIN.get(char, char if char.isascii() and char.isalnum() else "")
            for char in word
        )
        if len(letters) > 3 and letters.startswith("al"):
            letters = letters[2:]
        out.append(letters)
    return "".join(out)


def arabic_span(text):
    """The words of a message that could be a perfume name, **in the order they were typed**.

    A view of `identifying_tokens` for the one caller that cannot use its return value directly:
    scoring a name needs the customer's letters as a sequence, and that function returns a `set`.

    🔴 That is the whole reason this exists, and it is not a style preference. `"".join(
    identifying_tokens(text))` iterates a set of strings, whose order is `PYTHONHASHSEED`-dependent
    and therefore varies between processes. Measured on one four-token message across three runs:
    "بلوايروسڤيرزاتشيسوفاج", "بلوسوفاجايروسڤيرزاتشي", "بلوايروسڤيرزاتشيسوفاج". A score built on that
    join is nondeterministic in production and unreproducible in a backtest, while passing every
    unit test that happens to use a one-token span. So the message is re-split and filtered against
    the set rather than the set being joined.

    Space-separated, so the word count survives for a caller that needs it and the string is
    readable in a log line.
    """
    keep = identifying_tokens(text)
    if not keep:
        return ""
    cleaned = re.sub(r"\W+", " ", normalize_arabic(text or ""), flags=re.UNICODE)
    return " ".join(token for token in _fuse_spelled_out(cleaned.split()) if token in keep)


def _latin_key(name):
    """A catalogue name reduced to the same alphabet and shape `transliterate` produces."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def phonetic_ranking(text, store, products=None):
    """How well each catalogue name matches the customer's Arabic, best first.

    Returns `[(score, matched_chars, product), …]` — `difflib.SequenceMatcher.ratio()` of the
    transliterated span against the product's Latin spelling, plus the number of characters that
    actually lined up. Empty when there is no Arabic worth scoring.

    The gap this closes is the one `candidates` and `names_a_bare_brand` both document and neither
    can: `Product.name` holds Latin spellings, there is no alias column and no Arabic-name column,
    so every token-based matcher in this module returns nothing for an Arabic string whether we
    stock the perfume or not. Conversation 1041 is what that costs on the other channel from the
    denials — a customer wrote "في التراميل ؟" (Ultra Male, in stock) and was quoted Terre
    d'Hermes' prices by name, because nothing compared the model's answer to the customer's letters.

    🔴 This ranks; it does not decide. A #1 here is not a match and must never be substituted for
    what the extractor said — that is the "اوداورا" → *Dark Aura* substitution `product_resolver`
    records as actively dangerous. The thresholds that turn this into a decision live with the
    callers, per this module's contract — see `absence.py:19-21`.

    There are two sound uses, and the second was added after this docstring first claimed there was
    only one (*disagreement*). Both ask a question that does not require picking a row:

      * **disagreement** — `product_resolver._verify_placement`: one row separates clearly from the
        field and the model placed a different one, so something is wrong and a question is cheaper
        than a confident wrong price. A fire *withholds*.
      * **routing** — `product_resolver.confident_catalogue_match`, read by `router`: one row
        separates clearly and the classifier sent the turn somewhere that will never look a name up
        at all. A fire *reroutes*, to the branch that then does its own resolution from scratch.
        Conversation 1106: "عندكو خمره" answered with two boozy niche perfumes while four Lattafa
        Khamrahs sat in stock, because `خمره` is also the ordinary word for liquor and the
        classifier cannot see the catalogue. Still not a placement — the reroute changes which
        branch runs, and `resolve_products` inside it remains the only thing that picks the perfume.

    ⚠️ The two run on different populations, which is why each carries its own backtest
    (`eval_harness.backtest_placement`, `eval_harness.backtest_name_routing`). The same thresholds
    are a veto in one and a proposal in the other, and a false-positive rate measured for the first
    says nothing about the second.

    Both `name` and `brand + name` are scored, but the brand form **only for a span of two or more
    words**. This catalogue lists Versace Eros as the bare "Eros", so "ڤيرزاتشي ايروس" has to be
    able to reach it; a one-word span has no brand in it to reach with, and scoring both forms
    unconditionally gives every row two draws at the maximum, which inflates the top score more than
    the model's pick and widens the separation gap on noise.

    ⚠️ **The score is only as good as the span, and the span is only as good as `_REFERENTIAL`.**
    Every filler word left in dilutes the ratio, because the denominator is the whole span. Measured:
    "سترينجر وذ يو انتنسلي" scores 0.667 against its own row, but the same name inside
    "انااا بتكلم دلوقتي سعر سترينجر وذ يو انتنسلي عامل كام" scores 0.483 — `انااا`, `بتكلم`,
    `دلوقتي` and `عله` are not in `_REFERENTIAL`, so they survive into the span. Both of those turns
    were answered with the wrong perfume in production and `product_resolver`'s guard misses them
    for exactly this reason. Adding the missing vocabulary is worth more here than moving any
    threshold; it is deferred only because `_REFERENTIAL` also gates whether the resolver runs at
    all (`product_info.get_product_info`) and so needs its own regression floor.
    """
    from difflib import SequenceMatcher

    span = arabic_span(text)
    if not span or store is None:
        return []
    latin = transliterate(span)
    if len(latin) < 2:
        return []

    if products is None:
        from products.models import Product

        products = Product.objects.filter(store=store, is_active=True).select_related("brand")

    with_brand = len(span.split()) >= 2
    ranked = []
    for product in products:
        forms = [_latin_key(product.name)]
        if with_brand:
            try:
                brand = product.brand.name
            except Exception:
                brand = ""
            if brand:
                forms.append(_latin_key(brand) + _latin_key(product.name))

        best_score, best_chars = 0.0, 0
        for form in forms:
            if not form:
                continue
            matcher = SequenceMatcher(None, latin, form)
            score = matcher.ratio()
            if score > best_score:
                best_score = score
                best_chars = sum(block.size for block in matcher.get_matching_blocks())
        if best_score:
            ranked.append((best_score, best_chars, product))

    # Name as the tiebreak so the order is total and a backtest reruns identically.
    ranked.sort(key=lambda row: (-row[0], row[2].name))
    return ranked


def _similar_enough(left, right):
    """One-edit tolerance for a single-token difference.

    Exists for "Ambiro" vs "Ambero" — a one-character slip in model output that
    otherwise costs the reference, the exclusion, and the persisted preference all at
    once. Deliberately narrow: same length, exactly one differing character, and long
    enough that the coincidence rate is low.
    """
    if left == right:
        return True
    if len(left) != len(right) or len(left) < 5:
        return False
    return sum(1 for a, b in zip(left, right) if a != b) == 1


def _overlap(wanted, candidate):
    """How many of `wanted`'s tokens the candidate carries, allowing one typo each."""
    hits = 0
    for token in wanted:
        if any(_similar_enough(token, other) for other in candidate):
            hits += 1
    return hits


def re_asks(message, earlier):
    """True when `message` asks about the same thing an earlier message named.

    Both messages are ones the catalogue could not place, so there is no name on either side to
    compare — only the customer's own words. `product_info` needs the comparison anyway, because
    re-typing a name is how a customer insists and it must not read as a brand-new question:

      816  "عندك الكساندريا 2؟"        → "لحظة أتأكدلك منه"
           "بتكلم علي الكساندريا 2؟"    → "لحظة أتأكدلك منه" again, and that was the last reply
                                          the customer ever got.
      817  is the same two turns with لادور بخور.

    Compares `identifying_tokens`, so every way of *asking* has already been stripped from both
    sides and what is left is the closest thing to a name either message has. "بتكلم علي
    الكساندريا 2؟" keeps {الكساندريا, بتكلم, علي} against {الكساندريا} — the leftover verb and
    particle are why this is not a set equality.

    The rule is that the shared tokens cover the **smaller** of the two residues. Requiring the
    earlier message to be covered fails a customer who first asked verbosely and then re-asked in
    two words; requiring the current one to be covered fails the reverse. Covering the smaller
    side accepts both, and is still empty-handed on the case that has to stay negative: 795 turn 4
    asks "طب عندكو الكساندريا 2 ؟" while لادور بخور is the open question, the two share nothing,
    and that turn keeps the first deferral it has earned rather than inheriting a denial.

    A short message that repeats only one word of a longer question ("عندكو بخور؟" after "عندكو
    لادور بخور صح ؟") counts as a re-ask, which is the intended reading: the customer is still on
    the same thread and has already been promised a check once.
    """
    wanted = identifying_tokens(earlier)
    got = identifying_tokens(message)
    if not wanted or not got:
        return False
    return _overlap(wanted, got) >= min(len(wanted), len(got))


def candidates(name, store, products=None):
    """Every catalogue product a name could refer to, split into `(exact, partial)`.

    Split out of `match_product`, which collapses two very different outcomes into the same
    `None`: "no product in the catalogue looks like this name" and "several do, so picking one
    would be a guess". A caller deciding whether to *deny* a perfume needs those apart —
    denying on an ambiguous tie would tell a customer we do not carry something we do, which
    is the worst outcome in this system. See `products.services.absence`.

    Returns two lists of products, either or both possibly empty. A bare brand word
    contributes nothing to `partial` — the reason is at the skip below — so "Dior" comes back
    `([], [])`, indistinguishable here from a name we have never heard of. A caller that must
    tell those apart has to check for a bare brand itself.
    """
    wanted = tokens(name)
    if not wanted or store is None:
        return [], []

    if products is None:
        from products.models import Product

        products = Product.objects.filter(store=store, is_active=True).select_related("brand")

    exact, partial = [], []
    for product in products:
        candidate = tokens(product.name)
        if not candidate:
            continue
        hits = _overlap(wanted, candidate)
        reverse = _overlap(candidate, wanted)
        # Subset in either direction, measured with the typo tolerance applied.
        if hits < len(wanted) and reverse < len(candidate):
            continue
        if len(candidate) == len(wanted) and hits == len(wanted):
            exact.append(product)
            continue
        # A bare brand word ("Dior", "Tom Ford") names a house, not a perfume. It may
        # happen to be a subset of exactly one product name, and returning that product
        # would present an arbitrary pick as though the customer had named it — the
        # similarity engine would then cite its real notes as evidence for a request that
        # never mentioned it. Falling through to None keeps the reference on
        # general-knowledge notes, which the prompt labels as the weaker evidence it is.
        #
        # Latin spellings only; "شانيل" tokenises to nothing this can compare. See
        # `names_a_bare_brand`, which shares the test and carries the consequence.
        try:
            brand_tokens = tokens(product.brand.name)
        except Exception:
            brand_tokens = set()
        if brand_tokens and wanted <= brand_tokens:
            continue
        partial.append(product)

    return exact, partial


def names_a_bare_brand(name, store, products=None):
    """Does this name say a house and nothing more — "Dior", "Tom Ford", "Rasasi"?

    The companion `candidates` needs: it drops bare-brand partials for the reason given at that
    skip, so a bare brand comes back from it looking exactly like a name we have never heard of.
    A caller that would otherwise deny the name has to be able to tell the two apart, because
    "we don't carry Dior" is false in a store with three Diors on the shelf.

    True when the name's identifying tokens are a subset of some brand's, so "Tom" alone counts
    as naming Tom Ford — deliberately, since the cost of a false True here is a request to
    clarify and the cost of a false False is a denial.

    🔴 Latin spellings only, and not by choice: the comparison is against `Brand.name`, which holds
    "Chanel" and never "شانيل", and nothing in this codebase bridges the two. So "شانيل" returns
    False here — a false False, the expensive direction. `absence.catalogue_verdict` documents what
    covers that case instead at the rung that calls this.
    """
    wanted = tokens(name)
    if not wanted or store is None:
        return False

    if products is None:
        from products.models import Product

        products = Product.objects.filter(store=store, is_active=True).select_related("brand")

    for product in products:
        try:
            brand_tokens = tokens(product.brand.name)
        except Exception:
            continue
        if brand_tokens and wanted <= brand_tokens:
            return True
    return False


def match_product(name, store, products=None):
    """The catalogue product a name refers to, or None if it is ambiguous.

    A candidate qualifies when one name's identifying tokens are contained in the
    other's, so "sauvage" matches "Dior Sauvage" and "9pm afnan" matches "Afnan 9PM".

    Ambiguity returns None rather than a guess. A bare brand word like "Dior" is a subset
    of three different perfume names here, and silently picking one of them would be
    worse than not matching: `exclude_names=["Dior"]` has to keep excluding every Dior,
    which is exactly what the plain substring filter does when this returns None. An
    exact token match always wins, so "Stronger With You" still resolves to itself rather
    than to "Stronger With You Intensely".
    """
    exact, partial = candidates(name, store, products)

    if len(exact) == 1:
        return exact[0]
    if exact:
        # Two rows with the same identifying tokens is a catalogue problem, not something
        # to resolve by guessing.
        return None
    return partial[0] if len(partial) == 1 else None


def resolve_names(names, store, products=None):
    """Turn model-produced names into the catalogue spellings they refer to.

    Unmatched names are kept as written: an exclusion the customer clearly meant should
    still be attempted as a substring rather than silently dropped, and a similarity
    target we do not stock is still usable as a general-knowledge reference.
    """
    resolved = []
    for name in names or ():
        if not name:
            continue
        product = match_product(name, store, products)
        value = product.name if product else name
        if value not in resolved:
            resolved.append(value)
    return resolved


def mentioned_in(text, products):
    """Which of `products` the text names.

    Companion to `match_product`, reversed: that one takes a name and finds the product,
    this takes free text and finds every product it refers to. Reuses the same `tokens()`
    and stopword handling, so "Noirvel (90ml)" resolves the way "9pm by Afnan" already does.

    Written for the router's cancel branch. "مش عايز 1 × Noirvel (90ml)" is a request to
    remove one line of two, but it was classified `order_cancel` — "مش عايز" was a listed
    example of it — and the branch then wiped the whole cart, name and address included. The
    order flow already knows how to remove a single item; it needed a way to tell that this
    message names one.

    A product matches when every one of its identifying tokens appears in the text, so
    "Le Male" is not matched by a message that only says "Le". `_similar_enough` gives the
    same one-character tolerance as elsewhere, since a customer retyping a name from a
    summary line will occasionally miss a letter.
    """
    haystack = tokens(text)
    if not haystack:
        return []

    found = []
    for product in products:
        wanted = tokens(product.name)
        if not wanted:
            continue
        if all(
            any(_similar_enough(token, other) for other in haystack)
            for token in wanted
        ):
            found.append(product)
    return found


def names_in(text, names):
    """Which of `names` the text actually says, in order of first appearance.

    Catalogue names nest. "Stronger With You" is a prefix of "Stronger With You Intensely",
    so the obvious `name.lower() in text` reports the base as said whenever only the flanker
    was said — and every caller downstream then treats two perfumes as one.

    Conversation 768 is what that costs. Turn 1 named only Intensely, the search had injected
    both rows, so `described.under_discussion` recorded the base as under discussion without
    it ever having been said; `ranking.WEIGHTS["continuity"]` promoted that phantom into turn
    3's answer, and the customer was quoted two prices for what they reasonably read as one
    perfume. Asked about it, the bot apologised and retracted the correct one.

    The rule is to consume the LONGEST match at each position: a span already claimed by
    "Stronger With You Intensely" cannot also be claimed by the shorter name nested inside it.
    A name that genuinely appears elsewhere in the text is still found there, so a reply naming
    both perfumes yields both.

    Matched case-insensitively on the stored spelling, which is what every caller needs —
    names are stored and emitted in Latin. An Arabic transliteration matches nothing here, the
    same limitation `already_described` already documents, and that turn behaves as it does
    today.
    """
    haystack = (text or "").lower()
    if not haystack:
        return []

    longest_first = sorted((name for name in names if name), key=len, reverse=True)
    found, remaining = [], set(longest_first)
    position = 0
    while position < len(haystack) and remaining:
        for name in longest_first:
            if name in remaining and haystack.startswith(name.lower(), position):
                found.append(name)
                remaining.discard(name)
                position += len(name)
                break
        else:
            position += 1
    return found


def families(catalogue):
    """Every line in the catalogue at once: `{name: [its line-mates]}`, names with mates only.

    The batch form of `line_mates` below, and now the only definition of "same line" — that function
    delegates here. Two reasons it is the primitive rather than the convenience:

      * `line_mates` re-tokenises every name of a brand on each call, so calling it per product
        tokenises the same brand k times for a k-product brand. Both callers are batch callers:
        `product_formatting._line_mates_for` loops over an injected batch, and `product_resolver`
        needs the whole structure to put in a prompt. One pass tokenises each name once.
      * Conversation 1021 turn 22 is what happens when the extractor does not know lines exist. A
        customer asked for "لامال لكريز" — Le Male Elixir, in stock — and because the prompt listed
        `Le Male`, `Le Male Elixir` and `Ultra Male` as three unrelated strings, a name whose head is
        a line root and whose tail is a flanker word read as one unknown perfume. It was reported
        unplaced and denied, and two turns later the same conversation listed it as available. The
        grouping needed to prevent that already existed here; nothing was showing it to the model.

    `catalogue` is (name, brand_id) pairs — brand *id*, not brand name, so one `values_list` serves a
    whole batch without joining. A name repeated in the catalogue is taken at its first occurrence,
    matching `line_mates`' own first-match brand lookup.

    "Same line" is deliberately narrow, and the reasoning lives in `line_mates`' docstring: same
    brand, one name's identifying tokens contained in the other's, resolved through the line ROOT so
    that grouping is transitive.
    """
    by_brand = {}
    seen = set()
    for name, brand_id in catalogue:
        if not name or name in seen:
            continue
        seen.add(name)
        by_brand.setdefault(brand_id, []).append((name, tokens(name)))

    grouped = {}
    for members in by_brand.values():
        for name, own in members:
            if not own:
                continue
            # The fewest-token name this one contains — itself, when it is already the line's base.
            root = own
            for _, other in members:
                if other and other < root:
                    root = other
            mates = sorted(
                other_name for other_name, other in members
                if other_name != name and other >= root
            )
            if mates:
                grouped[name] = mates
    return grouped


def line_mates(name, catalogue):
    """The other perfumes on `name`'s line: same brand, one name nested in the other.

    A flanker is not "a completely different perfume" the way conversation 738's Acqua di Gio
    was — it shares a brand, a name and a family, and differs in scent, composition and price.
    Nothing in the injected data said so, so on conversation 768's last turn, where only the
    base's row was injected, the model had no way to know the 780 it had quoted three turns
    earlier belonged to a *different* perfume. It apologised for a mistake it had not made and
    declared the base's 700 "السعر الصحيح", leaving the customer believing Intensely costs 700.

    `catalogue` is (name, brand_id) pairs, so one query serves a whole batch.

    "Same line" is deliberately narrow: same brand, and one name's identifying tokens
    contained in the other's. Nesting is the condition that both makes a customer's shorthand
    ambiguous ("سترونجر" fits three rows) and defeats a substring test, so it is the condition
    worth guarding. `Dior Homme Intense` and `Dior Homme Sport` are NOT grouped — neither
    name's tokens contain the other's — and that is an accepted limit, not an oversight.
    Widening to "same brand + 2 shared tokens" would be a change to this function alone.

    Resolved through the line's ROOT rather than pairwise, which is what makes it transitive.
    Asked about Intensely, pairwise containment finds only the base: Absolutely is neither a
    subset nor a superset of it. Rooting on {stronger, with, you} finds both.

    A single-name convenience over `families` above, which does the work. Prefer `families` directly
    when you have more than one name to ask about — this rebuilds the whole grouping per call.
    """
    return families(catalogue).get(name, [])
