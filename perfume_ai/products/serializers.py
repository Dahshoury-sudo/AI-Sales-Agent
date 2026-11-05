from rest_framework import serializers
from .models import Product
from .scent_tags import normalize_scent_tags
from django.core.exceptions import ValidationError


class ProductSerializer(serializers.ModelSerializer):
    brand = serializers.StringRelatedField()
    category = serializers.StringRelatedField()

    def validate_scent_tags(self, value):
        try:
            return normalize_scent_tags(value)
        except ValidationError as exc:
            raise serializers.ValidationError(exc.messages)

    class Meta:
        model = Product
        fields = "__all__"
