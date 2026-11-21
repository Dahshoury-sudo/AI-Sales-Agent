"""Post blocklist end to end: dashboard rule → saved post_id → Meta comment webhook → reply or silence.

The webhook IDs follow Meta's formats: a Facebook feed event's `post_id` is PAGEID_POSTID, an
Instagram comment's `media.id` is the Graph API media ID. A rule only works if it holds exactly that.
"""
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from products import tasks
from products.models import PostCommentRule, Store, StoreSettings
from products.services import comment_filter

PAGE = "111"
IG = "17841400000000000"
SECRET = "secret"
TOKEN = "secret-page-token"
GRAPH_GET = "products.services.comment_filter.http_requests.get"


def graph_response(payload, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = payload
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status} for url ...access_token={TOKEN}",
                                                                   response=response)
    return response


class FakeGraph:
    """Stands in for requests.get against the Graph API: paged /posts and /media, plus the URL lookup."""

    def __init__(self, fb_posts=(), ig_media=(), lookup=None, status=200):
        self.fb_posts, self.ig_media, self.lookup, self.status = list(fb_posts), list(ig_media), lookup, status
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params))
        if self.status != 200:
            return graph_response({}, self.status)
        if url.endswith(("/posts", "/media")):
            items = self.fb_posts if url.endswith("/posts") else self.ig_media
            start, size = int(params.get("after") or 0), int(params["limit"])
            payload = {"data": items[start:start + size]}
            if start + size < len(items):
                payload["paging"] = {"cursors": {"after": str(start + size)}, "next": "https://graph.example/next"}
            return graph_response(payload)
        return graph_response(self.lookup or {})

    def listing_calls(self):
        return [call for call in self.calls if call[0].endswith(("/posts", "/media"))]


class PostFilterTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="filter-owner")
        self.store = Store.objects.create(name="Filter store", owner=self.owner)
        self.settings = StoreSettings.objects.create(
            store=self.store, facebook_page_id=PAGE, instagram_account_id=IG,
            meta_app_secret=SECRET, messenger_access_token=TOKEN, meta_access_token=TOKEN,
        )
        self.other_owner = User.objects.create_user(username="other-owner")
        self.other = Store.objects.create(name="Other store", owner=self.other_owner)
        StoreSettings.objects.create(store=self.other, facebook_page_id="999", instagram_account_id="888",
                                     meta_app_secret=SECRET, messenger_access_token="other-token")
        self.webhook = APIClient()
        self.dash = self.dashboard_client(self.owner)
        self.dispatch = self.enterContext(patch.object(tasks.process_comment_task, "apply_async"))

    @staticmethod
    def dashboard_client(user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        return client

    def send_comment(self, platform, post_id, parent=None):
        """POST a signed comment webhook; return True if a reply was dispatched."""
        self.dispatch.reset_mock()
        if platform == "facebook":
            value = {"item": "comment", "verb": "add", "comment_id": f"{post_id}_c", "post_id": post_id,
                     "from": {"id": "customer"}, "message": "بكام؟"}
            if parent:
                value["parent_id"] = parent
            payload = {"object": "page", "entry": [{"id": PAGE, "changes": [{"field": "feed", "value": value}]}]}
        else:
            payload = {"object": "instagram", "entry": [{"id": IG, "changes": [{"field": "comments", "value": {
                "id": "c1", "from": {"id": "customer"}, "text": "بكام؟", "media": {"id": post_id}}}]}]}
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = self.webhook.post(reverse("meta-webhook"), body, content_type="application/json",
                                     HTTP_X_HUB_SIGNATURE_256=signature)
        self.assertEqual(response.status_code, 200)
        return self.dispatch.called

    def add_rule(self, platform, value, graph=None, label="x"):
        graph = graph if graph is not None else FakeGraph()
        with patch(GRAPH_GET, side_effect=graph):
            return self.dash.post(reverse("dashboard-api-post-rules"),
                                  {"platform": platform, "post_id": value, "label": label}, format="json")


class BlocklistGateTests(PostFilterTestCase):
    def test_active_rule_silences_its_post_only(self):
        for platform, post in (("facebook", f"{PAGE}_222"), ("instagram", "18000000000000001")):
            with self.subTest(platform=platform):
                self.assertTrue(self.send_comment(platform, post))
                rule = PostCommentRule.objects.create(store=self.store, platform=platform, post_id=post)
                self.assertFalse(self.send_comment(platform, post))
                self.assertTrue(self.send_comment(platform, post + "9"))
                rule.is_active = False
                rule.save()
                self.assertTrue(self.send_comment(platform, post))
                rule.delete()

    def test_reply_under_a_comment_on_a_blocked_post_is_silenced_too(self):
        PostCommentRule.objects.create(store=self.store, platform="facebook", post_id=f"{PAGE}_222")
        self.assertFalse(self.send_comment("facebook", f"{PAGE}_222", parent=f"{PAGE}_222_c0"))

    def test_rule_is_scoped_to_its_store_and_platform(self):
        PostCommentRule.objects.create(store=self.other, platform="facebook", post_id=f"{PAGE}_222")
        self.assertTrue(self.send_comment("facebook", f"{PAGE}_222"))
        PostCommentRule.objects.create(store=self.store, platform="instagram", post_id=f"{PAGE}_223")
        self.assertTrue(self.send_comment("facebook", f"{PAGE}_223"))
        PostCommentRule.objects.create(store=self.store, platform="facebook", post_id="18000000000000002")
        self.assertTrue(self.send_comment("instagram", "18000000000000002"))

    def test_rule_added_after_dispatch_still_stops_the_queued_reply(self):
        self.assertTrue(self.send_comment("facebook", f"{PAGE}_222"))
        args = self.dispatch.call_args.kwargs["args"]
        PostCommentRule.objects.create(store=self.store, platform="facebook", post_id=f"{PAGE}_222")
        with patch("products.tasks.fetch_post_content") as fetch, patch("products.tasks.route") as route, \
             patch("products.tasks.send_private_reply") as private:
            tasks.process_comment_task(*args)
        fetch.assert_not_called()
        route.assert_not_called()
        private.assert_not_called()


class DashboardRuleTests(PostFilterTestCase):
    def assertBlocks(self, platform, value, webhook_id, graph=None):
        PostCommentRule.objects.all().delete()
        response = self.add_rule(platform, value, graph)
        self.assertEqual(response.status_code, 201, (value, response.content))
        self.assertEqual(response.json()["post_id"], webhook_id, value)
        self.assertTrue(response.json()["matchable"])
        self.assertFalse(self.send_comment(platform, webhook_id), f"{value} did not block {webhook_id}")

    def assertRejected(self, platform, value, message, graph=None):
        PostCommentRule.objects.all().delete()
        response = self.add_rule(platform, value, graph)
        self.assertEqual(response.status_code, 400, (value, response.content))
        self.assertEqual(response.json()["error"], message, value)
        self.assertFalse(PostCommentRule.objects.exists(), value)

    def test_pause_resume_delete_and_readd(self):
        response = self.add_rule("facebook", "https://www.facebook.com/perfamix/posts/222")
        rule_url = reverse("dashboard-api-post-rule-detail", args=[response.json()["id"]])
        self.assertFalse(self.send_comment("facebook", f"{PAGE}_222"))
        self.dash.put(rule_url, {"is_active": False}, format="json")
        self.assertTrue(self.send_comment("facebook", f"{PAGE}_222"))
        self.dash.put(rule_url, {"is_active": True}, format="json")
        self.assertFalse(self.send_comment("facebook", f"{PAGE}_222"))
        self.dash.put(rule_url, {"is_active": False}, format="json")
        self.assertEqual(self.add_rule("facebook", f"{PAGE}_222").status_code, 200)  # re-adding re-activates
        self.assertFalse(self.send_comment("facebook", f"{PAGE}_222"))
        self.dash.delete(rule_url)
        self.assertTrue(self.send_comment("facebook", f"{PAGE}_222"))

    def test_links_carrying_the_post_number_resolve_without_calling_meta(self):
        graph = FakeGraph()
        for value in (
            "https://www.facebook.com/perfamix/posts/222",
            "https://www.facebook.com/perfamix/posts/222/?mibextid=abc",
            f"https://www.facebook.com/{PAGE}/posts/222",
            f"https://www.facebook.com/permalink.php?story_fbid=222&id={PAGE}",
            f"https://m.facebook.com/story.php?story_fbid=222&id={PAGE}",
            "https://web.facebook.com/perfamix/posts/222",
            "www.facebook.com/perfamix/posts/222",  # no scheme
            "facebook.com/perfamix/posts/222",
            f"{PAGE}_222",
            "222",  # bare post number gets the page prefix the webhook uses
        ):
            with self.subTest(value=value):
                self.assertBlocks("facebook", value, f"{PAGE}_222", graph)
        self.assertEqual(graph.calls, [])

    def test_photo_video_and_reel_links_resolve_to_the_post_not_the_media_id(self):
        graph = FakeGraph(fb_posts=[
            {"id": f"{PAGE}_800", "attachments": {"data": [{"target": {"id": "1"}}]}},
            {"id": f"{PAGE}_900", "attachments": {"data": [{"target": {"id": "222"}}]}},
        ])
        for value in ("https://www.facebook.com/photo/?fbid=222&set=a.9",
                      "https://www.facebook.com/photo.php?fbid=222",
                      "https://www.facebook.com/watch/?v=222",
                      "https://www.facebook.com/reel/222",
                      "https://www.facebook.com/perfamix/videos/222/",
                      "https://www.facebook.com/perfamix/videos/summer-launch/222/"):
            with self.subTest(value=value):
                self.assertBlocks("facebook", value, f"{PAGE}_900", graph)

    def test_photo_whose_post_shares_its_id_resolves(self):
        graph = FakeGraph(fb_posts=[{"id": f"{PAGE}_222"}])
        self.assertBlocks("facebook", "https://www.facebook.com/photo/?fbid=222", f"{PAGE}_222", graph)

    def test_pfbid_links_match_the_permalink_and_share_links_use_metas_lookup(self):
        graph = FakeGraph(fb_posts=[{"id": f"{PAGE}_900",
                                     "permalink_url": "https://www.facebook.com/perfamix/posts/pfbid02abcDEF"}],
                          lookup={"id": f"{PAGE}_901"})
        self.assertBlocks("facebook", "https://www.facebook.com/perfamix/posts/pfbid02abcDEF?mibextid=x",
                          f"{PAGE}_900", graph)
        self.assertBlocks("facebook", "https://www.facebook.com/share/p/1AbCdEf/", f"{PAGE}_901", graph)

    def test_lookup_stops_after_five_pages(self):
        graph = FakeGraph(fb_posts=[{"id": f"{PAGE}_{n}"} for n in range(1000, 1700)])
        self.assertRejected("facebook", "https://www.facebook.com/photo/?fbid=5", comment_filter.NOT_FOUND["facebook"],
                            graph)
        self.assertEqual(len(graph.listing_calls()), comment_filter.MAX_LOOKUP_PAGES)

    def test_inputs_that_could_never_match_are_rejected_instead_of_saved(self):
        not_found = comment_filter.NOT_FOUND["facebook"]
        cases = [
            ("photo not among recent posts", "https://www.facebook.com/photo/?fbid=222", not_found, FakeGraph()),
            ("lookup returns the URL node", "https://www.facebook.com/share/p/1AbCdEf/", not_found,
             FakeGraph(lookup={"id": "https://www.facebook.com/share/p/1AbCdEf/"})),
            ("lookup returns a bare object id", "https://www.facebook.com/share/p/1AbCdEf/", not_found,
             FakeGraph(lookup={"id": "222"})),
            ("another page's post id", "999_222", comment_filter.OTHER_PAGE, None),
            ("another page's post link", "https://www.facebook.com/999/posts/222", comment_filter.OTHER_PAGE, None),
            ("plain text", "بوست الصيف", comment_filter.NOT_A_POST["facebook"], None),
            ("instagram link on facebook", "https://www.instagram.com/p/AbC/", comment_filter.WRONG_PLATFORM["facebook"],
             None),
        ]
        for name, value, message, graph in cases:
            with self.subTest(name):
                self.assertRejected("facebook", value, message, graph)
        self.assertRejected("instagram", "https://www.facebook.com/perfamix/posts/222",
                            comment_filter.WRONG_PLATFORM["instagram"])
        self.assertRejected("instagram", "AbC_12-x", comment_filter.NOT_A_POST["instagram"])

    def test_unlinked_store_gets_the_setup_message(self):
        StoreSettings.objects.filter(store=self.store).update(facebook_page_id="", instagram_account_id="",
                                                              messenger_access_token="", meta_access_token="")
        self.assertRejected("facebook", "222", comment_filter.SETUP_NEEDED["facebook"])
        self.assertRejected("facebook", "https://www.facebook.com/photo/?fbid=222",
                            comment_filter.SETUP_NEEDED["facebook"])
        self.assertRejected("instagram", "https://www.instagram.com/p/AbC/", comment_filter.SETUP_NEEDED["instagram"])

    def test_meta_outage_during_lookup_is_reported(self):
        self.assertRejected("facebook", "https://www.facebook.com/photo/?fbid=222",
                            comment_filter.META_UNREACHABLE["facebook"], FakeGraph(status=500))

    def test_instagram_links_resolve_to_the_media_id(self):
        media_id = "18000000000000001"
        graph = FakeGraph(ig_media=[{"id": "1", "shortcode": "OTHER"}, {"id": media_id, "shortcode": "AbC_12-x"}])
        for value in ("https://www.instagram.com/p/AbC_12-x/",
                      "https://www.instagram.com/p/AbC_12-x/?igsh=xyz",
                      "https://www.instagram.com/reel/AbC_12-x/",
                      "https://www.instagram.com/reels/AbC_12-x/",
                      "https://www.instagram.com/perfamix/p/AbC_12-x/",
                      "instagram.com/p/AbC_12-x/",
                      media_id):
            with self.subTest(value=value):
                self.assertBlocks("instagram", value, media_id, graph)
        by_permalink = FakeGraph(ig_media=[{"id": media_id, "permalink": "https://www.instagram.com/reel/XyZ/"}])
        self.assertBlocks("instagram", "https://www.instagram.com/reels/XyZ/", media_id, by_permalink)

    def test_rule_list_flags_rules_that_can_never_match(self):
        for platform, post_id in (("facebook", f"{PAGE}_222"), ("facebook", "222"),
                                  ("facebook", "https://www.facebook.com/share/p/1/"), ("facebook", "999_1"),
                                  ("instagram", "18000000000000001"), ("instagram", "AbC")):
            PostCommentRule.objects.create(store=self.store, platform=platform, post_id=post_id)
        listed = {rule["post_id"]: rule["matchable"]
                  for rule in self.dash.get(reverse("dashboard-api-post-rules")).json()}
        self.assertEqual(listed, {f"{PAGE}_222": True, "222": False, "https://www.facebook.com/share/p/1/": False,
                                  "999_1": False, "18000000000000001": True, "AbC": False})


class RecentPostsViewTests(PostFilterTestCase):
    def recent(self, platform, graph, client=None, **params):
        with patch(GRAPH_GET, side_effect=graph):
            return (client or self.dash).get(reverse("dashboard-api-post-rules-recent"),
                                             {"platform": platform, **params})

    def test_facebook_posts_come_back_with_their_webhook_ids_and_blocked_flags(self):
        PostCommentRule.objects.create(store=self.store, platform="facebook", post_id=f"{PAGE}_1")
        PostCommentRule.objects.create(store=self.store, platform="facebook", post_id=f"{PAGE}_2", is_active=False)
        PostCommentRule.objects.create(store=self.other, platform="facebook", post_id=f"{PAGE}_3")
        graph = FakeGraph(fb_posts=[
            {"id": f"{PAGE}_1", "message": "عرض الصيف", "full_picture": "https://cdn.example/1.jpg",
             "permalink_url": "https://www.facebook.com/p/1", "created_time": "2026-11-20T10:00:00+0000",
             "attachments": {"data": [{"target": {"id": "77"}}]}},
            {"id": f"{PAGE}_2", "story": "Perfamix updated their cover photo."},
            {"id": f"{PAGE}_3"},
        ])
        response = self.recent("facebook", graph)
        self.assertEqual(response.status_code, 200)
        posts = response.json()["posts"]
        self.assertEqual(posts[0], {"id": f"{PAGE}_1", "text": "عرض الصيف", "image": "https://cdn.example/1.jpg",
                                    "permalink": "https://www.facebook.com/p/1",
                                    "created_at": "2026-11-20T10:00:00+0000", "blocked": True})
        self.assertEqual(posts[1]["text"], "Perfamix updated their cover photo.")
        self.assertEqual([p["blocked"] for p in posts], [True, False, False])
        self.assertIsNone(response.json()["next"])
        url, params = graph.calls[0]
        self.assertEqual(url, f"{comment_filter.GRAPH_URL}/{PAGE}/posts")
        self.assertEqual(params["access_token"], TOKEN)

    def test_instagram_media_uses_the_video_thumbnail(self):
        graph = FakeGraph(ig_media=[
            {"id": "5", "caption": "ريل", "media_type": "VIDEO", "media_url": "https://cdn.example/v.mp4",
             "thumbnail_url": "https://cdn.example/v.jpg", "timestamp": "2026-11-20T10:00:00+0000"},
            {"id": "6", "media_type": "IMAGE", "media_url": "https://cdn.example/i.jpg"},
            {"id": "7", "media_type": "VIDEO", "media_url": "https://cdn.example/v2.mp4"},
        ])
        posts = self.recent("instagram", graph).json()["posts"]
        self.assertEqual([p["image"] for p in posts], ["https://cdn.example/v.jpg", "https://cdn.example/i.jpg", ""])
        self.assertEqual(graph.calls[0][0], f"{comment_filter.GRAPH_URL}/{IG}/media")

    def test_cursor_is_passed_through_both_ways(self):
        graph = FakeGraph(fb_posts=[{"id": f"{PAGE}_{n}"} for n in range(60)])
        first = self.recent("facebook", graph).json()
        self.assertEqual((len(first["posts"]), first["next"]), (25, "25"))
        second = self.recent("facebook", graph, after=first["next"]).json()
        self.assertEqual(second["posts"][0]["id"], f"{PAGE}_25")
        third = self.recent("facebook", graph, after="50").json()
        self.assertEqual((len(third["posts"]), third["next"]), (10, None))

    def test_unlinked_store_gets_400_without_calling_meta(self):
        StoreSettings.objects.filter(store=self.store).update(messenger_access_token="", meta_access_token="")
        graph = FakeGraph()
        for platform in ("facebook", "instagram"):
            response = self.recent(platform, graph)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"], comment_filter.SETUP_NEEDED[platform])
        self.assertEqual(graph.calls, [])

    def test_meta_failure_is_502_and_the_token_stays_out_of_the_log(self):
        with self.assertLogs("products.services.comment_filter", "WARNING") as logs:
            response = self.recent("facebook", FakeGraph(status=400))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], comment_filter.META_UNREACHABLE["facebook"])
        self.assertNotIn(TOKEN, "\n".join(logs.output))

    def test_bad_platform_and_anonymous_requests_are_refused(self):
        self.assertEqual(self.recent("tiktok", FakeGraph()).status_code, 400)
        self.assertEqual(self.recent("facebook", FakeGraph(), client=APIClient()).status_code, 401)

    def test_each_owner_lists_their_own_store(self):
        graph = FakeGraph()
        self.recent("facebook", graph, client=self.dashboard_client(self.other_owner))
        url, params = graph.calls[0]
        self.assertEqual(url, f"{comment_filter.GRAPH_URL}/999/posts")
        self.assertEqual(params["access_token"], "other-token")
