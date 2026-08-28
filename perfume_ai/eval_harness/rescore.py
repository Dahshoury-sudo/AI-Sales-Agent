# -*- coding: utf-8 -*-
"""Second-pass deterministic scoring over an existing run.

Runs offline against results/runs.json — no model calls. Exists because the first
pass produced false positives that would have inflated the bug count, and an
evaluation that overstates failures is as useless as one that hides them:

  * "ميزانيتك" in "الـ90 أغلى من ميزانيتك" is a statement, not a re-ask.
  * "مش مضمون" is the agent *refusing* to guarantee — the opposite of the defect.
  * A phone number the customer typed two turns ago is not an invented number.
  * Naming an out-of-stock perfume to say it is unavailable is correct behaviour.

It also adds checks the first pass lacked: cross-turn repetition, and leakage of the
banned-closer family through morphological variants the production regex misses.
"""

import json
import os
import re
import sys
from difflib import SequenceMatcher

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from eval_harness import checks  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
# The agent refusing to guarantee is the opposite of the defect, so the negated forms have to
# be recognised. `أقدر` was missing beside `هقدر`, so "بس مش أقدر أضمن" — a correct refusal, and
# exactly what the gift scenario asks for — was scored a high unsupported_guarantee (G1).
_NEGATED_GUARANTEE = re.compile(
    r"(?:مش|مِش|لا|بدون|من\s+غير)\s+(?:[هأا]قدر\s+)?[أا]?ضمن|مش\s+مضمون"
)

# Asking for the budget again — must actually be a question.
_ASK_BUDGET_Q = (
    re.compile(r"ميزانيتك\s+(?:في\s+حدود\s+)?كام"),
    re.compile(r"حدود\s+كام"),
    re.compile(r"ميزانيتك\s+[أا]يه"),
    re.compile(r"في\s+رينج\s+[أا]يه"),
)
_ASK_GENDER_Q = (
    re.compile(r"رجالي\s+ولا\s+حريمي"),
    re.compile(r"حريمي\s+ولا\s+رجالي"),
)

# The closer family the persona bans by name. Production's BANNED_CLOSERS only matches
# "تحب تعرف الأسعار"; every variant below is the same forbidden move and gets through.
_BANNED_CLOSER_VARIANTS = (
    re.compile(r"تحب[يى]?\s+[أا]عرفك\s+(?:على\s+)?(?:ال)?[أا]?سعار"),
    re.compile(r"تحب[يى]?\s+[أا]قولك\s+(?:ال)?[أا]?سعار"),
    re.compile(r"تحب[يى]?\s+[أا]عرفك\s+[أا]كتر\s+عن"),
    re.compile(r"تحب[يى]?\s+تعرف\s+(?:ال)?[أا]?سعار"),
    re.compile(r"[أا]قدر\s+[أا]ساعدك\s+(?:في\s+)?[أا]?يه"),
    re.compile(r"حاج[هة]\s+تاني[هة]\s+ممكن\s+[أا]ساعدك"),
    re.compile(r"لو\s+حابب\s+[أا]ساعدك\s+في\s+حاج[هة]\s+تاني[هة]"),
)

# Order-closing moves, split the way production splits them. The bool is whether production's
# reply_sanitizer misses the variant entirely (*), which drives severity.
#
# The size-choice pattern moved to _NARROWING: production now permits a narrowing next step one
# stage earlier than a hard ask, because the closer patterns were mechanically one-sided — every
# one of them is an online closer and none matches a walk-in invite, so the only CTA that could
# survive mid-conversation was "come to the shop".
_CLOSING_VARIANTS = (
    (re.compile(r"تحب[يى]?\s+[أا]ساعدك\s+في\s+(?:ال)?(?:طلب|[أا]وردر)"), False),
    (re.compile(r"تحب[يى]?\s+[أا]ساعدك\s+[تن]طلب"), True),
    (re.compile(r"تحب[يى]?\s+[أا]جهزلك"), True),
    (re.compile(r"تحب[يى]?\s+[أا]جهز\s+لك"), True),
    (re.compile(r"تحب[يى]?\s+[نت]طلب"), False),
    (re.compile(r"ن(?:سجل|كمل)\s+(?:ال)?(?:طلب|[أا]وردر)"), False),
)

# "أجيبلك الـ90 ولا الـ50؟" — a size choice, permitted from the recommendation stage on.
_NARROWING = (re.compile(r"[أا]جيبلك\s+(?:الـ?\s*)?\d+"),)

# Imported rather than restated. These were three hardcoded copies of the same set — production,
# checks.py and here — and they had already drifted: R3's "تحبي أجهزلك واحدة؟" scored 9 from the
# judge, was invisible to checks.py, and would have been a high-severity finding here.
from products.services.sales.stage import (  # noqa: E402
    CLOSING_STAGES,
    SOFT_CLOSING_STAGES,
)


