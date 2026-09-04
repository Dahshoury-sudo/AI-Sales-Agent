"""Strip phrasing the prompt forbids but the model still produces.

`conv_651.txt` closed three separate replies with "تحب تعرف أسعارهم والأحجام؟" — a
question quoted *inside* the persona (prompts.py, PERSONA section) as forbidden. With
dozens of competing absolute rules the model drops some of them, so the ones that are
purely mechanical are enforced here instead of asked for again.

Applied before the reply is saved, not at send time: a banned phrase persisted to
Message flows back through build_llm_history on the next turn and reads to the model
as an example of its own acceptable output.

This deliberately does not add a replacement CTA. The persona allows a reply to end
after answering, and inventing a closer here would put words in the model's mouth that
the product data may not support.
"""

import logging
import re
from decimal import Decimal

logger = logging.getLogger(__name__)


# Each pattern matches a whole trailing question, including the whitespace before it,
# so removing it leaves the preceding sentence intact.
#
# The optional connector in front of every one of them has to absorb a *whole* conditional
# opener rather than half of one. `(?:و\s*)?` on its own matched the و **inside** لو ("if"),
# because both letters are word characters and the group happily started mid-word — so
# "…وأغنى شوية. لو تحب تعرف الأسعار؟" was stripped down to "…وأغنى شوية. ل" and the reply
# ended on an orphaned letter. Evaluation caught that in a live reply (scenario M3).
# A `\bو` fix is not enough: it turns the orphaned ل into an orphaned لو, which is a reply
# ending in "if". The alternation below consumes لو / ولو / هل / a bare و together with the
# question, and _trim_dangling_connector is the backstop for whatever slips past it.
# "هل" was added after a live reply ended on it: "...1100 جنيه. هل" — the interrogative
# particle introduces the question that was just removed and can never end a sentence.
_LEAD = r"\s*(?:(?:و\s*)?لو\s+|(?:و\s*)?هل\s+|و\s*)?"

BANNED_CLOSERS = (
    # "تحب تعرف الأسعار والأحجام المتاحة؟" / "تحب تعرف أسعارهم والأحجام؟"
    re.compile(
        _LEAD + r"تحب[يى]?\s+تعرف\s+(?:ال)?[أاإ]?سعار\S*"
        r"(?:\s*و\s*(?:ال)?[أاإ]?حجام\S*)?(?:\s+المتاحة)?\s*[؟?]"
    ),
    # The same forbidden move in the first person, which is how the model actually
    # phrased it in evaluation: "تحب أعرفك الأسعار والأحجام؟" / "تحب أقولك الأسعار؟".
    # The pattern above requires "تعرف" and matched none of these, so the single
    # question the persona quotes verbatim as forbidden went out repeatedly.
    re.compile(
        _LEAD + r"تحب[يى]?\s+[أاإ](?:عرفك|قولك)\s+(?:على\s+)?(?:ال)?[أاإ]?سعار\S*"
        r"(?:\s*و\s*(?:ال)?[أاإ]?حجام\S*)?(?:\s+المتاحة)?\s*[؟?]"
    ),
    # "تحب أعرفك أكتر عن الأحجام دي؟" — same emptiness, different noun.
    re.compile(_LEAD + r"تحب[يى]?\s+[أاإ]عرفك\s+[أاإ]كتر\s+عن\s+[^؟?]{0,40}[؟?]"),
    # "عايز حاجة تانية؟" / "محتاج مساعدة في حاجة؟" / "محتاج حاجة تانية؟"
    re.compile(_LEAD + r"(?:عايز|محتاج|تحب)\s+(?:حاجة\s+تانية|مساعدة(?:\s+في\s+حاجة)?)\s*[؟?]"),
    # The statement form, which carries no question mark and so escaped the pattern
    # above: "لو حابب أساعدك في حاجة تانية، تحت أمرك."
    re.compile(_LEAD + r"حابب\s+[أاإ]?ساعدك\s+في\s+حاج[ةه]\s+تاني[ةه][^.؟?]*[.؟?]?"),
    # "عطر معين في بالك؟"
    re.compile(_LEAD + r"(?:فيه\s+)?عطر\s+معين\s+في\s+بالك\s*[؟?]"),
    # "أقدر أساعدك إزاي؟" / "أقدر أساعدك في إيه؟" — the persona's own banned opener.
    re.compile(_LEAD + r"[أاإ]قدر\s+[أاإ]ساعدك\s+(?:في\s+)?[أاإ]?(?:يه|زاي)\s*[؟?]"),
    # "لو في حاجة تانية ممكن أساعدك فيها دلوقتي، تحت أمرك." — the same banned filler with the
    # verb moved, so none of the patterns above reach it. It survived a handoff reply in CS1.
    re.compile(
        _LEAD + r"(?:في\s+)?حاج[ةه]\s+تاني[ةه]\s+ممكن\s+[أاإ]?ساعدك[^.؟?]*[.؟?]?"
    ),
)


