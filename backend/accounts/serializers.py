"""Serializers for the front-end authentication contract."""
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
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
    """Politique de mot de passe des comptes de l'étude.

    Ce serializer est le SEUL contrôle traversé par l'activation d'un compte et
    par la réinitialisation. Tant qu'il n'appelait pas les validateurs de
    Django, `AUTH_PASSWORD_VALIDATORS` — dont la liste des mots de passe les
    plus courants — était de la configuration morte : « Password1 », « Azerty12 »
    ou « Abcd1234 » satisfaisaient la règle maison (majuscule + minuscule +
    chiffre) et ouvraient l'accès aux minutes de l'étude.
    """

    password = serializers.CharField(min_length=12, trim_whitespace=False)

    def validate_password(self, value: str) -> str:
        if not (any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value)):
            raise serializers.ValidationError("Le mot de passe doit contenir majuscule, minuscule et chiffre.")
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value
