from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Sum, Case, When, F, Value, CharField
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from products.models import Conversation, Order, OrderItem
from products.services.usage_service import messages_used_this_month, monthly_cap
from .auth_backend import StoreOwnerAuthentication
from .analytics import Filters, Report, serialize
from rest_framework.exceptions import ValidationError
from django.http import HttpResponse
import csv
import io


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _revenue_for_period(store, start, end):
    """Return confirmed/delivered revenue between two datetimes."""
    result = Order.objects.filter(
        store=store,
        status__in=["confirmed", "delivered"],
        created_at__gte=start,
        created_at__lt=end,
    ).aggregate(total=Sum("total_price"))
    return result["total"] or Decimal("0")


def _orders_for_period(store, start, end):
    """Return total order count between two datetimes."""
    return Order.objects.filter(
        store=store,
        created_at__gte=start,
        created_at__lt=end,
    ).count()


def _conversations_for_period(store, start, end):
    """Return conversation count between two datetimes."""
    return Conversation.objects.filter(
        store=store,
        created_at__gte=start,
        created_at__lt=end,
    ).count()


def _pct_change(current, previous):
    """Calculate percentage change, returns None if previous is 0."""
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _period_block(store, label, current_start, current_end, prev_start, prev_end):
    """Build a comparison block for a given period pair."""
    cur_rev  = _revenue_for_period(store, current_start, current_end)
    prev_rev = _revenue_for_period(store, prev_start, prev_end)

    cur_orders  = _orders_for_period(store, current_start, current_end)
    prev_orders = _orders_for_period(store, prev_start, prev_end)

    cur_convs  = _conversations_for_period(store, current_start, current_end)
    prev_convs = _conversations_for_period(store, prev_start, prev_end)

    return {
        "label": label,
        "current": {
            "revenue":       cur_rev,
            "orders":        cur_orders,
            "conversations": cur_convs,
        },
        "previous": {
            "revenue":       prev_rev,
            "orders":        prev_orders,
            "conversations": prev_convs,
        },
        "change": {
            "revenue":       _pct_change(cur_rev, prev_rev),
            "orders":        _pct_change(cur_orders, prev_orders),
            "conversations": _pct_change(cur_convs, prev_convs),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────────────────────────────────

class DashboardOverviewView(APIView):
    """Quick-summary endpoint used by the Overview section."""

    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store

        total_conversations = Conversation.objects.filter(store=store).count()
        total_orders        = Order.objects.filter(store=store).count()
        pending_orders      = Order.objects.filter(store=store, status="pending").count()
        pending_handoffs    = Conversation.objects.filter(store=store, needs_human=True).count()

        # Platform breakdown
        platform_stats = (
            Conversation.objects.filter(store=store)
            .values("platform")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # Total confirmed/delivered revenue
        revenue = Order.objects.filter(
            store=store, status__in=["confirmed", "delivered"]
        ).aggregate(total=Sum("total_price"))

        return Response({
            "total_conversations": total_conversations,
            "total_orders":        total_orders,
            "pending_orders":      pending_orders,
            "pending_handoffs":    pending_handoffs,
            "revenue":             str(revenue["total"] or 0),
            "platform_breakdown":  {s["platform"]: s["count"] for s in platform_stats},
        })


class AnalyticsView(APIView):
    """
    Comprehensive analytics endpoint.

    Returns:
      - period_comparisons: today/yesterday, this week/last week, this month/last month
      - order_status_breakdown: counts per status
      - platform_breakdown: conversations per platform
      - top_products: top-5 best-selling products by quantity
      - kpis: conversion_rate, avg_order_value, total_revenue, total_orders,
              total_conversations, unique_customers
    """

    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.query_params.get("version") == "2":
            return Response(Report(request.store, Filters.parse(request.query_params)).overview())
        if "version" in request.query_params:
            raise ValidationError({"version": "نسخة التقرير غير مدعومة."})
        store = request.store
        now   = timezone.now()

        # ── Date boundaries ──────────────────────────────────────────────────
        today_start     = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end       = today_start + timedelta(days=1)
        yesterday_start = today_start - timedelta(days=1)
        yesterday_end   = today_start

        week_start      = today_start - timedelta(days=now.weekday())
        week_end        = today_end
        last_week_start = week_start - timedelta(weeks=1)
        last_week_end   = week_start

        month_start      = today_start.replace(day=1)
        month_end        = today_end
        last_month_end   = month_start
        last_month_start = (month_start - timedelta(days=1)).replace(day=1)

        # ── Period comparison blocks ─────────────────────────────────────────
        period_comparisons = [
            _period_block(store, "اليوم vs امبارح",
                          today_start,     today_end,
                          yesterday_start, yesterday_end),
            _period_block(store, "الأسبوع ده vs اللي فات",
                          week_start,      week_end,
                          last_week_start, last_week_end),
            _period_block(store, "الشهر ده vs اللي فات",
                          month_start,     month_end,
                          last_month_start, last_month_end),
        ]

        # ── Order status breakdown ───────────────────────────────────────────
        status_qs = (
            Order.objects.filter(store=store)
            .values("status")
            .annotate(count=Count("id"))
        )
        order_status_breakdown = {row["status"]: row["count"] for row in status_qs}

        # ── Platform breakdown ───────────────────────────────────────────────
        platform_qs = (
            Conversation.objects.filter(store=store)
            .values("platform")
            .annotate(count=Count("id"))
        )
        platform_breakdown = {row["platform"]: row["count"] for row in platform_qs}

        # ── Top-5 products by units sold ─────────────────────────────────────
        top_products_qs = (
            OrderItem.objects.filter(order__store=store)
            .annotate(product_name=Case(
                When(variant__product__store=store, then=F("variant__product__name")),
                When(variant__isnull=True, then=F("product_snapshot_name")),
                default=Value("منتج غير متاح"), output_field=CharField(),
            ))
            .values("product_name")
            .annotate(total_qty=Sum("quantity"))
            .order_by("-total_qty")[:5]
        )
        top_products = [
            {"name": row["product_name"] or "منتج غير متاح", "qty": row["total_qty"]}
            for row in top_products_qs
        ]

        # ── Global KPIs ──────────────────────────────────────────────────────
        total_orders        = Order.objects.filter(store=store).count()
        total_conversations = Conversation.objects.filter(store=store).count()
        total_revenue       = (
            Order.objects.filter(store=store, status__in=["confirmed", "delivered"])
            .aggregate(total=Sum("total_price"))["total"] or Decimal("0")
        )
        delivered_orders = Order.objects.filter(
            store=store, status__in=["confirmed", "delivered"]
        ).count()

        conversion_rate = (
            round((total_orders / total_conversations) * 100, 1)
            if total_conversations > 0 else 0
        )
        avg_order_value = (
            round(total_revenue / delivered_orders, 2)
            if delivered_orders > 0 else 0
        )
        # Counted on the blind index, not customer_phone: that column is encrypted with
        # a non-deterministic cipher, so a DISTINCT over it would report every order as
        # a separate customer. The hash is also normalized, which fixes a bug that
        # predates encryption — "0100 000 0000" and "01000000000" used to count twice.
        unique_customers = (
            Order.objects.filter(store=store)
            .exclude(customer_phone_hash="")
            .values("customer_phone_hash")
            .distinct()
            .count()
        )

        # Read-only: usage_for would get_or_create, writing a row on every dashboard
        # page load, including for months the store never sent anything.
        messages_this_month = messages_used_this_month(store)
        cap = monthly_cap(store)

        return Response({
            "period_comparisons":    period_comparisons,
            "order_status_breakdown": order_status_breakdown,
            "platform_breakdown":    platform_breakdown,
            "top_products":          top_products,
            "kpis": {
                "total_revenue":       total_revenue,
                "total_orders":        total_orders,
                "total_conversations": total_conversations,
                "unique_customers":    unique_customers,
                "conversion_rate":     conversion_rate,
                "avg_order_value":     avg_order_value,
                "messages_this_month": messages_this_month,
                "monthly_message_cap": cap,
            },
        })


class AnalyticsProductsView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        filters = Filters.parse(request.query_params)
        return Response(serialize({"meta": filters.meta(), **Report(request.store, filters).products()}))


def _csv_cell(value):
    # Only text needs spreadsheet formula protection; preserve signed numbers.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return "" if value is None else value


class AnalyticsExportView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        filters = Filters.parse(request.query_params)
        service = Report(request.store, filters)
        report = filters.data["report"]
        if report == "products":
            rows = service.products(export=True)
            columns = ["product_key", "name", "units", "orders", "revenue"]
        elif report == "trends":
            rows = service.trends()
            columns = ["start", "end", "previous_start", "previous_end", "revenue", "previous_revenue", "orders", "previous_orders"]
        elif report == "statuses":
            rows, columns = service.statuses(), ["status", "orders"]
        elif report == "channels":
            rows, columns = service.channels(), ["platform", "conversations", "orders", "revenue"]
        else:
            from .analytics import change
            current = service.kpis(filters.start, filters.end)
            previous = service.kpis(filters.previous_start, filters.previous_end)
            rows = [{"metric": key, "current": value, "previous": previous[key], "change_percent": change(value, previous[key])} for key, value in current.items()]
            columns = ["metric", "current", "previous", "change_percent"]
        meta = {"report": report, **filters.meta(), "currency": "EGP"}
        # Keep selected-period metadata distinct from each trend bucket's dates.
        for key in ("start", "end", "previous_start", "previous_end"):
            meta["filter_" + key] = meta.pop(key)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([*meta, *columns])
        for row in rows or [{}]:
            writer.writerow([*[_csv_cell(v) for v in meta.values()], *[_csv_cell(row.get(key)) for key in columns]])
        response = HttpResponse("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="analytics-{report}-{meta["filter_start"]}-{meta["filter_end"]}.csv"'
        response["Cache-Control"] = "private, no-store"
        return response

