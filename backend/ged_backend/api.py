"""Shared base objects for explicit contract API views."""
from rest_framework import serializers


class ContractSerializer(serializers.Serializer):
    """Empty fallback for endpoints with hand-authored response bodies."""
