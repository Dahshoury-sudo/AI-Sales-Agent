import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from products.models import PostCommentRule, StoreSettings
from products.services.comment_filter import (
    PLATFORMS, PostLookupError, extract_post_id_from_url, is_matchable, list_recent_posts,
)
from .auth_backend import StoreOwnerAuthentication

logger = logging.getLogger(__name__)


def _store_settings(store):
    try:
        return store.settings
    except StoreSettings.DoesNotExist:
        return None


def _rule_json(rule, store_settings):
    return {
        "id": rule.id,
        "platform": rule.platform,
        "post_id": rule.post_id,
        "label": rule.label,
        "is_active": rule.is_active,
        "created_at": rule.created_at.isoformat(),
        # False when the saved ID can never equal what the webhook sends (rules saved
        # before IDs were validated), so the dashboard can tell the owner to replace it.
        "matchable": is_matchable(rule.platform, rule.post_id, store_settings),
    }


class PostRuleListCreateView(APIView):
    """List all post comment rules for the store, or create a new one."""
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store
        store_settings = _store_settings(store)
        rules = PostCommentRule.objects.filter(store=store)
        return Response([_rule_json(rule, store_settings) for rule in rules])

    def post(self, request):
        store = request.store
        platform = request.data.get("platform", "").strip()
        raw_input = request.data.get("post_id", "").strip()
        label = request.data.get("label", "").strip()

        if platform not in PLATFORMS:
            return Response({"error": "platform must be 'facebook' or 'instagram'."}, status=400)

        if not raw_input:
            return Response({"error": "الرابط أو الـ Post ID مطلوب."}, status=400)

        # Extract post ID from a pasted link, or validate a raw / picked ID
        store_settings = _store_settings(store)
        post_id, error = extract_post_id_from_url(raw_input, platform, store_settings)
        if error:
            return Response({"error": error}, status=400)

        rule, created = PostCommentRule.objects.get_or_create(
            store=store,
            platform=platform,
            post_id=post_id,
            defaults={"label": label, "is_active": True},
        )

        if not created:
            # Rule already exists — update label and re-activate if needed
            rule.label = label or rule.label
            rule.is_active = True
            rule.save(update_fields=["label", "is_active"])

        return Response(_rule_json(rule, store_settings), status=201 if created else 200)


class RecentPostsView(APIView):
    """The store's recent Facebook posts or Instagram media, for picking one to block."""
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store
        platform = request.query_params.get("platform", "").strip()
        if platform not in PLATFORMS:
            return Response({"error": "platform must be 'facebook' or 'instagram'."}, status=400)

        try:
            posts, next_cursor = list_recent_posts(
                _store_settings(store), platform, after=request.query_params.get("after") or None,
            )
        except PostLookupError as exc:
            return Response({"error": exc.message}, status=exc.status)

        blocked = set(PostCommentRule.objects.filter(
            store=store, platform=platform, is_active=True,
        ).values_list("post_id", flat=True))
        return Response({
            "posts": [
                {
                    "id": post["id"],
                    "text": post["text"],
                    "image": post["image"],
                    "permalink": post["permalink"],
                    "created_at": post["created_at"],
                    "blocked": post["id"] in blocked,
                }
                for post in posts
            ],
            "next": next_cursor,
        })


class PostRuleDetailView(APIView):
    """Update or delete a single post comment rule."""
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def _get_rule(self, store, rule_id):
        try:
            return PostCommentRule.objects.get(id=rule_id, store=store)
        except PostCommentRule.DoesNotExist:
            return None

    def put(self, request, rule_id):
        rule = self._get_rule(request.store, rule_id)
        if not rule:
            return Response({"error": "Rule not found."}, status=404)

        if "label" in request.data:
            rule.label = request.data["label"]
        if "is_active" in request.data:
            rule.is_active = request.data["is_active"]

        rule.save()
        return Response(_rule_json(rule, _store_settings(request.store)))

    def delete(self, request, rule_id):
        rule = self._get_rule(request.store, rule_id)
        if not rule:
            return Response({"error": "Rule not found."}, status=404)

        rule.delete()
        return Response({"message": "Rule deleted."})