def _all_text(record, upto):
    """Everything said by either side up to and including turn `upto`."""
    parts = []
    for turn in record["turns"]:
        if turn["n"] > upto:
            break
        parts.append(turn["user"] or "")
        parts.append(turn.get("context") or "")
        if turn["n"] < upto:
            parts.append(turn["reply"] or "")
    return "\n".join(parts)


def rescore(record, truth, scenario_budget=None):
    findings = []
    previous_reply = None
    budget_stated = False

    for turn in record["turns"]:
        reply = turn["reply"] or ""
        context = turn.get("context") or ""
        history_text = _all_text(record, turn["n"])
        stage = turn.get("stage")
        intent = turn.get("merged_intent") or {}

        def add(code, severity, detail):
            findings.append({
                "turn": turn["n"], "code": code, "severity": severity, "detail": detail,
            })

        # ── invented numbers, with the whole conversation as allowed context ──
        allowed = checks._allowed_numbers(truth, history_text, history_text)
        for number in {m.group().replace(",", "") for m in _NUM.finditer(reply)}:
            base = number.split(".")[0]
            if number in allowed or base in allowed:
                continue
            if base in history_text:
                continue
            try:
                value = float(base)
            except ValueError:
                continue
            if value >= 100:
                add("invented_number", "critical",
                    f"'{number}' is not a catalogue price, a store fact, or anything said "
                    f"earlier in this conversation")

        # ── unsupported guarantee, excluding negated forms ──
        if not _NEGATED_GUARANTEE.search(reply):
            for pattern in checks._GUARANTEE:
                if pattern.search(reply):
                    add("unsupported_guarantee", "high",
                        f"certainty claim /{pattern.pattern}/ with no negation")
                    break

        for pattern in checks._URGENCY:
            if pattern.search(reply):
                add("false_urgency", "high", f"manufactured urgency /{pattern.pattern}/")
                break

        # ── closing before it is earned ──
        for pattern, missed_by_production in _CLOSING_VARIANTS:
            if pattern.search(reply):
                if stage not in CLOSING_STAGES:
                    add("premature_close",
                        "high" if missed_by_production else "medium",
                        f"order-closing question at stage '{stage}'"
                        + (" — variant NOT matched by reply_sanitizer.PREMATURE_CLOSERS"
                           if missed_by_production else " — sanitizer should have caught this"))
                break

        # ── narrowing one stage too early ──
        # Separate from the above because a size choice is not an order ask. It is earned from
        # the recommendation stage on, and is only premature before that.
        for pattern in _NARROWING:
            if pattern.search(reply) and stage not in SOFT_CLOSING_STAGES:
                add("premature_close", "medium",
                    f"size-choice CTA at stage '{stage}', before a recommendation was made")
                break

        # ── the banned-closer family, in variants production misses ──
        for pattern in _BANNED_CLOSER_VARIANTS:
            if pattern.search(reply):
                add("banned_closer_leak", "medium",
                    f"persona-forbidden filler closer /{pattern.pattern}/ survived sanitize_reply")
                break

        # ── re-asking what is already known ──
        if intent.get("max_price"):
            for pattern in _ASK_BUDGET_Q:
                if pattern.search(reply):
                    add("reasked_budget", "high",
                        f"asked for the budget although max_price={intent['max_price']} was known")
                    break
        if intent.get("gender") and intent["gender"] != "multiple":
            for pattern in _ASK_GENDER_Q:
                if pattern.search(reply):
                    add("reasked_gender", "high",
                        f"asked male/female although gender={intent['gender']} was known")
                    break

        # ── gender gate spent a whole turn on a high-information request ──
        if any(p.search(reply) for p in _ASK_GENDER_Q) and len(reply) < 120:
            informative = [
                key for key in ("similar_to", "notes", "perfume_type", "occasion",
                                "season", "longevity", "projection", "max_price",
                                "avoid_notes", "avoid_traits", "wants_uncommon")
                if intent.get(key)
            ]
            if informative:
                add("gender_gate_wasted_turn", "high",
                    f"whole turn spent asking male/female while the customer had already "
                    f"supplied {informative}")

        # ── similarity overclaim ──
        similarity = (turn.get("search") or {}).get("similarity")
        if similarity and not similarity.get("has_close_match"):
            claimed = re.search(r"(شبه|بديل|نفس\s+الريح|نفس\s+الجو)", reply)
            # "مش موجود عندنا حاجة شبهه" is the agent admitting the gap in as many words, but
            # the marker list did not carry "مش موجود" — so an honest admission that happened
            # to contain the word "شبه" was scored as an overclaim.
            admitted = re.search(
                r"(مفيش|مش\s+لاقي|مختلف|مش\s+نفس|مش\s+قريب|مش\s+موجود)", reply
            )
            if claimed and not admitted:
                add("similarity_overclaim", "critical",
                    f"band '{similarity.get('best_band')}' for "
                    f"'{similarity.get('reference_name')}' but the reply asserts a lookalike")

        # ── the reference perfume shortlisted as its own lookalike ──
        matched = (turn.get("search") or {}).get("matched") or []
        reference = (similarity or {}).get("reference_name")
        if reference and reference in matched:
            add("reference_in_own_shortlist", "high",
                f"'{reference}' is the perfume the customer asked to be matched against, yet "
                f"it was shortlisted as a candidate — and its self-similarity of 1.0 is what "
                f"set has_close_match={similarity.get('has_close_match')}")

        # ── denying a perfume we actually stock ──
        # `_DENIAL` sat in checks.py unused, and the docstring at the top of this file already
        # promised the false-positive rule ("Naming an out-of-stock perfume to say it is
        # unavailable is correct behaviour") with nothing implementing it. Both halves land here.
        #
        # Stricter than the checks.py pass in one way that matters: a denial is excused when the
        # perfume's own injected row says it is gone. `format_product` writes
        # "❌ هذا المنتج غير متوفر حالياً بجميع أحجامه" as its Stock Status when nothing is
        # sellable, so a reply relaying that is telling the truth about this turn's data even if
        # the row looks sellable now.
        denied = checks._false_denial(reply, truth)
        if denied and "غير متوفر حالياً بجميع أحجامه" not in (context or ""):
            add("false_denial", "critical",
                f"told the customer '{denied}' is not available, but it is active in the "
                f"catalogue with a sellable bottle"
                + (f" and was in this turn's shortlist {matched}" if denied in matched else ""))

        # ── verbatim-ish repetition across turns ──
        if previous_reply and reply:
            ratio = SequenceMatcher(None, reply.strip(), previous_reply.strip()).ratio()
            if ratio > 0.7:
                add("repeated_reply", "high",
                    f"{ratio:.0%} similar to the previous reply — the customer asked for "
                    f"something new and got the same answer")
        previous_reply = reply or previous_reply

        # ── a stated order total, checked against the scenario's budget ──
        # Independent of _allowed_numbers, which whitelists price x1-4 and every pairwise
        # sum, so a fabricated total is unflaggable there by construction.
        #
        # Only from the turn the customer actually states the budget. runner.py already gates
        # this; rescore did not, so M1's turn 1 — a price answer of 944 given two turns BEFORE
        # the customer mentioned 700 — was scored as breaking a budget that did not exist yet.
        if not budget_stated and scenario_budget:
            budget_stated = (
                str(int(scenario_budget)) in (turn.get("user") or "")
                or bool(intent.get("max_price"))
            )
        budget = scenario_budget if budget_stated else None
        if budget:
            for value in checks.check_stated_total(reply, budget, truth):
                add("over_budget_total", "critical",
                    f"stated an order total of {value:.0f} against a stated budget of "
                    f"{budget}, with no acknowledgement")

        if len(reply) > 700:
            add("too_long", "low", f"{len(reply)} characters")

    return findings


