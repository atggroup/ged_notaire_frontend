"""En-têtes de sécurité complémentaires à ceux que Django pose lui-même.

Django couvre déjà `X-Frame-Options`, `X-Content-Type-Options`, HSTS et le
referrer. Il ne fournit pas de Content-Security-Policy : c'est l'objet de ce
module.

Pourquoi elle compte ici : l'interface construit son HTML par `innerHTML` à
peu près partout. L'échappement est correct aujourd'hui — vérifié en déposant
des charges actives dans les noms de dossiers, de clients et de documents,
qui s'affichent bien comme du texte — mais une seule omission suffirait à
ouvrir une brèche sur des pages qui détiennent un jeton d'accès à l'ensemble
des actes de l'étude. La CSP est le filet sous ce fil.

`script-src 'self'` est posé **sans** `unsafe-inline` : c'est la directive qui
protège réellement. Les quelques scripts et gestionnaires en ligne qui
existaient ont été déplacés dans les fichiers .js pour le permettre. Les
attributs `style=` restent nombreux dans les gabarits, d'où le maintien de
`unsafe-inline` pour les styles seuls — sans conséquence pour l'exécution de
code.
"""

CSP = "; ".join([
    "default-src 'self'",
    # Aucune exception : plus aucun script en ligne ni CDN tiers. pdf.js est
    # servi par l'application (assets/vendor/).
    "script-src 'self'",
    "worker-src 'self' blob:",
    # Les gabarits portent ~550 attributs style= ; Google Fonts sert la
    # feuille de style des polices.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    # data: pour les miniatures et les rendus canvas ; blob: pour les
    # aperçus de documents déchiffrés en mémoire.
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "connect-src 'self'",
    # L'application n'est jamais censée être encadrée ni encadrer.
    "frame-ancestors 'none'",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


class SecurityHeadersMiddleware:
    """Ajoute la CSP et quelques en-têtes que Django ne pose pas."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", CSP)
        # Coupe l'accès aux capteurs et périphériques : une GED n'en a aucun
        # besoin, et le scanner passe par un dépôt de fichier classique.
        response.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        )
        # Empêche une page tierce de garder une référence à la fenêtre ouverte
        # (notamment lors du retour de connexion Google).
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        return response
