"""Recognise a customer praising a perfume, so the reply can say thank you.

Conversation 1105 is two compliments in a row, each answered "ممكن تكتب لي اسم العطر تاني بشكل
أوضح؟". The reason was in `naming` and is fixed there — praise was surviving `identifying_tokens`,
so `may_name_a_perfume` read "البرفان تحفه وثباته ممتاز" as an unreadable name. That fix alone stops
the interrogation, but it leaves the turn answered blandly: nothing in the pipeline knows a
compliment is a compliment, so nothing thanks anyone for one.

Keyword-based rather than a fourteenth classifier intent, for the three reasons
`sales.objection`'s docstring gives and which apply here unchanged: no LLM call on a path already
spending two or three per turn, directly unit-testable where classifier behaviour is not, and a miss
falls back to today's behaviour rather than a worse one.

🔴 **Advisory, not a route.** Unlike `objection`, which outranks classification and takes the turn,
this only ever *adds an instruction* to whichever branch the classifier already chose. Praise is
rarely the only thing in a message — "العطر تحفه عندكم منه حجم أكبر" is a compliment and a size
question — and a branch that swallowed the turn would drop the half that needs answering. It would
also bypass `ai.classifier`'s rule that a named past purchase must load real product data, which is
the guard against describing a perfume from memory.
"""

from dataclasses import dataclass

from . import naming, objection
from ..static_faq_service import normalize_arabic

# Thanks is not praise. "شكرا" is overwhelmingly a farewell in this corpus — "لا شكرا",
# "تمام شكرا مش عايز خلاص" — and `router._is_goodbye_loop` already owns that turn. It belongs in
# `naming._PRAISE` so it stops counting as a perfume name, but on its own it must not make the bot
# gush at someone who is politely declining. Only thanks *alongside* real praise counts.
_THANKS_ONLY = frozenset({"شكرا", "شكرن", "متشكر", "متشكره"})

# Compliments that no single token carries, so `naming.praise_tokens` cannot see them.
_PHRASES = (
    "تسلم ايدك", "تسلم ايديك", "ربنا يكرمك", "من احسن العطور", "احسن عطر",
    "مبسوط بيه", "مبسوط بالعطر", "الناس بتسال عليه", "كلهم سالوني",
    "هاجي تاني", "هطلب تاني", "هكرر", "بشكركم", "شغلكم حلو", "تعاملكم حلو",
)

# 🔴 Negated praise is a complaint, and thanking someone for one is the worst possible reply.
# "مش حلو", "مش عجبني", "مكانش تحفه" all contain a praise word and all mean the opposite. Matched as
# the word immediately before the praise token rather than anywhere in the message, so
# "مش عارف اختار بس العطر تحفه" still reads as praise.
_NEGATORS = frozenset({"مش", "مو", "ما", "مكانش", "مكنش", "مبقاش", "ولا", "لا", "غير"})


@dataclass(frozen=True)
class Appreciation:
    matched: tuple = ()
    # True when the customer also named a perfume ("اشتريت منكم امبيرو وعجبني"). The reply still
    # thanks them either way; the caller needs this only to know whether real product data is in
    # play, because the thanks has to open the reply instead of a data dump.
    names_a_perfume: bool = False


def _normalize(text):
    """normalize_arabic plus tatweel removal, exactly as `objection._normalize` does it."""
    return normalize_arabic(text).replace("ـ", "")


def _unnegated_praise(message):
    """Praise tokens that are not immediately preceded by a negator."""
    words = _normalize(message).split()
    praise = naming.praise_tokens(message)
    return {
        word
        for index, word in enumerate(words)
        if word in praise and not (index and words[index - 1] in _NEGATORS)
    }


def detect(message, history=None):
    """The compliment in this message, or None.

    Only the customer's own words are examined. History is accepted so callers do not have to
    special-case it, and because scanning bot replies would match the bot's own warm language back
    at itself.
    """
    if not message:
        return None

    normalized = _normalize(message)
    if not normalized:
        return None

    # A complaint outranks a compliment. "جبته من عندكم بس مش ثابت" carries a past purchase and a
    # doubt; `objection` owns that turn and its playbook opens by resolving, not by thanking.
    if objection.detect(message, history=history):
        return None

    hits = tuple(sorted(_unnegated_praise(message) - _THANKS_ONLY))
    hits += tuple(phrase for phrase in _PHRASES if phrase in normalized)
    if not hits:
        return None

    return Appreciation(
        matched=hits,
        names_a_perfume=bool(naming.identifying_tokens(message)),
    )


# The move, decided here so the model cannot default to selling. A prompt fragment rather than a
# reply: the model still writes the Arabic.
#
# 🔴 Thanks and nothing else — no question, not even "أنهي عطر؟". That is the store owner's explicit
# call: asking which perfume it was reads as working the customer for a lead when they were only
# being nice. We accept never learning which perfume they meant.
RULES = """
🎁 العميل بيمدح العطر أو بيشكرك:
- ابدأ الرد بشكر دافئ وشخصي على كلامه الحلو. ده أهم جزء في الرد.
- ❌🔴 ممنوع تسأله أنهي عطر ده أو تطلب منه يكتب اسم العطر. هو مش بيسأل عن حاجة، هو بيمدح.
- ❌🔴 ممنوع تعرض عليه أي عطر تاني، وممنوع ترشح، وممنوع تسأله يشتري تاني.
- ❌🔴 ممنوع تقول إنك مش فاهم قصده أو تطلب توضيح.
- خلي الرد قصير وطبيعي، زي ما صاحب المحل يرد على زبون بيمدح بضاعته.
- ✅ لو سأل سؤال تاني في نفس الرسالة، اشكره الأول بجملة قصيرة وبعدين جاوب على سؤاله عادي.
"""
