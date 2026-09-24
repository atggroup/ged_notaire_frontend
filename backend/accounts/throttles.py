"""Limitation de débit.

Deux régimes distincts, choisis requête par requête par `EtudeRateThrottle`
(la classe par défaut de toute l'API) :

- **routes publiques** (connexion, second facteur, inscription, mot de passe
  oublié, Google) : budget serré **par adresse IP et par route** — c'est la
  protection anti-force-brute ;
- **utilisateur authentifié** : budget large **par compte**. Auparavant, le
  budget anti-force-brute (20/min par adresse et par route) s'appliquait à
  toute l'API : derrière le NAT d'une étude, tous les postes partagent une
  adresse, et quelques collaborateurs suffisaient à recevoir des 429 en
  travaillant normalement.

Toute nouvelle route publique (`AllowAny`) hérite automatiquement du régime
strict : l'oubli d'une déclaration ne peut pas ouvrir une porte.

Les compteurs vivent dans le cache partagé (base de données), commun à tous
les processus gunicorn : un cache local à chaque processus multipliait le
budget réel par le nombre de processus.
"""
from rest_framework import permissions
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle

from ged_backend.reseau import adresse_client


class AuthRateThrottle(SimpleRateThrottle):
    """Régime strict des routes publiques : par adresse et par route."""
    scope = "auth"

    def get_rate(self):
        # Lu à chaque appel (et non figé à l'import) : les réglages restent
        # modifiables par configuration, y compris dans les tests.
        return api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)

    def get_ident(self, request):
        """Identité du client pour la limitation de débit.

        L'implémentation de DRF se rabat sur `X-Forwarded-For`, que nginx
        concatène avec ce que le navigateur a envoyé : il suffisait de faire
        varier l'en-tête pour repartir à zéro (mesuré : 26 tentatives de
        connexion, aucun 429). On passe par `ged_backend.reseau`, qui ne lit
        qu'un en-tête explicitement déclaré de confiance et posé par notre
        propre proxy.
        """
        return adresse_client(request) or "inconnu"

    def get_cache_key(self, request, view):
        # Rate-limit each authentication operation independently.  A user
        # completing a legitimate multi-step sign-in must not exhaust the
        # login budget merely by requesting an OTP or finishing Google OAuth.
        return f"auth:{view.__class__.__name__}:{self.get_ident(request)}"


def route_publique(view) -> bool:
    return any(p is permissions.AllowAny for p in getattr(view, "permission_classes", []))


class EtudeRateThrottle(AuthRateThrottle):
    """Classe par défaut : régime strict sur les routes publiques, budget par
    compte pour les utilisateurs connectés."""

    def allow_request(self, request, view):
        connecte = getattr(request, "user", None) is not None and request.user.is_authenticated
        self.scope = "user" if connecte and not route_publique(view) else "auth"
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        if self.scope == "user":
            return f"user:{request.user.pk}"
        return super().get_cache_key(request, view)
