"""One busy store must not limit or delay another store.

Covers the three layers: per-store rate-limit counters on direct messages, the comment
limits, and the per-store Celery queues that make the worker take turns between stores.
"""
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from products import tasks
from products.models import Message, PostCommentRule, Store, StoreSettings
from products.services import rate_limit
from products.task_routing import STORE_QUEUE_COUNT, all_queue_names, store_queue


def fill(bucket, limit):
    for _ in range(limit):
        rate_limit.hit(bucket, limit, 60)


class MessageLimitIsolationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.busy = Store.objects.create(name="Busy store")
        self.quiet = Store.objects.create(name="Quiet store")

    def run_message(self, store, sender="2010"):
        with mock.patch.object(tasks, "route", return_value=("ok", "")), \
             mock.patch.object(tasks, "send_platform_message", return_value=True), \
             mock.patch.object(tasks, "sanitize_reply", side_effect=lambda reply, conv: reply), \
             mock.patch.object(tasks.process_incoming_message, "apply_async") as deferred:
            tasks.process_incoming_message(store.pk, "whatsapp", sender, "عايز عطر")
        return deferred

    def replies(self, store):
        return Message.objects.filter(conversation__store=store, role="assistant").count()

    def test_a_store_at_its_ceiling_defers_only_its_own_messages(self):
        fill(f"store:{self.busy.pk}", tasks.STORE_PER_MINUTE)

        self.assertTrue(self.run_message(self.busy).called, "the busy store was not limited")
        self.assertEqual(self.replies(self.busy), 0)

        self.assertFalse(self.run_message(self.quiet).called, "the quiet store was limited too")
        self.assertEqual(self.replies(self.quiet), 1)

    def test_the_same_customer_messaging_two_stores_has_two_allowances(self):
        for _ in range(tasks.SENDER_PER_MINUTE):
            self.run_message(self.busy, sender="201000")
        self.assertTrue(self.run_message(self.busy, sender="201000").called)

        self.assertFalse(self.run_message(self.quiet, sender="201000").called)


class CommentLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.store = self.make_store("Comment store")
        self.patches = {name: self.enterContext(mock.patch.object(tasks, name)) for name in (
            "fetch_post_content", "reply_to_comment", "reply_to_ig_comment", "send_private_reply")}
        self.patches["fetch_post_content"].return_value = ""
        self.route = self.enterContext(mock.patch.object(tasks, "route", return_value=("answer", "")))
        self.enterContext(mock.patch.object(tasks, "sanitize_reply", side_effect=lambda reply, conv: reply))
        self.serial = 0

    def make_store(self, name):
        store = Store.objects.create(name=name)
        StoreSettings.objects.create(store=store, facebook_page_id=f"page-{name}",
                                     instagram_account_id=f"ig-{name}", messenger_access_token="token")
        return store

    def comment(self, store=None, commenter="c1", deferrals=0, retries=0):
        """Run one comment; returns (answered, deferred_call)."""
        self.serial += 1
        args = [(store or self.store).pk, "facebook", f"comment-{self.serial}", commenter, "بكام؟", "post"]
        private = self.patches["send_private_reply"]
        before = private.call_count
        with mock.patch.object(tasks.process_comment_task, "apply_async") as deferred:
            if retries:
                tasks.process_comment_task.apply(args=args, kwargs={"deferrals": deferrals}, retries=retries)
            else:
                tasks.process_comment_task(*args, deferrals=deferrals)
        return private.call_count > before, deferred

    def count(self, bucket):
        return cache.get(f"ratelimit:{bucket}")

    def test_a_commenter_over_the_minute_limit_is_deferred_not_answered(self):
        for _ in range(tasks.COMMENT_SENDER_PER_MINUTE):
            answered, deferred = self.comment()
            self.assertTrue(answered)
            self.assertFalse(deferred.called)
        calls = self.route.call_count

        answered, deferred = self.comment()

        self.assertFalse(answered)
        self.assertEqual(self.route.call_count, calls, "a model call was spent on a limited comment")
        self.assertEqual(deferred.call_args.kwargs["kwargs"], {"deferrals": 1})
        self.assertGreater(deferred.call_args.kwargs["countdown"], 0)

    def test_deferral_stops_at_the_cap(self):
        for _ in range(tasks.COMMENT_SENDER_PER_MINUTE):
            self.comment()

        answered, deferred = self.comment(deferrals=tasks.MAX_DEFERRALS)

        self.assertFalse(answered)
        self.assertFalse(deferred.called, "deferred past the cap instead of dropping")

    def test_deferred_and_retried_comments_are_not_charged_twice(self):
        bucket = f"comment-sender:{self.store.pk}:facebook:c1"
        self.comment()
        self.assertEqual(self.count(bucket), 1)

        self.assertTrue(self.comment(deferrals=1)[0])
        self.assertTrue(self.comment(retries=1)[0])

        self.assertEqual(self.count(bucket), 1)

    def test_one_stores_comment_wave_does_not_defer_another_store(self):
        other = self.make_store("Other comment store")
        fill(f"comment-store:{self.store.pk}", tasks.COMMENT_STORE_PER_MINUTE)

        busy_answered, busy_deferred = self.comment(commenter="fresh")
        other_answered, other_deferred = self.comment(store=other, commenter="fresh")

        self.assertFalse(busy_answered)
        self.assertTrue(busy_deferred.called)
        self.assertTrue(other_answered)
        self.assertFalse(other_deferred.called)

    def test_comments_do_not_spend_the_direct_message_budget(self):
        self.comment()

        self.assertEqual(self.count(f"comment-store:{self.store.pk}"), 1)
        self.assertIsNone(self.count(f"store:{self.store.pk}"))

    def test_comments_that_would_not_be_answered_are_not_counted(self):
        StoreSettings.objects.filter(store=self.store).update(facebook_comment_replies_enabled=False)
        self.comment()
        StoreSettings.objects.filter(store=self.store).update(facebook_comment_replies_enabled=True)
        PostCommentRule.objects.create(store=self.store, platform="facebook", post_id="post")
        self.comment()

        self.assertIsNone(self.count(f"comment-store:{self.store.pk}"))
        self.assertFalse(self.patches["send_private_reply"].called)


