import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from products.models import PostCommentRule, StoreSettings
from products.services.comment_filter import extract_post_id_from_url
from .auth_backend import StoreOwnerAuthentication

logger = logging.getLogger(__name__)


class PostRuleListCreateView(APIView):
    """List all post comment rules for the store, or create a new one."""
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store
        rules = PostCommentRule.objects.filter(store=store)
        return Response([
            {
                "id": rule.id,
                "platform": rule.platform,
                "post_id": rule.post_id,
                "label": rule.label,
                "is_active": rule.is_active,
                "created_at": rule.created_at.isoformat(),
            }
            for rule in rules
        ])

    def post(self, request):
        store = request.store
        platform = request.data.get("platform", "").strip()
        raw_input = request.data.get("post_id", "").strip()
        label = request.data.get("label", "").strip()

        if platform not in ("facebook", "instagram"):
            return Response({"error": "platform must be 'facebook' or 'instagram'."}, status=400)

        if not raw_input:
            return Response({"error": "الرابط أو الـ Post ID مطلوب."}, status=400)

        # Extract post ID from URL (or pass raw ID through unchanged)
        try:
            store_settings = store.settings
        except StoreSettings.DoesNotExist:
            store_settings = None

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

        return Response({
            "id": rule.id,
            "platform": rule.platform,
            "post_id": rule.post_id,
            "label": rule.label,
            "is_active": rule.is_active,
            "created_at": rule.created_at.isoformat(),
        }, status=201 if created else 200)


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
        return Response({
            "id": rule.id,
            "platform": rule.platform,
            "post_id": rule.post_id,
            "label": rule.label,
            "is_active": rule.is_active,
        })

    def delete(self, request, rule_id):
        rule = self._get_rule(request.store, rule_id)
        if not rule:
            return Response({"error": "Rule not found."}, status=404)

        rule.delete()
        return Response({"message": "Rule deleted."})
