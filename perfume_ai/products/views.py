from .services.router import route


class ChatAPIView(APIView):

    def post(self, request):

        message = request.data.get("message")

        if not message:
            return Response(
                {"error": "message is required"},
                status=400
            )

        try:

            reply = route(message)

            return Response({
                "reply": reply
            })

        except Exception as e:

            return Response(
                {"error": str(e)},
                status=500
            )