# Connectors whose only job is to introduce the clause that was just removed. Trimmed only
# when a strip actually happened, and only mid-text (never the whole reply), so a reply that
# legitimately ends on one is left alone. "بس" and "كمان" are deliberately absent: both are
# ordinary sentence-final words in Egyptian ("دي الأسعار بس", "وفيه 50 ملي كمان").
_DANGLING_TAIL = re.compile(r"\s+(?:ل|لو|ولو|و|أو|او|لكن|يعني|هل|وهل)\s*[.،,؟?!]*\s*$")


def _trim_dangling_connector(text):
    """Drop a connector left stranded at the end by a stripped question."""
    return _DANGLING_TAIL.sub("", text).rstrip()


# ── False over-budget claims ──────────────────────────────────────────────────────────────
#
# Conversation 931, budget 1200: "الـ90 ملي بـ1019 جنيه ⚠️ يعني أغلى من ميزانيتك بـ353 جنيه".
# 1019 is inside 1200 and its own price line said "✅ (داخل الميزانية)". 353 is 1019 − 666 — the
# gap between the *two sizes*, printed one line below the budget labels by value.size_value_note
# as "أغلى بـ 353 جنيه في الإجمالي (1019 مقابل 666)". The model took a real number, re-pointed its
# referent from the 50ml to the budget, and lifted the ⚠️ along with it.
#
# Conversation 912 was the same failure (1046 against 1200) and was answered prompt-side, in
# recommendation.budget_note, on the premise that "a ✅ line has no figure to quote, so the
# request is unfillable rather than forbidden". That premise was false: size_value_note supplies
# a quotable figure, in the same block, under the same word. The prohibition did not hold.
#
# So the rule lives here instead, as arithmetic on the finished reply. Two properties the prompt
# layer cannot have: it is verifiable, and it runs before the reply is persisted — which is the
# only place the *repeat* can be caught. In 931 the false claim was saved to Message.content,
# flowed back through build_llm_history, and came out again on a turn with no injected product
# context at all. There is nothing prompt-side on that turn to fix.
#
# What this deliberately does not do, so the next reader does not read a miss as a bug:
#
#   * A price-like number that is not a price silences the guard for that turn — "Baccarat Rouge
#     540" reads as 540, and a 540 above the budget trips the max-price bail. A miss, which leaves
#     the reply as the model wrote it; the alternative is stripping true warnings.
#   * A claim whose own sentence quotes no price is left alone, even when the rest of the reply is
#     full of in-budget prices. That is `_has_a_price_in_scope`, and it is the correction four
#     archived eval runs forced on this module: "Dior Sauvage خرج من طلبك لأنه سعر الـ90 ملي أعلى
#     من ميزانيتك بكتير" is TRUE at budget 700, and the guard was deleting the only clause that
#     explained why the perfume had been ruled out.
#   * Reverse word order ("بـ353 جنيه أعلى من ميزانيتك") strips the claim and leaves the number.
#     Consuming a number *before* the claim is how a real price gets deleted, so it is not done.
#     `_CLAIM_SUBJECT` handles the one case a replay actually produced, where a connector marks
#     the fragment off unambiguously; without a connector the reply keeps its own opening words.
#   * A reply asserting an *unstated* cart total is safe on two counts now. By construction, its
#     sentence has no figure for the scope test to find; and in practice
#     `order_service._over_budget_warning` only ever prints a figure `budget_tier` has called
#     "far", so every legitimate warning carries a number above the budget and bails at step 3.
#     Pinned by a test that calls that function rather than quoting it, so the two cannot drift.

# Both spellings, both final letters. أغلى (with غ) is the word conversation 931 actually used;
# a set carrying only أعلى (with ع) — as eval_harness.checks did — matches none of that
# transcript, which is why the harness scored the turn without ever examining it.
_HIGHER = r"[أاإ][عغ]ل[ىي]"

# The optional connector or marker introducing the clause. ⚠️ is here because the model lifts it
# out of the over-budget label and puts it in front of the claim. لو and هل are spelled out ahead
# of the bare و for the reason _LEAD documents above: و is a word character, so a bare و happily
# matches the second letter of لو and leaves a reply carrying an orphaned ل.
_CONNECTOR = (
    r"(?:\s*[،,]?\s*"
    r"(?:⚠️?|(?:و\s*)?لو\b|(?:و\s*)?هل\b|يعني|بس|لكن|و|ف)"
    r"\s*)"
)

