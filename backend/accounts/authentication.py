"""JWT authentication with immediate server-side session revocation."""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class SessionVersionJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if validated_token.get("sv") != user.session_version:
            raise AuthenticationFailed("Session révoquée. Veuillez vous reconnecter.", code="session_revoked")
        return user
