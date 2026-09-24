from rest_framework import serializers
from ged_backend.referentiels import NATURE_LABELS, ORIGINE_LABELS, QUALITE_PARTIE_LABELS, SUPPORT_LABELS, TYPE_DOCUMENT_LABELS
from .models import Document


def contexte_liste(request, documents) -> dict:
    """Pré-calcule, en deux requêtes, ce que le serializer allait autrement
    chercher pièce par pièce.

    `is_favorite` déclenchait un SELECT par document et `has_access` jusqu'à
    trois : une liste de 80 pièces coûtait 82 requêtes, et le plafond de 200
    en coûtait plusieurs centaines. Les vues qui sérialisent une LISTE passent
    donc ce contexte ; le repli pièce par pièce reste en place pour les vues
    de détail, où il ne coûte rien.
    """
    from permissions_app.access import documents_visibles_par
    from .models import DocumentFavorite

    documents = list(documents)
    identifiants = [doc.pk for doc in documents]
    favoris = set(
        DocumentFavorite.objects.filter(user=request.user, document_id__in=identifiants)
        .values_list("document_id", flat=True)
    )
    if request.user.role == "admin":
        accessibles = set(identifiants)
    else:
        accessibles = set(
            documents_visibles_par(request.user, Document.objects.filter(pk__in=identifiants))
            .values_list("pk", flat=True)
        )
    return {"request": request, "favoris": favoris, "accessibles": accessibles, "liste": True}


class DocumentSerializer(serializers.ModelSerializer):
    is_favorite = serializers.SerializerMethodField()
    has_access = serializers.SerializerMethodField()
    fileUrl = serializers.SerializerMethodField()
    dossierReference = serializers.SerializerMethodField()
    uploadedByName = serializers.SerializerMethodField()
    idMaitre = serializers.SerializerMethodField()
    codeNotarialActuel = serializers.SerializerMethodField()
    typeCodeLabel = serializers.SerializerMethodField()
    origineLabel = serializers.SerializerMethodField()
    supportLabel = serializers.SerializerMethodField()
    natureLabel = serializers.SerializerMethodField()
    qualitePartieLabel = serializers.SerializerMethodField()
    statutLabel = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ["reference", "master_reference", "previous_version", "is_current", "code_notarial", "codeNotarialActuel", "idMaitre", "dossierReference", "type", "type_code", "typeCodeLabel", "origine", "origineLabel", "support", "supportLabel", "nature", "natureLabel", "qualite_partie", "qualitePartieLabel", "nom", "original_filename", "content_type", "size_bytes", "sha256", "extracted_text", "ocr_status", "ocr_processed_at", "ocr_error", "ocr_attempts", "valid_until", "niveau_de_confidentialite", "statut", "statutLabel", "version", "quality_checked_by", "quality_checked_at", "quality_passed", "quality_notes", "uploaded_by", "uploadedByName", "validated_by", "validated_at", "is_archived", "trashed_at", "destruction_requested_at", "destruction_authorized_at", "destroyed_at", "destroyed_by", "is_favorite", "has_access", "fileUrl", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.context.get("liste"):
            # Le texte OCR intégral d'un acte n'a rien à faire dans une LISTE :
            # 200 pièces renvoyaient 200 actes en clair (poids et exposition),
            # sans que l'interface s'en serve. Le champ n'est même pas lu (les
            # vues le diffèrent en SQL) ; `ocr_status` indique s'il existe. Il
            # reste servi sur la fiche d'une pièce, que l'utilisateur est
            # habilité à ouvrir.
            self.fields.pop("extracted_text", None)

    def get_is_favorite(self, obj):
        favoris = self.context.get("favoris")
        if favoris is not None:
            return obj.pk in favoris
        request = self.context.get("request")
        return bool(request and obj.favorites.filter(user=request.user).exists())

    def get_has_access(self, obj):
        accessibles = self.context.get("accessibles")
        if accessibles is not None:
            return obj.pk in accessibles
        request = self.context.get("request")
        if not request or not getattr(request, "user", None):
            return False
        from permissions_app.access import has_document_access
        return has_document_access(request.user, obj)

    def get_fileUrl(self, obj):
        request = self.context.get("request")
        path = f"/api/documents/{obj.reference}"
        return request.build_absolute_uri(path) if request else path

    def get_dossierReference(self, obj):
        return obj.dossier.reference if obj.dossier else None

    def get_uploadedByName(self, obj):
        return obj.uploaded_by.display_name if obj.uploaded_by_id else None

    def get_idMaitre(self, obj):
        return obj.id_maitre

    def get_codeNotarialActuel(self, obj):
        return obj.code_notarial_actuel()

    def get_statutLabel(self, obj):
        """Libellé humain du statut. Sans lui, l'interface n'a d'autre choix
        que d'afficher la valeur technique brute (« en_validation »,
        « destruction_demandée ») — tous les autres référentiels exposent
        déjà leur libellé."""
        return obj.get_statut_display()

    def get_typeCodeLabel(self, obj):
        return TYPE_DOCUMENT_LABELS.get(obj.type_code)

    def get_origineLabel(self, obj):
        return ORIGINE_LABELS.get(obj.origine)

    def get_supportLabel(self, obj):
        return SUPPORT_LABELS.get(obj.support)

    def get_natureLabel(self, obj):
        return NATURE_LABELS.get(obj.nature)

    def get_qualitePartieLabel(self, obj):
        return QUALITE_PARTIE_LABELS.get(obj.qualite_partie)
