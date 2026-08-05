import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from products.models import StaticFAQ
from .auth_backend import StoreOwnerAuthentication

logger = logging.getLogger(__name__)


class FAQListCreateView(APIView):
    """GET: list all FAQs for the store. POST: create a new FAQ."""
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store
        faqs = StaticFAQ.objects.filter(store=store).order_by('-priority', 'id')

        data = []
        for faq in faqs:
            data.append({
                "id": faq.id,
                "question": faq.question,
                "keywords": faq.keywords,
                "answer": faq.answer,
                "priority": faq.priority,
                "is_active": faq.is_active,
                "created_at": faq.created_at.isoformat(),
            })

        return Response({"faqs": data})

    def post(self, request):
        store = request.store
        question = request.data.get("question", "").strip()
        keywords = request.data.get("keywords", "").strip()
        answer = request.data.get("answer", "").strip()
        priority = request.data.get("priority", 0)
        is_active = request.data.get("is_active", True)

        if not question or not keywords or not answer:
            return Response(
                {"error": "السؤال والكلمات المفتاحية والرد مطلوبين"},
                status=400
            )

        try:
            priority = int(priority)
        except (ValueError, TypeError):
            priority = 0

        faq = StaticFAQ.objects.create(
            store=store,
            question=question,
            keywords=keywords,
            answer=answer,
            priority=priority,
            is_active=is_active,
        )

        return Response({
            "id": faq.id,
            "question": faq.question,
            "keywords": faq.keywords,
            "answer": faq.answer,
            "priority": faq.priority,
            "is_active": faq.is_active,
            "created_at": faq.created_at.isoformat(),
        }, status=201)


class FAQDetailView(APIView):
    """PUT: update a FAQ. DELETE: delete a FAQ."""
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def put(self, request, faq_id):
        store = request.store

        try:
            faq = StaticFAQ.objects.get(id=faq_id, store=store)
        except StaticFAQ.DoesNotExist:
            return Response({"error": "السؤال غير موجود"}, status=404)

        question = request.data.get("question", "").strip()
        keywords = request.data.get("keywords", "").strip()
        answer = request.data.get("answer", "").strip()
        priority = request.data.get("priority", faq.priority)
        is_active = request.data.get("is_active", faq.is_active)

        if not question or not keywords or not answer:
            return Response(
                {"error": "السؤال والكلمات المفتاحية والرد مطلوبين"},
                status=400
            )

        try:
            priority = int(priority)
        except (ValueError, TypeError):
            priority = 0

        faq.question = question
        faq.keywords = keywords
        faq.answer = answer
        faq.priority = priority
        faq.is_active = is_active
        faq.save()

        return Response({
            "id": faq.id,
            "question": faq.question,
            "keywords": faq.keywords,
            "answer": faq.answer,
            "priority": faq.priority,
            "is_active": faq.is_active,
            "created_at": faq.created_at.isoformat(),
        })

    def delete(self, request, faq_id):
        store = request.store

        try:
            faq = StaticFAQ.objects.get(id=faq_id, store=store)
        except StaticFAQ.DoesNotExist:
            return Response({"error": "السؤال غير موجود"}, status=404)

        faq.delete()
        return Response({"status": "تم حذف السؤال بنجاح"})