# The claim's own subject, when the model puts one between the connector and the claim:
# "…، يعني الـ90 أعلى من ميزانيتك". The connector run stops dead at الـ90 — a size reference is
# not a connector — so the match began after it and the strip left "…، يعني الـ90." behind: a
# sentence whose predicate had been deleted out from under it. Conversation 933 turn 11, in the
# first replay run of the fix itself.
#
# Two digits at most, deliberately. Volumes are 30-90 and prices start at three, and PRICE_FLOOR
# is 100 for the same reason — so this cannot reach a price even though "الـ666" is the same
# shape. A missed tidy leaves a fragment; a consumed price deletes a fact, which is the trade the
# module docstring already makes when it declines to eat a *leading* number.
_CLAIM_SUBJECT = r"(?:الـ?\s*)?\d{1,2}(?:\s*(?:ملي|مل|ml))?|ده|دي|دول|هو|هي|هما|الاتنين"

# A subject is only consumed when a connector introduced it, so a reply *opening* on the claim
# keeps its own first words and the emptiness guard has something to protect.
_CLAIM_LEAD = (
    r"(?:" + _CONNECTOR + r"+(?:" + _CLAIM_SUBJECT + r")\s*"
    r"|" + _CONNECTOR + r"*)"
)

_INTENSIFIER = r"(?:\s*(?:شوية|بشوية|كتير|بكتير|جدا[ًا]?))?"

# The overage figure, when the model invents one: "بـ353 جنيه", "بحوالي 353". Consumed with the
# claim so no orphaned number is left behind.
#
# The intensifier is repeated here because it lands on either side of the budget word, and only
# one side was covered at first. Conversation 931's second reply put it *after*:
# "أغلى من ميزانيتك شوية بـ353 جنيه" — the claim came out and "شوية بـ353 جنيه" stayed, which is
# worse than the original sentence.
_OVERAGE_TAIL = (
    _INTENSIFIER + r"(?:\s*ب(?:ـ)?\s*(?:حوالي\s*)?\d[\d.,]*\s*(?:جنيه)?)?"
)

# The model also puts the ⚠️ *inside* the clause — conversation 912's wording was
# "أعلى شوية ⚠️ عن ميزانيتك" — so every core tolerates one before من/عن.
_MID = r"(?:\s*⚠️?)?\s*"
# Every possessive form — ميزانيتك، ميزانية، ميزانيتها — but stopping short of punctuation. A
# plain \S* here reaches past the noun and eats the full stop that ends the sentence, so
# conversation 912's "عن ميزانيتك." came out as a reply with no terminator.
_BUDGET = r"(?:ال)?ميزاني[^\s،,.؟!?]*"


def _claim(core):
    """Compile one claim pattern, with the core marked off from the lead that precedes it.

    The `core` group exists so `_is_negated` can ask its question at the right position. The lead
    deliberately swallows connectors and the comma before them, which puts `match.start()` on the
    far side of a clause break — so anchoring the negation scan there read the previous clause and
    let "مش بطال، بس أغلى من ميزانيتك" pass as a retraction. The lead is glue to be removed; the
    core is the claim being made, and the claim is what a negator does or does not deny.
    """
    return re.compile(_CLAIM_LEAD + "(?P<core>" + core + ")" + _OVERAGE_TAIL)


# (pattern, negatable). `negatable` is False for the two cores that open with مش, whose مش is
# part of the claim rather than a negation of it.
_OVER_BUDGET_CLAIMS = (
    (_claim(_HIGHER + _INTENSIFIER + _MID + r"(?:من|عن)\s+" + _BUDGET), True),
    (_claim(r"[أاإ]كتر" + _INTENSIFIER + _MID + r"(?:من|عن)\s+" + _BUDGET), True),
    (_claim(r"فوق" + _MID + _BUDGET), True),
    (_claim(r"(?:خارج|بر[هة])" + _MID + _BUDGET), True),
    (_claim(r"(?:زياد[هة]|بيزيد|زايد)" + _INTENSIFIER + _MID + r"(?:عن|على)\s+" + _BUDGET), True),
    (_claim(r"الفرق\s+\d[\d.,]*\s*(?:جنيه)?\s*(?:عن|من)\s+" + _BUDGET), True),
    (_claim(r"مش\s+داخل" + _MID + _BUDGET), False),
    (_claim(r"مش\s+في" + _MID + _BUDGET), False),
)