class StoreQueueRoutingTests(SimpleTestCase):
    STORE_TASKS = ("products.tasks.process_incoming_message", "products.tasks.process_incoming_attachment",
                   "products.tasks.process_comment_task")

    def route(self, name, args=(), kwargs=None):
        from perfume_ai.celery import app
        return app.amqp.router.route({}, name, args, kwargs or {})["queue"].name

    def test_each_store_id_maps_to_a_stable_queue_and_the_first_32_never_share(self):
        self.assertEqual(len({store_queue(i) for i in range(STORE_QUEUE_COUNT)}), STORE_QUEUE_COUNT)
        self.assertEqual(store_queue(5), store_queue("5"))
        self.assertEqual(store_queue(STORE_QUEUE_COUNT), store_queue(0))

    def test_every_store_task_dispatch_lands_on_its_stores_queue(self):
        for name in self.STORE_TASKS:
            with self.subTest(name=name):
                self.assertEqual(self.route(name, (5, "whatsapp", "x")), "store-5")
                self.assertEqual(self.route(name, (), {"store_id": 7}), "store-7")

    def test_other_tasks_stay_on_the_default_queue(self):
        self.assertEqual(self.route("products.tasks.retry_order_notifications"), "celery")

    def test_every_queue_has_its_own_routing_key(self):
        """A bare Queue(name) inherits the default routing key, so every queue is bound to the
        same key and a message is dropped or copied into all of them."""
        queues = {q.name: q for q in settings.CELERY_TASK_QUEUES}
        self.assertEqual(list(queues), all_queue_names())
        for name, queue in queues.items():
            self.assertEqual(queue.routing_key, name)
            self.assertEqual(queue.exchange.name, "celery")

    def test_the_worker_consumes_every_queue(self):
        """`-Q celery` on the worker would leave every store queue unconsumed."""
        procfile = (Path(settings.BASE_DIR) / "Procfile").read_text()
        worker = next(line for line in procfile.splitlines() if line.startswith("worker:"))
        self.assertNotIn(" -Q", worker)
        self.assertNotIn("--queues", worker)
