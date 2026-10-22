"""Read-only store reporting. Calendar windows use settings.TIME_ZONE.

Revenue reflects current order status, dated by order creation (not settlement).
Conversion is a new-conversation cohort with qualifying orders in that same window.
No customer identifiers or individual customer records leave this module.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from collections import OrderedDict

from django.db.models import (BigIntegerField, Case, CharField, Count, DecimalField,
                             Exists, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value, When)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework import serializers

from products.models import Conversation, Order, OrderItem, Product

QUALIFYING = ("confirmed", "delivered")
MONEY = DecimalField(max_digits=24, decimal_places=2)
ZERO = Decimal("0")
PLATFORMS = dict(Conversation.PLATFORM_CHOICES)
REPORTS = ("overview", "trends", "statuses", "products", "channels")


class FilterSerializer(serializers.Serializer):
    preset = serializers.ChoiceField(choices=("today", "7d", "30d", "month", "custom"), default="30d")
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    aggregation = serializers.ChoiceField(choices=("daily", "weekly", "monthly"), default="daily")
    search = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")
    sort = serializers.ChoiceField(choices=("name", "-name", "units", "-units", "revenue", "-revenue", "orders", "-orders"), default="-revenue")
    page = serializers.IntegerField(min_value=1, max_value=100000, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100, default=20)
    report = serializers.ChoiceField(choices=REPORTS, default="overview")
    as_of = serializers.DateTimeField(required=False)


@dataclass(frozen=True)
class Filters:
    data: dict
    start: datetime
    end: datetime
    previous_start: datetime
    previous_end: datetime
    end_date: date
    as_of: datetime
    days: int

    @classmethod
    def parse(cls, params, now=None):
        serializer = FilterSerializer(data=params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        tz = timezone.get_default_timezone()
        now = (now or timezone.now()).astimezone(tz)
        cutoff = data.get("as_of", now).astimezone(tz)
        if cutoff > now:
            raise serializers.ValidationError({"as_of": "وقت التحديث لا يمكن أن يكون في المستقبل."})
        today = cutoff.date()
        if today.year < 2:
            raise serializers.ValidationError({"as_of": "التاريخ أقدم من نطاق المقارنة المدعوم."})
        if data["preset"] == "custom":
            if "start" not in data or "end" not in data:
                raise serializers.ValidationError({"start": "اختر تاريخ البداية والنهاية."})
            first, last = data["start"], data["end"]
        else:
            if "start" in data or "end" in data:
                raise serializers.ValidationError({"preset": "استخدم الفترة المخصصة مع تواريخ البداية والنهاية."})
            last = today
            first = today.replace(day=1) if data["preset"] == "month" else today - timedelta(days={"today": 0, "7d": 6, "30d": 29}[data["preset"]])
        days = (last - first).days + 1
        if not 1 <= days <= 366 or last > today:
            raise serializers.ValidationError({"end": "اختر فترة مرتبة حتى اليوم، بحد أقصى ٣٦٦ يوماً."})
        if first.toordinal() <= days:
            raise serializers.ValidationError({"start": "التاريخ أقدم من نطاق المقارنة المدعوم."})
        # Midnight construction is intentional: timedelta arithmetic on UTC would
        # move local day boundaries across DST changes.
        start = datetime.combine(first, time.min, tz)
        end = min(datetime.combine(last + timedelta(days=1), time.min, tz), cutoff)
        previous_start = datetime.combine(first - timedelta(days=days), time.min, tz)
        previous_end = end - timedelta(days=days)
        return cls(data, start, end, previous_start, previous_end, last, cutoff, days)

    def meta(self):
        return {
            "start": self.start.date().isoformat(), "end": self.end_date.isoformat(),
            "previous_start": self.previous_start.date().isoformat(),
            "previous_end": (self.end_date - timedelta(days=self.days)).isoformat(),
            "end_exclusive": self.end.isoformat(), "previous_end_exclusive": self.previous_end.isoformat(),
            "as_of": self.as_of.isoformat(), "timezone": str(timezone.get_default_timezone()),
            "partial": self.end_date == self.as_of.date(),
            "aggregation": self.data["aggregation"], "preset": self.data["preset"],
            "search": self.data["search"], "sort": self.data["sort"],
        }


def serialize(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serialize(v) for v in value]
    return value


def change(current, previous):
    if current is None or previous in (None, 0):
        return None
    return ((Decimal(current) - Decimal(previous)) / Decimal(previous) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


class Report:
    def __init__(self, store, filters):
        self.store = store
        self.filters = filters
        self.orders = self.orders_between(filters.start, filters.end)
        self.conversations = Conversation.objects.filter(store=store, created_at__gte=filters.start, created_at__lt=filters.end)
        self.items = OrderItem.objects.filter(order__in=self.orders.filter(status__in=QUALIFYING))

    def orders_between(self, start, end):
        return Order.objects.filter(store=self.store, created_at__gte=start, created_at__lt=end)

    def kpis(self, start, end):
        orders = self.orders_between(start, end)
        stats = orders.aggregate(
            orders=Count("id"), qualifying_orders=Count("id", filter=Q(status__in=QUALIFYING)),
            revenue=Sum("total_price", filter=Q(status__in=QUALIFYING), default=ZERO),
            customers=Count("customer_phone_hash", distinct=True, filter=~Q(customer_phone_hash="")),
            missing_customer_orders=Count("id", filter=Q(customer_phone_hash="")),
        )
        cohort = Conversation.objects.filter(store=self.store, created_at__gte=start, created_at__lt=end)
        linked = orders.filter(status__in=QUALIFYING, conversation_id=OuterRef("pk"), created_at__gte=OuterRef("created_at"))
        conversations = cohort.annotate(converted=Exists(linked)).aggregate(
            conversations=Count("id"), converted_conversations=Count("id", filter=Q(converted=True)))
        stats.update(conversations)
        stats["revenue"] = stats["revenue"].quantize(Decimal("0.01"))
        stats["aov"] = (stats["revenue"] / stats["qualifying_orders"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if stats["qualifying_orders"] else None
        stats["conversion"] = (Decimal(stats["converted_conversations"]) / stats["conversations"] * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if stats["conversations"] else None
        return stats

    def products_query(self):
        valid_live = Q(variant__product__store=self.store)
        key = Case(
            When(valid_live, then=Coalesce("product_snapshot_id", "variant__product_id", output_field=BigIntegerField())),
            When(variant__isnull=True, then=F("product_snapshot_id")),
            default=Value(None), output_field=BigIntegerField())
        names = self.items.filter(product_snapshot_id=OuterRef("product_key")).filter(
            Q(variant__isnull=True) | valid_live).exclude(product_snapshot_name="").order_by("-order__created_at", "-pk")
        live_name = Product.objects.filter(store=self.store, pk=OuterRef("product_key")).values("name")[:1]
        return self.items.order_by().annotate(product_key=key).annotate(
            name=Coalesce(Subquery(live_name), Subquery(names.values("product_snapshot_name")[:1]), Value("منتج غير متاح"), output_field=CharField()),
        ).values("product_key", "name").annotate(
            units=Sum("quantity"), orders=Count("order_id", distinct=True),
            revenue=Sum(ExpressionWrapper(F("quantity") * F("price_at_time_of_order"), output_field=MONEY)),
        )

    def products(self, export=False):
        qs = self.products_query()
        if self.filters.data["search"]:
            qs = qs.filter(name__icontains=self.filters.data["search"])
        qs = qs.order_by(self.filters.data["sort"], "product_key")
        count = qs.count()
        if export:
            if count > 10000:
                raise serializers.ValidationError({"report": "التقرير يتجاوز ١٠٬٠٠٠ صف. اختر فترة أو بحثاً أضيق."})
            return list(qs)
        size, page = self.filters.data["page_size"], self.filters.data["page"]
        pages = max(1, (count + size - 1) // size)
        page = min(page, pages)
        return {"rows": list(qs[(page - 1) * size:page * size]), "total": count, "page": page, "pages": pages, "page_size": size}

    def trends(self):
        f = self.filters
        def daily(start, end):
            rows = self.orders_between(start, end).order_by().annotate(day=TruncDate("created_at", tzinfo=timezone.get_default_timezone())).values("day").annotate(
                orders=Count("id"), revenue=Sum("total_price", filter=Q(status__in=QUALIFYING), default=ZERO))
            return {row["day"]: row for row in rows}
        current, previous = daily(f.start, f.end), daily(f.previous_start, f.previous_end)
        buckets = OrderedDict()
        for offset in range(f.days):
            day = f.start.date() + timedelta(days=offset)
            prior = day - timedelta(days=f.days)
            key = day
            if f.data["aggregation"] == "weekly":
                key = day - timedelta(days=day.weekday())
            elif f.data["aggregation"] == "monthly":
                key = day.replace(day=1)
            if key not in buckets:
                buckets[key] = {"start": day.isoformat(), "previous_start": prior.isoformat(), "revenue": ZERO, "previous_revenue": ZERO, "orders": 0, "previous_orders": 0}
            bucket = buckets[key]
            bucket.update(end=day.isoformat(), previous_end=prior.isoformat())
            for metric in ("orders", "revenue"):
                bucket[metric] += current.get(day, {}).get(metric, 0)
                bucket["previous_" + metric] += previous.get(prior, {}).get(metric, 0)
        return list(buckets.values())

    def statuses(self):
        counts = dict(self.orders.values("status").annotate(count=Count("id")).values_list("status", "count"))
        rows = [{"status": status, "orders": counts.pop(status, 0)} for status, _ in Order.STATUS_CHOICES]
        if counts:
            rows.append({"status": "unknown", "orders": sum(counts.values())})
        return rows

    def channels(self):
        normalize = lambda platform: platform if platform in PLATFORMS else "unknown"
        rows = {key: {"platform": key, "conversations": 0, "orders": 0, "revenue": ZERO} for key in [*PLATFORMS, "unknown", "unattributed"]}
        for row in self.conversations.values("platform").annotate(count=Count("id")):
            rows[normalize(row["platform"])]["conversations"] += row["count"]
        qs = self.orders.annotate(channel=Case(
            When(conversation__store=self.store, conversation__platform__in=PLATFORMS, then=F("conversation__platform")),
            When(conversation__store=self.store, then=Value("unknown")),
            default=Value("unattributed"), output_field=CharField(),
        )).values("channel").annotate(orders=Count("id"), revenue=Sum("total_price", filter=Q(status__in=QUALIFYING), default=ZERO))
        for row in qs:
            key = "unattributed" if row["channel"] == "unattributed" else normalize(row["channel"])
            rows[key]["orders"] += row["orders"]
            rows[key]["revenue"] += row["revenue"]
        return list(rows.values())

    def overview(self):
        f = self.filters
        current, previous = self.kpis(f.start, f.end), self.kpis(f.previous_start, f.previous_end)
        changes = {key: change(current[key], previous[key]) for key in ("revenue", "orders", "aov", "customers", "conversations", "conversion")}
        top_units = list(self.products_query().order_by("-units", "product_key")[:5])
        top_revenue = list(self.products_query().order_by("-revenue", "product_key")[:5])
        channels = self.channels()
        item_total = self.items.aggregate(total=Sum(ExpressionWrapper(F("quantity") * F("price_at_time_of_order"), output_field=MONEY), default=ZERO))["total"].quantize(Decimal("0.01"))
        missing_item_orders = self.orders.filter(status__in=QUALIFYING).filter(~Exists(OrderItem.objects.filter(order_id=OuterRef("pk")))).count()
        leader = max((r for r in channels if r["platform"] not in ("unknown", "unattributed") and r["revenue"] > 0), key=lambda r: r["revenue"], default=None)
        return serialize({
            "meta": f.meta(), "kpis": current, "previous": previous, "changes": changes,
            "trends": self.trends(), "statuses": self.statuses(), "channels": channels,
            "top_units": top_units, "top_revenue": top_revenue, "products": self.products(),
            "reconciliation": {"item_revenue": item_total, "difference": current["revenue"] - item_total, "orders_without_items": missing_item_orders},
            "summary": {"revenue_change": changes["revenue"], "leading_product": top_revenue[0] if top_revenue else None, "leading_channel": leader},
        })