# A claim preceded by one of these is a *correction*, not a claim — and the correction is the
# reply this guard exists to produce. Conversation 931's customer objected twice; the right
# answer is "هو مش أعلى من ميزانيتك", which every pattern above matches inside.
_NEGATORS = frozenset((
    "مش", "مِش", "ولا", "لا", "ماهو", "ماهوش", "مكانش", "مبقاش",
    # Quantified denials, which is how a retraction covering *both* sizes is actually phrased:
    # "ولا واحد منهم أعلى من ميزانيتك", "مفيش حاجة فيهم فوق الميزانية".
    "مفيش", "مافيش", "ماحدش", "محدش",
))
_WHITESPACE = re.compile(r"\s+")
_CLAUSE_BREAK = re.compile(r"[،,.؟!?]")

_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# The label on its own, with no sentence attached: "الـ90 ملي بـ1019 جنيه ⚠️، والـ50 ملي بـ666
# جنيه داخل الميزانية." — replay run 1, turn 10, budget 1200, both sizes inside it.
#
# Stripping the *claim* does not reach this, because there is no claim: the model lifted the
# marker and left the sentence out. That is not a lesser version of the bug. ⚠️ has exactly one
# meaning in this system — `product_formatting._BUDGET_LABELS["near"]`, "أعلى شوية من الميزانية" —
# and four prompt rules bind it to that meaning by name, so a glyph beside an in-budget price
# tells the customer the same falsehood the sentence did. Turn 11 of that run is the customer
# asking "ازاي اعلي من ميزانيتي" with nothing but the glyph to have prompted it.
#
# Price-adjacent, because that is how the label is built: `_BUDGET_LABELS` renders it as a suffix
# to a price and nowhere else. Every other ⚠️ this system emits either lives only in the injected
# context or *leads* its line — "⚠️ للعلم: إجمالي الطلب…", "⚠️ العطور اللي تحت دي" — and requiring a
# number in front is what keeps a relay of one of those intact. The comma the marker leans on is
# left alone; `_tidy_after_strip` closes the gap.
_BUDGET_GLYPH = re.compile(r"(?<=[\d٠-٩])(\s*(?:جنيه|جني[هة]|ج\.?م)?)\s*⚠️?")

# Below this a number in a perfume reply is not a price: volumes (50, 90), percentages,
# quantities, list markers. Counting those would make "the reply quotes a price" true of every
# reply and drain the precondition in strip_false_over_budget of all its meaning.
PRICE_FLOOR = Decimal("100")

# Where a claim's referent stops being findable. A comma does not end it — conversation 912 said
# "سعره 1046 جنيه، أعلى شوية ⚠️ عن ميزانيتك" and the 1046 across that comma is exactly what makes
# the claim false — but a full stop or a line break does.
_SENTENCE_BREAK = re.compile(r"[.؟!?\n\r]")


def _is_negated(text, start):
    """Whether the claim at `start` is preceded by a negator, i.e. is a retraction.

    Token comparison rather than a lookbehind: لا is a substring of ولا and خلاص, so a character
    lookbehind fires on words carrying no negation at all.

    How far back to look is bounded twice, and the two bounds pull opposite ways. The scan stops
    at the previous clause break, because a negator in the clause before does not deny this one —
    "الـ90 بـ1019 جنيه مش بطال، بس أغلى من ميزانيتك" is the claim, not a retraction, and reading it
    as one would leave the falsehood in the reply. Within the clause it reads three tokens, no
    more, because a whole-clause scan lets any مش in a long sentence do the same thing.

    Three, not two, because two is a bug this cost: "ولا واحد منهم أعلى من ميزانيتك" — the natural
    way to retract about *both* sizes at once — puts the negator three tokens out, so a two-token
    window read a correct retraction as a claim and stripped its own subject, leaving
    "ولا واحد منهم، 666 و1019". Found by the eval harness's copy of this check, which is the
    argument for the harness having its own copy.
    """
    before = text[:start]
    breaks = [match.end() for match in _CLAUSE_BREAK.finditer(before)]
    clause = before[breaks[-1]:] if breaks else before
    tokens = _WHITESPACE.split(clause.strip())[-3:]
    return any(token.strip("،,.!؟?ـ()") in _NEGATORS for token in tokens)


def _prices_in(text):
    """Every number in the text big enough to be a price, as Decimals."""
    values = []
    for raw in _NUMBER.findall(text.translate(_ARABIC_INDIC)):
        try:
            value = Decimal(raw.replace(",", ""))
        except ArithmeticError:
            continue
        if value >= PRICE_FLOOR:
            values.append(value)
    return values


