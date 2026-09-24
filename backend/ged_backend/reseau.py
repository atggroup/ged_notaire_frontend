"""Détermination de l'adresse réelle du client, derrière le reverse proxy.

Pourquoi ce module existe : `X-Forwarded-For` **n'est pas une source de
confiance**. nginx y concatène ce que le navigateur a envoyé
(`$proxy_add_x_forwarded_for` vaut « $http_x_forwarded_for, $remote_addr »),
si bien qu'un client peut y écrire ce qu'il veut. Deux conséquences mesurées :

  * la limitation de débit repartait à zéro à chaque valeur différente —
    26 tentatives de connexion sans un seul 429 ;
  * l'adresse consignée dans le **journal d'audit** était celle que
    l'attaquant avait choisie. Pour une étude notariale, un journal dont
    l'adresse est dictée par l'intéressé ne prouve rien.

`X-Real-IP`, lui, est posé par nginx avec `proxy_set_header X-Real-IP
$remote_addr` : `proxy_set_header` **remplace** la valeur reçue, elle n'est
donc pas falsifiable par le client. C'est cet en-tête que l'on lit, et
uniquement lui, et uniquement s'il est explicitement déclaré de confiance.

`TRUSTED_CLIENT_IP_HEADER = ""` désactive toute confiance : on se rabat sur
`REMOTE_ADDR`. C'est le réglage à retenir si l'application est exposée sans
reverse proxy devant elle.
"""
import ipaddress

from django.conf import settings


def _valide(valeur: str | None) -> str | None:
    if not valeur:
        return None
    valeur = valeur.strip()
    try:
        ipaddress.ip_address(valeur)
    except ValueError:
        return None
    return valeur


def adresse_client(request) -> str | None:
    """Adresse IP du client, ou None si elle n'est pas déterminable.

    Ne lit jamais `X-Forwarded-For` : voir le module pour la raison.
    """
    entete = getattr(settings, "TRUSTED_CLIENT_IP_HEADER", "") or ""
    if entete:
        depuis_proxy = _valide(request.META.get(entete))
        if depuis_proxy:
            return depuis_proxy
    return _valide(request.META.get("REMOTE_ADDR"))
