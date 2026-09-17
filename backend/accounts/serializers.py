"""Serializers for the front-end authentication contract."""
from django.contrib.auth import authenticate
from rest_framework import serializers
from .models import User


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)

    def validate(self, attrs):
        user = authenticate(email=attrs["email"].lower(), password=attrs["password"])
        if user is None or not user.is_active:
            raise serializers.ValidationError("Identifiants invalides.")
        attrs["user"] = user
        return attrs


class RegisterStartSerializer(serializers.Serializer):
    # The role is checked against the invitation server-side; it is never a
    # privilege claim made by the browser.
    role = serializers.ChoiceField(choices=User.Role.choices)
    inviteCode = serializers.CharField(max_length=96)
    firstName = serializers.CharField(max_length=150)
    lastName = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    jobTitle = serializers.CharField(max_length=150, required=False, allow_blank=True)


class PasswordSerializer(serializers.Serializer):
    password = serializers.CharField(min_length=8, trim_whitespace=False)

    def validate_password(self, value: str) -> str:
        if not (any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value)):
            raise serializers.ValidationError("Le mot de passe doit contenir majuscule, minuscule et chiffre.")
        return value