def _has_a_price_in_scope(text, start):
    """Whether a price the claim at `start` could be *about* is actually in the reply.

    The reply-wide test this replaces asked the wrong question. "does this reply quote a price
    that is inside the budget" is true of almost every priced reply, including ones whose
    over-budget claim is about something they deliberately did not price:

        Dior Sauvage خرج من طلبك لأنه سعر الـ90 ملي أعلى من ميزانيتك بكتير. عندنا بدائل داخل
        الميزانية زي Ambero والـ50 ملي بـ601 جنيه، وDark Aura والـ50 ملي بـ680 جنيه.

    Budget 700. That sentence is TRUE — Sauvage's 90ml really is over, and the prompt forbids
    pricing an ❌ size, which is why no figure appears next to it — while 601 and 680 belong to
    two different perfumes named afterwards. A reply-wide max of 680 ≤ 700 made the guard strip
    the one clause that explained the exclusion, leaving "خرج من طلبك لأنه سعر الـ90 ملي." Found
    by re-grading four archived eval runs after the check was widened, on scenarios M1 and G2 —
    two turns that had been graded clean for months by a harness that could not see this shape.

    So the question is per-claim: is there a price near enough to be its referent? Scan back to
    the previous sentence break, not clause break — conversation 912's "سعره 1046 جنيه، أعلى شوية
    ⚠️ عن ميزانيتك" puts the price one comma away and is the earlier report of this same bug, so a
    clause bound would lose it. A full stop or a newline does end the scope, which is what keeps
    G2's trailing "لو حابب حجم أكبر، الـ90 ملي أغلى بكتير عن ميزانيتك" — its own line, no price on
    it — out of reach of four bullet prices above.

    Forward as well as back, for word order: "أعلى من ميزانيتك بـ353 جنيه" states the figure after
    the claim, and conversation 931's msg 20 is that shape. Same bound.

    Erring towards *not* stripping. A claim left standing is a possible falsehood; a claim removed
    from a reply that was telling the truth is a certain loss of the reason a perfume was ruled
    out, and the customer is left with a sentence that stops mid-thought.
    """
    breaks = [match.end() for match in _SENTENCE_BREAK.finditer(text[:start])]
    scope_start = breaks[-1] if breaks else 0
    ahead = _SENTENCE_BREAK.search(text, start)
    scope_end = ahead.start() if ahead else len(text)
    return bool(_prices_in(text[scope_start:scope_end]))


