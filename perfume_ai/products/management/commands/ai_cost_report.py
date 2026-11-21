"""Monthly OpenAI cost per store.

    python manage.py ai_cost_report                     # this month, USD
    python manage.py ai_cost_report --month 2026-11 --egp-rate 50
    python manage.py ai_cost_report --by-channel        # adds a per-channel breakdown

Costs come from AIUsageDaily (written on every model call); AI messages come from
StoreMonthlyUsage (the count plans and caps are based on). The total should match
OpenAI's usage page for the same month when production has its own API key.
"""
import sys
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.utils import timezone

from products.models import AIUsageDaily, Store, StoreMonthlyUsage
from products.services.ai.usage import PRICING_DATE

SUMMED = ("calls", "failed_calls", "unpriced_calls", "prompt_tokens", "cached_tokens",
          "cache_write_tokens", "completion_tokens", "reasoning_tokens", "cost_usd")
NO_STORE = "No store (admin evaluations)"


def monthly_usage(month_start, by_channel=False):
    """Summed AIUsageDaily rows for one calendar month, per store (and channel)."""
    keys = ["store_id"] + (["channel"] if by_channel else [])
    return list(AIUsageDaily.objects
                .filter(date__year=month_start.year, date__month=month_start.month)
                .values(*keys).annotate(**{name: Sum(name) for name in SUMMED}).order_by(*keys))


def parse_month(value):
    if not value:
        return timezone.localdate().replace(day=1)
    try:
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)
    except (ValueError, TypeError):
        raise CommandError(f"--month must look like 2026-11, got {value!r}")


def parse_rate(value):
    if value in (None, ""):
        return None
    try:
        rate = Decimal(str(value))
    except InvalidOperation:
        raise CommandError(f"--egp-rate must be a number, got {value!r}")
    if rate <= 0:
        raise CommandError("--egp-rate must be positive")
    return rate


class Command(BaseCommand):
    help = "Show each store's OpenAI cost (tokens and money) for one month."

    def add_arguments(self, parser):
        parser.add_argument("--month", help="YYYY-MM (default: this month, UTC)")
        parser.add_argument("--egp-rate", help="EGP per USD (default: settings.USD_EGP_RATE)")
        parser.add_argument("--by-channel", action="store_true", help="Add a per-channel breakdown")

    def handle(self, *args, **options):
        if self.stdout._out is sys.stdout and hasattr(sys.stdout, "reconfigure"):
            # Store names are often Arabic; a non-UTF-8 console must not crash the report.
            sys.stdout.reconfigure(errors="replace")
        month = parse_month(options["month"])
        rate = parse_rate(options["egp_rate"] if options["egp_rate"] is not None else settings.USD_EGP_RATE)

        usage = {row["store_id"]: row for row in monthly_usage(month)}
        messages = dict(StoreMonthlyUsage.objects.filter(period=month).values_list("store_id", "llm_messages"))
        store_ids = sorted({*usage, *messages} - {None})
        names = dict(Store.objects.filter(pk__in=store_ids).values_list("pk", "name"))

        header = ["Store", "AI messages", "Model calls", "Input tokens", "Cached", "Output tokens", "Cost USD"]
        if rate:
            header.append("Cost EGP")
        header.append("USD per AI message")

        rows, total = [], {name: 0 for name in SUMMED}
        total_messages = 0
        for store_id in store_ids + ([None] if None in usage else []):
            row = usage.get(store_id) or {}
            count = messages.get(store_id)
            label = f"{names.get(store_id, '?')} (#{store_id})" if store_id else NO_STORE
            rows.append(self.line(label, count, row, rate))
            for name in SUMMED:
                total[name] += row.get(name) or 0
            total_messages += count or 0
        rows.append(self.line("TOTAL", total_messages, total, rate))

        self.stdout.write(f"AI cost for {month:%Y-%m}  (prices dated {PRICING_DATE}"
                          + (f", {rate} EGP/USD)" if rate else ")"))
        self.table(header, rows)

        if options["by_channel"]:
            self.stdout.write("")
            self.stdout.write("By channel")
            channel_header = ["Store", "Channel", "Model calls", "Input tokens", "Cached", "Output tokens", "Cost USD"]
            channel_rows = []
            for row in monthly_usage(month, by_channel=True):
                store_id = row["store_id"]
                label = f"{names.get(store_id, '?')} (#{store_id})" if store_id else NO_STORE
                channel_rows.append([label, row["channel"], *self.usage_cells(row)])
            self.table(channel_header, channel_rows)

        self.stdout.write("")
        if total["unpriced_calls"]:
            self.stdout.write(self.style.WARNING(
                f"{total['unpriced_calls']} calls used a model missing from the price table, or returned "
                f"no usage; their cost is NOT included. Update PRICES in products/services/ai/usage.py."))
        if total["failed_calls"]:
            self.stdout.write(self.style.WARNING(
                f"{total['failed_calls']} calls failed before a response; OpenAI may have billed some."))
        self.stdout.write(f"Compare TOTAL with OpenAI's usage page for {month:%Y-%m} "
                          f"(exact when production has its own API key).")

    def usage_cells(self, row):
        prompt = row.get("prompt_tokens") or 0
        cached = row.get("cached_tokens") or 0
        cost = row.get("cost_usd") or Decimal(0)
        return [str(row.get("calls") or 0), f"{prompt:,}", f"{cached / prompt:.0%}" if prompt else "-",
                f"{row.get('completion_tokens') or 0:,}", f"${cost:.4f}"]

    def line(self, label, messages, row, rate):
        cost = row.get("cost_usd") or Decimal(0)
        cells = [label, "-" if messages is None else f"{messages:,}", *self.usage_cells(row)]
        if rate:
            cells.append(f"{cost * rate:,.2f}")
        cells.append(f"${cost / messages:.5f}" if messages and row.get("calls") else "-")
        return cells

    def table(self, header, rows):
        widths = [max(len(str(cell)) for cell in column) for column in zip(header, *rows)]
        for cells in [header, ["-" * width for width in widths], *rows]:
            self.stdout.write("  ".join(str(cell).ljust(width) for cell, width in zip(cells, widths)).rstrip())
