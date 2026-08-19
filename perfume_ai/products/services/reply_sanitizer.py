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

logger = logging.getLogger(__name__)


# Each pattern matches a whole trailing question, including the whitespace before it,
# so removing it leaves the preceding sentence intact.
BANNED_CLOSERS = (
    # "تحب تعرف الأسعار والأحجام المتاحة؟" / "تحب تعرف أسعارهم والأحجام؟"
    re.compile(
        r"\s*(?:و\s*)?تحب\s+تعرف\s+(?:ال)?[أاإ]?سعار\S*"
        r"(?:\s*و\s*(?:ال)?[أاإ]?حجام\S*)?(?:\s+المتاحة)?\s*[؟?]"
    ),
    # "عايز حاجة تانية؟" / "محتاج مساعدة في حاجة؟" / "محتاج حاجة تانية؟"
    re.compile(r"\s*(?:عايز|محتاج|تحب)\s+(?:حاجة\s+تانية|مساعدة(?:\s+في\s+حاجة)?)\s*[؟?]"),
    # "عطر معين في بالك؟"
    re.compile(r"\s*(?:فيه\s+)?عطر\s+معين\s+في\s+بالك\s*[؟?]"),
)


def sanitize_reply(reply, conversation=None):
    """Remove forbidden filler questions from a generated reply.

    Returns the cleaned text. If a reply is nothing but a banned question, the
    original is kept — sending an empty message is worse than sending a weak one.
    """
    if not reply:
        return reply

    cleaned = reply
    removed = []
    for pattern in BANNED_CLOSERS:
        cleaned, count = pattern.subn("", cleaned)
        if count:
            removed.append(pattern.pattern)

    cleaned = cleaned.strip()

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
