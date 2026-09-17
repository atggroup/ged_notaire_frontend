from rest_framework import serializers
from ged_backend.referentiels import NATURE_LABELS, ORIGINE_LABELS, QUALITE_PARTIE_LABELS, SUPPORT_LABELS, TYPE_DOCUMENT_LABELS
from .models import Document


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

    class Meta:
        model = Document
        fields = ["reference", "master_reference", "previous_version", "is_current", "code_notarial", "codeNotarialActuel", "idMaitre", "dossierReference", "type", "type_code", "typeCodeLabel", "origine", "origineLabel", "support", "supportLabel", "nature", "natureLabel", "qualite_partie", "qualitePartieLabel", "nom", "original_filename", "content_type", "size_bytes", "sha256", "extracted_text", "ocr_status", "ocr_processed_at", "ocr_error", "niveau_de_confidentialite", "statut", "version", "quality_checked_by", "quality_checked_at", "quality_passed", "quality_notes", "uploaded_by", "uploadedByName", "validated_by", "validated_at", "is_archived", "trashed_at", "destruction_requested_at", "destruction_authorized_at", "destroyed_at", "destroyed_by", "is_favorite", "has_access", "fileUrl", "created_at", "updated_at"]

    def get_is_favorite(self, obj):
        request = self.context.get("request")
        return bool(request and obj.favorites.filter(user=request.user).exists())

    def get_has_access(self, obj):
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
