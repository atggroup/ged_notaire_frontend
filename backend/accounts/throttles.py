from rest_framework.throttling import SimpleRateThrottle


class AuthRateThrottle(SimpleRateThrottle):
    scope = "auth"

    def get_cache_key(self, request, view):
        # Rate-limit each authentication operation independently.  A user
        # completing a legitimate multi-step sign-in must not exhaust the
        # login budget merely by requesting an OTP or finishing Google OAuth.
        return f"auth:{view.__class__.__name__}:{self.get_ident(request)}"
