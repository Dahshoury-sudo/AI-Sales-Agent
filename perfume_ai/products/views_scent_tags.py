from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from dashboard.auth_backend import StoreOwnerAuthentication
from .services.scent_tag_import import export_scent_tags, import_scent_tags


class ScentTagImportView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        response = HttpResponse(export_scent_tags(request.store), content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        response["Content-Disposition"] = 'attachment; filename="perfume_scent_labels.xlsx"'
        return response

    def post(self, request):
        file = request.FILES.get("file")
        if not file or not file.name.lower().endswith(".xlsx"):
            return Response({"error": "ارفع ملف Excel بصيغة .xlsx."}, status=400)
        result = import_scent_tags(file.read(), request.store)
        return Response(result, status=400 if result["errors"] else 200)
