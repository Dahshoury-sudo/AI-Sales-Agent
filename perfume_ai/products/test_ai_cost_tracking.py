"""Exact OpenAI tokens and cost per store, recorded on every model call; no API requests."""
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import DatabaseError, transaction
from django.test import RequestFactory, TestCase
from django.utils import timezone
from openai.types.chat import ChatCompletion
from rest_framework.test import APIClient

from products import tasks
from products.admin import ConversationAdmin, StoreMonthlyUsageAdmin
from products.models import AIUsageDaily, Conversation, Store, StoreMonthlyUsage, StoreSettings
from products.services.ai import client as ai_client
from products.services.ai import usage

# 500 ordinary input, 200 cached reads, 300 cache writes, 400 output on gpt-6-luna:
# (500 x 0.10 + 200 x 0.01 + 300 x 0.10 x 1.25 + 400 x 0.50) / 1M
ONE_CALL_USD = Decimal("0.0002895")


def completion(*, model="gpt-6-luna", content="ok", prompt=1000, cached=200, written=300, output=400,
               reasoning=250, write_field="cache_write_tokens", with_usage=True):
    return ChatCompletion(
        id="test-completion", object="chat.completion", created=0, model=model,
        choices=[{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        usage={"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output,
               "prompt_tokens_details": {"cached_tokens": cached, write_field: written},
               "completion_tokens_details": {"reasoning_tokens": reasoning}} if with_usage else None,
    )


def ask(text="hi"):
    return ai_client.chat([{"role": "user", "content": text}])


class OpenAIStubMixin:
    def stub_openai(self, *responses):
        """Replace the OpenAI client; each create() returns the next response (or raises it)."""
        fake = self.enterContext(mock.patch.object(ai_client, "client"))
        fake.chat.completions.create.side_effect = list(responses) or [completion()]
        return fake.chat.completions.create


class RecordingTests(OpenAIStubMixin, TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Cost store")
        self.other = Store.objects.create(name="Other cost store")

    def row(self, store=None, channel="messenger"):
        return AIUsageDaily.objects.get(store=store or self.store, channel=channel)

    def test_a_call_is_saved_with_exact_tokens_and_cost_when_the_turn_ends(self):
        self.stub_openai(completion())
        with usage.attribute(self.store.pk, "messenger"):
            self.assertEqual(ask(), "ok")
            self.assertFalse(AIUsageDaily.objects.exists(), "written before the turn ended")
        row = self.row()
        self.assertEqual(row.date, timezone.localdate())
        self.assertEqual((row.calls, row.failed_calls, row.unpriced_calls), (1, 0, 0))
        self.assertEqual((row.prompt_tokens, row.cached_tokens, row.cache_write_tokens,
                          row.completion_tokens, row.reasoning_tokens), (1000, 200, 300, 400, 250))
        self.assertEqual(row.cost_usd, ONE_CALL_USD)

    def test_the_older_cache_write_field_is_priced_the_same(self):
        self.stub_openai(completion(write_field="cache_creation_tokens"))
        with usage.attribute(self.store.pk, "messenger"):
            ask()
        self.assertEqual(self.row().cache_write_tokens, 300)
        self.assertEqual(self.row().cost_usd, ONE_CALL_USD)

    def test_spend_survives_a_turn_that_rolls_back(self):
        """OpenAI bills the call whether or not the turn's transaction commits."""
        self.stub_openai(completion())
        with usage.attribute(self.store.pk, "messenger"):
            with self.assertRaises(RuntimeError), transaction.atomic():
                ask()
                raise RuntimeError("turn failed after the model answered")
        self.assertEqual(self.row().calls, 1)

    def test_turns_add_up_and_stores_and_channels_stay_apart(self):
        self.stub_openai(*[completion() for _ in range(4)])
        for store, channel in ((self.store, "messenger"), (self.store, "messenger"),
                               (self.store, "web"), (self.other, "messenger")):
            with usage.attribute(store.pk, channel):
                ask()
        self.assertEqual(self.row().calls, 2)
        self.assertEqual(self.row().cost_usd, ONE_CALL_USD * 2)
        self.assertEqual(self.row(channel="web").calls, 1)
        self.assertEqual(self.row(store=self.other).calls, 1)
        self.assertEqual(AIUsageDaily.objects.count(), 3)

    def test_an_empty_answer_and_its_retry_are_two_billed_calls(self):
        self.stub_openai(completion(content=""), completion(content="second"))
        with usage.attribute(self.store.pk, "messenger"):
            self.assertEqual(ask(), "second")
        self.assertEqual(self.row().calls, 2)
        self.assertEqual(self.row().cost_usd, ONE_CALL_USD * 2)

    def test_calls_outside_a_store_turn_are_logged_but_not_saved(self):
        self.stub_openai(completion())
        with self.assertLogs("ai.usage", "INFO") as logs:
            ask()
        self.assertFalse(AIUsageDaily.objects.exists())
        self.assertIn('"store_id":null', logs.output[0])
        self.assertIn('"cost_usd":"0.0002895"', logs.output[0])

    def test_a_failed_call_is_counted_and_still_raises(self):
        self.stub_openai(RuntimeError("connection reset"))
        with usage.attribute(self.store.pk, "messenger"):
            with self.assertRaises(RuntimeError):
                ask()
        row = self.row()
        self.assertEqual((row.calls, row.failed_calls, row.cost_usd), (1, 1, 0))

    def test_an_unknown_model_or_missing_usage_is_counted_as_unpriced(self):
        self.stub_openai(completion(model="gpt-99-unknown"), completion(with_usage=False))
        with usage.attribute(self.store.pk, "messenger"):
            ask()
            ask()
        row = self.row()
        self.assertEqual((row.calls, row.unpriced_calls, row.cost_usd), (2, 2, 0))
        self.assertEqual(row.prompt_tokens, 1000, "tokens of the unknown model are still kept")

    def test_a_database_error_while_saving_never_reaches_the_reply(self):
        self.stub_openai(completion())
        with mock.patch.object(AIUsageDaily.objects, "get_or_create", side_effect=DatabaseError("down")), \
             self.assertLogs("ai.usage", "ERROR"):
            with usage.attribute(self.store.pk, "messenger"):
                self.assertEqual(ask(), "ok")


def route_that_calls_the_model(text, history, store, conversation):
    return ask(text), ""


class EntryPointTests(OpenAIStubMixin, TestCase):
    """Each way production reaches the model attributes its spend to the right store."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.store = Store.objects.create(name="Entry store")
        StoreSettings.objects.create(store=self.store, facebook_page_id="page", messenger_access_token="token")
        self.stub_openai(completion())

    def channels(self):
        return list(AIUsageDaily.objects.values_list("store_id", "channel", "calls"))

    def test_a_whatsapp_message(self):
        with mock.patch.object(tasks, "route", route_that_calls_the_model), \
             mock.patch.object(tasks, "send_platform_message", return_value=True), \
             mock.patch.object(tasks, "sanitize_reply", side_effect=lambda reply, conv: reply):
            tasks.process_incoming_message(self.store.pk, "whatsapp", "2010", "عايز عطر")
        self.assertEqual(self.channels(), [(self.store.pk, "whatsapp", 1)])

    def test_a_facebook_comment(self):
        with mock.patch.object(tasks, "route", route_that_calls_the_model), \
             mock.patch.object(tasks, "sanitize_reply", side_effect=lambda reply, conv: reply), \
             mock.patch.object(tasks, "fetch_post_content", return_value=""), \
             mock.patch.object(tasks, "reply_to_comment"), mock.patch.object(tasks, "send_private_reply"):
            tasks.process_comment_task(self.store.pk, "facebook", "comment-1", "customer", "بكام؟", "post")
        self.assertEqual(self.channels(), [(self.store.pk, "facebook_comment", 1)])

    def test_website_chat(self):
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=self.store.api_key)
        with mock.patch("products.views.route", route_that_calls_the_model), \
             mock.patch("products.views.sanitize_reply", side_effect=lambda reply, conv: reply):
            response = client.post("/api/chat/", {"message": "عايز عطر"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.channels(), [(self.store.pk, "web", 1)])

    def test_admin_evaluation_is_recorded_without_a_store(self):
        conversation = Conversation.objects.create(store=self.store, platform="web")
        model_admin = ConversationAdmin(Conversation, site)
        with mock.patch("products.services.ai.evaluator_service.evaluate_conversation",
                        side_effect=lambda conv: bool(ask())), \
             mock.patch.object(model_admin, "message_user"):
            model_admin.trigger_evaluation(RequestFactory().post("/"), Conversation.objects.filter(pk=conversation.pk))
        self.assertEqual(self.channels(), [(None, "admin_evaluation", 1)])


class ReportTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Perfamix")
        self.other = Store.objects.create(name="Second shop")
        november, october = date(2026, 11, 5), date(2026, 10, 30)
        usage_row = dict(prompt_tokens=8000, cached_tokens=6000, completion_tokens=300)
        AIUsageDaily.objects.create(store=self.store, date=november, channel="whatsapp", calls=20,
                                    cost_usd=Decimal("0.30"), **usage_row)
        AIUsageDaily.objects.create(store=self.store, date=november, channel="web", calls=10,
                                    cost_usd=Decimal("0.10"), **usage_row)
        AIUsageDaily.objects.create(store=self.store, date=october, channel="web", calls=99,
                                    cost_usd=Decimal("9.00"), **usage_row)
        AIUsageDaily.objects.create(store=self.other, date=november, channel="messenger", calls=4,
                                    unpriced_calls=1, cost_usd=Decimal("0.02"), **usage_row)
        AIUsageDaily.objects.create(store=None, date=november, channel="admin_evaluation", calls=2,
                                    cost_usd=Decimal("0.05"), **usage_row)
        StoreMonthlyUsage.objects.create(store=self.store, period=date(2026, 11, 1), llm_messages=200)
        StoreMonthlyUsage.objects.create(store=self.other, period=date(2026, 11, 1), llm_messages=4)

    def report(self, **options):
        out = StringIO()
        call_command("ai_cost_report", month="2026-11", stdout=out, **options)
        return out.getvalue()

    def line(self, text, label):
        return next(line for line in text.splitlines() if line.startswith(label))

    def test_each_store_gets_its_month_cost_messages_and_cost_per_message(self):
        text = self.report(egp_rate="50")
        perfamix = self.line(text, f"Perfamix (#{self.store.pk})")
        self.assertIn("$0.4000", perfamix)        # October's 9.00 is excluded
        self.assertIn("200", perfamix)
        self.assertIn("20.00", perfamix)          # EGP at 50/USD
        self.assertIn("$0.00200", perfamix)       # 0.40 / 200 messages
        self.assertIn("$0.0200", self.line(text, f"Second shop (#{self.other.pk})"))
        self.assertIn("$0.0500", self.line(text, "No store (admin evaluations)"))
        self.assertIn("$0.4700", self.line(text, "TOTAL"))
        self.assertIn("1 calls used a model missing from the price table", text)

    def test_channel_breakdown_and_usd_only_without_a_rate(self):
        with self.settings(USD_EGP_RATE=None):
            text = self.report(by_channel=True)
        self.assertNotIn("Cost EGP", text)
        self.assertIn("By channel", text)
        for channel in ("whatsapp", "web", "messenger", "admin_evaluation"):
            self.assertIn(channel, text)

    def test_bad_arguments_are_refused(self):
        with self.assertRaises(CommandError):
            call_command("ai_cost_report", month="November", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("ai_cost_report", month="2026-11", egp_rate="-3", stdout=StringIO())


class MonthlyAdminCostTests(TestCase):
    def test_the_monthly_usage_list_shows_each_stores_cost_for_that_month(self):
        store = Store.objects.create(name="Admin store")
        other = Store.objects.create(name="Admin other")
        AIUsageDaily.objects.create(store=store, date=date(2026, 11, 2), channel="web", cost_usd=Decimal("0.25"))
        AIUsageDaily.objects.create(store=store, date=date(2026, 11, 20), channel="whatsapp", cost_usd=Decimal("0.15"))
        AIUsageDaily.objects.create(store=store, date=date(2026, 10, 31), channel="web", cost_usd=Decimal("5"))
        AIUsageDaily.objects.create(store=other, date=date(2026, 11, 2), channel="web", cost_usd=Decimal("7"))
        StoreMonthlyUsage.objects.create(store=store, period=date(2026, 11, 1), llm_messages=100)
        request = RequestFactory().get("/admin/")
        request.user = User.objects.create_superuser("cost-admin", "a@example.test", "pw")
        model_admin = StoreMonthlyUsageAdmin(StoreMonthlyUsage, site)

        row = model_admin.get_queryset(request).get(store=store)

        self.assertEqual(row.ai_cost_usd, Decimal("0.40"))
        self.assertEqual(model_admin.ai_cost(row), "$0.4000")
        self.assertEqual(model_admin.cost_per_message(row), "$0.00400")

    def test_the_list_and_the_single_month_page_both_show_the_cost(self):
        store = Store.objects.create(name="Admin page store")
        AIUsageDaily.objects.create(store=store, date=date(2026, 11, 2), channel="whatsapp", calls=12,
                                    prompt_tokens=96_000, cached_tokens=72_000, completion_tokens=960,
                                    unpriced_calls=1, cost_usd=Decimal("0.0345"))
        usage_row = StoreMonthlyUsage.objects.create(store=store, period=date(2026, 11, 1), llm_messages=5)
        empty = StoreMonthlyUsage.objects.create(store=store, period=date(2026, 9, 1), llm_messages=3)
        self.client.force_login(User.objects.create_superuser("page-admin", "p@example.test", "pw"))

        listing = self.client.get("/admin/products/storemonthlyusage/").content.decode()
        self.assertIn("AI cost (USD)", listing)
        self.assertIn("$0.0345", listing)

        page = self.client.get(f"/admin/products/storemonthlyusage/{usage_row.pk}/change/").content.decode()
        for text in ("$0.0345", "$0.00690", "AI usage by channel", "<td>whatsapp</td>", "<td>96,000</td>",
                     "<td>75%</td>", "<td>1 unpriced</td>"):
            self.assertIn(text, page)

        before = self.client.get(f"/admin/products/storemonthlyusage/{empty.pk}/change/").content.decode()
        self.assertIn("No AI usage recorded for this month", before)