def _tidy_after_strip(text):
    """Repair the punctuation a deleted mid-sentence clause leaves behind."""
    text = re.sub(r"\s*،\s*(?=[،.؟!?])", "", text)
    text = re.sub(r"\s+([،.؟!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    # A clause removed from the end of a sentence leaves the comma that introduced it, and no
    # Arabic sentence ends on one. _DANGLING_TAIL cannot see this: it requires a connector word
    # in front of the punctuation, and here the connector went out with the clause.
    text = re.sub(r"\s*،\s*$", ".", text.strip())
    return _trim_dangling_connector(text.strip())


def strip_false_over_budget(reply, budget):
    """Remove an over-budget claim that the reply's own numbers disprove.

    Returns `(cleaned, removed)`. Pure and Django-free — `budget` is a plain number — so the
    rule is testable without a Conversation row.

    The rule, in order:

    1. No usable budget: nothing to check against.
    2. No price-like number in the reply: no evidence about what the claim refers to, so it
       stands. Not caution for its own sake — "الـ90 ملي أعلى شوية من ميزانيتك" with the price
       left out is a shape the code really produces (budget_label's docstring records it) and
       it can be perfectly true.
    3. Some quoted price is genuinely above the budget: the claim may be about that price, so
       it stands.
    4. Per claim, no price within its own sentence: same reasoning as step 2, asked where it
       actually belongs. A reply can price four alternatives and make a true claim about a fifth
       thing it deliberately left unpriced — see `_has_a_price_in_scope`, which is the correction
       four archived eval runs forced on this function.
    5. What is left is a claim sitting beside a price that is inside the budget, and it cannot be
       true about it. It goes — and so does a price-adjacent ⚠️, which says the same thing without
       a sentence to hang it on. See `_BUDGET_GLYPH`.

    One-directional by construction: a true statement about a price the customer can see must
    name a number above the budget, so step 3 always catches it before step 5 can fire.
    """
    if not reply:
        return reply, []

    try:
        limit = Decimal(str(budget))
    except (ArithmeticError, TypeError, ValueError):
        return reply, []
    if limit <= 0:
        return reply, []

    prices = _prices_in(reply)
    if not prices or max(prices) > limit:
        return reply, []

    cleaned = reply
    removed = []
    for pattern, negatable in _OVER_BUDGET_CLAIMS:
        # The span removed is the whole match, lead included — that is what leaves clean prose.
        # The negation and scope questions are both asked at the core: the lead can reach back
        # across the comma that carries the price, and asking from there would find a referent in
        # the previous sentence. `_claim` documents the same reasoning for negation.
        spans = [
            match.span() for match in pattern.finditer(cleaned)
            if not (negatable and _is_negated(cleaned, match.start("core")))
            and _has_a_price_in_scope(cleaned, match.start("core"))
        ]
        removed.extend(cleaned[start:end].strip() for start, end in spans)
        for start, end in reversed(spans):
            cleaned = cleaned[:start] + cleaned[end:]

    # After the claims, so a glyph the lead already swallowed is not counted twice, and
    # independently of whether any claim matched at all — the bare marker is its own leak. The
    # captured group is the currency word the marker sat behind, put back so only the glyph goes.
    cleaned, glyphs = _BUDGET_GLYPH.subn(r"\1", cleaned)
    removed.extend(["⚠️"] * glyphs)

    if not removed:
        return reply, []

    cleaned = _tidy_after_strip(cleaned)
    if not cleaned:
        # Same invariant as sanitize_reply: an empty message is worse than a wrong one. The
        # price floor above makes a reply that is *nothing* but the claim close to unreachable,
        # so this is a guard rather than a path.
        return reply, []

    return cleaned, removed


def sanitize_reply(reply, conversation=None):
    """Remove forbidden filler questions, and any provably false over-budget claim.

    Returns the cleaned text. If a reply is nothing but a banned question, the
    original is kept — sending an empty message is worse than sending a weak one.

    The budget pass runs first: it is a factual correction rather than a matter of register,
    and removing a mid-sentence clause can leave a connector for the closer pass to tidy.
    """
    if not reply:
        return reply

    cleaned = reply

    # Read straight off the conversation rather than importing the model, so this module stays
    # Django-free. merge_preferences (conversation_service) writes max_price *inside* route(),
    # on the same instance later handed here, so the budget is available on the very turn the
    # customer states it. order_service._over_budget_warning reads the identical place.
    try:
        budget = (getattr(conversation, "preferences", None) or {}).get("max_price")
    except (AttributeError, TypeError):
        budget = None

    cleaned, false_claims = strip_false_over_budget(cleaned, budget)
    if false_claims:
        logger.warning(
            "FALSE_OVER_BUDGET: stripped %d over-budget claim(s) or marker(s) the reply's own "
            "numbers disprove%s. Budget %s, highest price quoted %s. Removed: %s. Every one of "
            "those prices was labelled in-budget in the context, so this figure was generated, "
            "not computed — a steady rate here means the prompt-side wording still invites it.",
            len(false_claims),
            f" (conversation #{conversation.id})" if conversation is not None else "",
            budget,
            max(_prices_in(reply), default="none"),
            " | ".join(false_claims),
        )

    removed = []
    for pattern in BANNED_CLOSERS:
        cleaned, count = pattern.subn("", cleaned)
        if count:
            removed.append(pattern.pattern)

    cleaned = cleaned.strip()

    if removed or false_claims:
        cleaned = _trim_dangling_connector(cleaned)

    if not cleaned:
        return reply

    if removed:
        logger.info(
            "Stripped %d banned closer(s) from reply%s. The prompt already forbids "
            "these; frequent hits mean the persona rules are being dropped.",
            len(removed),
            f" (conversation #{conversation.id})" if conversation is not None else "",
        )

    return cleaned


# Closing questions — asking for the order. Legitimate at the right moment and premature
# everywhere else, so unlike BANNED_CLOSERS these are NOT stripped by sanitize_reply.
# strip_premature_closing is called per-branch, only where the sales stage says the
# customer has not earned a close yet: still comparing, still objecting, still trying to
# remember a name. sanitize_reply must stay byte-identical for a legitimate close —
# "الـ 90 ملي أوفر بكتير. أجيبلك الـ 90 ولا الـ 50؟" is pinned as passing through untouched.
#
# Split into two tiers because the gate was mechanically one-sided. Every pattern here is an
# *online* closer; none of them matches "تنورنا في الستور تشم وتجرب؟". So at six of the eight
# stages the only CTA that could physically survive to the customer was a walk-in invite —
# backwards for a business that sells online, and most of why the evaluation's
# sales_effectiveness sat at 6.9 with "no concrete next step" as its commonest complaint.
#
# Every `تحب` also accepts `تحبي` / `تحبى`. The masculine-only form let R3's
# "تحبي أجهزلك واحدة؟" through all three layers at once — production, checks.py and
# rescore.py — which for a store whose customers are largely women is half the conversations.
#
# HARD asks for the order. SOFT narrows toward it. A size choice right after a recommendation
# is not a close; it is how the sale moves, and the persona already ships it as a recommended
# CTA (prompts.py, the CTA menu).
_HARD_CLOSERS = (
    # "تحب أساعدك في الطلب؟" / "تحب اساعدك في طلب واحد فيهم؟"
    # `[^؟?]{0,30}` rather than `\S*\s*` because every one of these patterns used to
    # require the question mark to sit immediately after the order word. Real replies put
    # words in between — "تحب أساعدك في طلب واحد فيهم؟", "تحب تطلبه تاني؟" — and every
    # one of them walked straight through.
    re.compile(_LEAD + r"تحب[يى]?\s+[أاإ]?ساعدك\s+في\s+(?:ال)?(?:طلب|اوردر|أوردر)[^؟?]{0,30}[؟?]"),
    # The same close without the "في": "تحب أساعدك تطلب واحد فيهم؟".
    re.compile(_LEAD + r"تحب[يى]?\s+[أاإ]?ساعدك\s+[نت]طلب[^؟?]{0,30}[؟?]"),
    # "تحب أجهزلك واحد منهم؟" — a close carrying no order word at all.
    re.compile(_LEAD + r"تحب[يى]?\s+[أاإ]?جهز\s*ل?ك[^؟?]{0,40}[؟?]"),
    # "تحب تطلب؟" / "تحب تطلبه تاني؟" / "تحب نطلبه؟"
    re.compile(_LEAD + r"تحب[يى]?\s+[نت]طلب[^؟?]{0,30}[؟?]"),
    # "نسجل الطلب؟" / "نكمل الاوردر؟" / "تحب نكمل الطلب؟"
    # The optional "تحب" matters for ordering: without it this pattern matched only
    # "نكمل الطلب؟" out of "تحب نكمل الطلب؟" and left the verb stranded, because it is
    # tried before the "تحب نكمل" pattern below and consumed the tail first.
    re.compile(_LEAD + r"(?:تحب[يى]?\s+)?ن(?:سجل|كمل)\s+(?:ال)?(?:طلب|اوردر|أوردر)[^؟?]{0,20}[؟?]"),
    # "تحب نكمل؟" — the same close with no order word for the pattern above to anchor on.
    re.compile(_LEAD + r"تحب[يى]?\s+نكمل[^؟?]{0,25}[؟?]"),
    # The statement form, which carries no question mark and so escaped every pattern above:
    # "لو تحب أساعدك في الطلب أو تحب تجرب العطور في الستور تحت أمرك." went out at stage
    # 'discovery'. BANNED_CLOSERS already carries a statement variant for the same reason;
    # this family needed one too.
    re.compile(
        _LEAD + r"تحب[يى]?\s+[أاإ]?ساعدك\s+في\s+(?:ال)?(?:طلب|اوردر|أوردر)[^.؟?]*[.؟?]?"
    ),
    # And the same for أجهزلك. Once the persona started leading with an online close the model
    # phrased it as a statement — "لو تحب أجهزلك الطلب وأبعتلك التفاصيل." — which the
    # question-mark form above cannot see. It leaked at stage 'recommendation' in F2 and R3.
    re.compile(
        _LEAD + r"تحب[يى]?\s+[أاإ]?جهز\s*ل?ك[^.؟?]*[.؟?]?"
    ),
)

_SOFT_CLOSERS = (
    # "أجيبلك الـ90 ولا الـ50؟" — a size choice. Narrowing, not closing: it commits the
    # customer to nothing and is the next step a seller actually takes after recommending.
    re.compile(_LEAD + r"[أا]جيبلك\s+(?:الـ?\s*)?\d+\s*(?:ملي)?\s*ولا\s+(?:الـ?\s*)?\d+\s*(?:ملي)?\s*[؟?]"),
)

# Kept as the union so anything reasoning about "the closers" still sees all of them.
PREMATURE_CLOSERS = _HARD_CLOSERS + _SOFT_CLOSERS


def strip_premature_closing(reply, stage=None, allow_soft=False):
    """Remove an order-closing question the current stage has not earned.

    Enforced here rather than asked for in the persona because the persona already asks:
    it says to close only when the customer is clearly buying, and the bot closed three
    replies in a row anyway. A stage that permits closing leaves the reply untouched.

    `allow_soft` keeps a *narrowing* next step — a size choice — while still removing the
    hard asks. The caller decides, via `sales.stage.soft_closing_allowed`, so this module
    stays free of any dependency on the stage table. It defaults to False so every existing
    caller and test keeps the old all-or-nothing behaviour.

    As with sanitize_reply, a reply that is *nothing but* a closing question is kept —
    sending an empty message is worse than sending a premature one.
    """
    if not reply:
        return reply

    patterns = _HARD_CLOSERS if allow_soft else PREMATURE_CLOSERS

    cleaned = reply
    removed = 0
    for pattern in patterns:
        cleaned, count = pattern.subn("", cleaned)
        removed += count

    cleaned = cleaned.strip()
    if removed:
        cleaned = _trim_dangling_connector(cleaned)
    if not cleaned:
        return reply

    if removed:
        logger.info(
            "Stripped %d premature closing question(s) at stage %s.", removed, stage
        )

    return cleaned


# Marketing filler. Replacements rather than deletions: cutting a phrase out of the middle
# of an Arabic sentence leaves it ungrammatical, which is worse than the filler. The
# intensifier goes and the adjective stays.
FLUFF_PATTERNS = (
    (re.compile(r"جذابة\s+جدا[ًا]?"), "جذابة"),
    (re.compile(r"جذاب\s+جدا[ًا]?"), "جذاب"),
    (re.compile(r"فخمة\s+جدا[ًا]?"), "فخمة"),
    (re.compile(r"فخم\s+جدا[ًا]?"), "فخم"),
    (re.compile(r"رائعة\s+جدا[ًا]?"), "حلوة"),
    (re.compile(r"تركيبة\s+رائعة"), "تركيبة حلوة"),
    (re.compile(r"لمسة\s+عصرية\s+جذابة"), "ريحة عصرية"),
    (re.compile(r"لمسة\s+عصرية"), "ريحة عصرية"),
    (re.compile(r"عبق\S*\s+"), ""),
)

# Quantified certainty the product data cannot support. "مضمون" alone is left alone: the
# persona's own trade-secrets line says "أضمنلك إن جودتها هتعجبك", and gagging that would
# break a pinned rule. What goes is the manufactured precision and the absolute guarantee.
UNSUPPORTED_CLAIMS = (
    (re.compile(r"مضمون(?:ين|ة)?\s*(?:100|١٠٠)\s*%"), "اختيار آمن"),
    (re.compile(r"(?:100|١٠٠)\s*%\s*مضمون(?:ين|ة)?"), "اختيار آمن"),
    (re.compile(r"مطابق\S*\s+للأصل\s+بنسبة\s+\d+\s*%"), "قريب من الأصل"),
    (re.compile(r"(?:شبه|تشابه|مشابه)\S*\s+(?:ب)?نسبة\s+\d+\s*%"), "قريب منه"),
    (re.compile(r"بنسبة\s+\d+\s*%\s+من\s+الأصل"), "قريب من الأصل"),
    (re.compile(r"الاتنين\s+مضمونين"), "الاتنين اختيار آمن"),
    (re.compile(r"مضمون(?:ة)?\s+(?:هتعجب|إنها\s+هتعجب)\S*"), "أغلب الناس بتحبها"),
)

# One emoji is punctuation; six is a brochure. Structural markers are protected: 🔹 opens
# each recommendation line, 💰 marks the order total that _summary_was_shown greps for, and
# the ✅/❌/⚠️/💡/⭐ set carries meaning the prompts depend on. Stripping any of those would
# damage the reply rather than tidy it.
PROTECTED_EMOJI = frozenset("🔹💰💡✅❌⚠⭐️")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF]"
)
MAX_EMOJI = 3


def _cap_emoji(reply):
    """Drop decorative emoji past the cap, keeping the earliest and all structural ones."""
    decorative = [
        match for match in _EMOJI.finditer(reply)
        if match.group() not in PROTECTED_EMOJI
    ]
    if len(decorative) <= MAX_EMOJI:
        return reply

    result = reply
    for match in reversed(decorative[MAX_EMOJI:]):
        result = result[: match.start()] + result[match.end() :]
    return result


def soften_marketing_language(reply):
    """Replace brochure phrasing and unsupported precision with plain seller Arabic.

    These are in code rather than the persona for the same reason as BANNED_CLOSERS: the
    persona's own approved-words list recommended "جذابة جداً", which is exactly the
    register the evaluation flagged. The list is fixed there; this catches what the model
    produces anyway.
    """
    if not reply:
        return reply

    cleaned = reply
    for pattern, replacement in UNSUPPORTED_CLAIMS:
        cleaned = pattern.sub(replacement, cleaned)
    for pattern, replacement in FLUFF_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)

    cleaned = _cap_emoji(cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = cleaned.strip()

    return cleaned or reply