def main():
    with open(os.path.join(HERE, "results", "runs.json"), encoding="utf-8") as handle:
        runs = json.load(handle)

    from products.models import Store
    truth = checks.build_ground_truth(Store.objects.get(name="Perfamix"))

    # The scenario's stated budget, so the total check has something to compare against.
    try:
        from eval_harness.scenarios import SCENARIOS
        budgets = {s["id"]: s.get("assert_budget") for s in SCENARIOS}
    except Exception:
        budgets = {}

    tally, by_scenario = {}, {}
    for record in runs:
        findings = rescore(record, truth, scenario_budget=budgets.get(record["id"]))
        by_scenario[record["id"]] = findings
        for finding in findings:
            key = (finding["code"], finding["severity"])
            tally[key] = tally.get(key, 0) + 1

    out = os.path.join(HERE, "results", "findings.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(by_scenario, handle, ensure_ascii=False, indent=2)

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    print(f"{'CODE':32s} {'SEV':9s} COUNT  SCENARIOS")
    for (code, severity), count in sorted(tally.items(), key=lambda kv: (order[kv[0][1]], -kv[1])):
        hits = sorted({sid for sid, fs in by_scenario.items()
                       for f in fs if f["code"] == code})
        print(f"{code:32s} {severity:9s} {count:5d}  {','.join(hits)}")

    total_turns = sum(len(r["turns"]) for r in runs)
    clean = [sid for sid, fs in by_scenario.items() if not fs]
    print(f"\nscenarios={len(runs)} turns={total_turns} "
          f"findings={sum(len(f) for f in by_scenario.values())}")
    print(f"clean scenarios ({len(clean)}): {','.join(sorted(clean))}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
