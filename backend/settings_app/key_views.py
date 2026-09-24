import json
from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.generics import GenericAPIView
from ged_backend.api import ContractSerializer
from audit.services import log_event
from .key_management import key_status, generate_key, recovery_export, recovery_import

class APIView(GenericAPIView): serializer_class = ContractSerializer

def admin(request):
    return request.user.role == "admin"

class KeyStatusView(APIView):
    def get(self, request):
        if not admin(request): return Response({"detail":"Réservé au notaire."}, status=403)
        return Response(key_status())

class GenerateKeyView(APIView):
    def post(self, request):
        if not admin(request): return Response({"detail":"Réservé au notaire."}, status=403)
        try: key_id, value = generate_key(request.data.get("keyId"))
        except ValueError as exc: return Response({"detail":str(exc)}, status=400)
        log_event(request, "encryption_key_generated", "encryption_key", key_id)
        return Response({"keyId":key_id,"key":value,"warning":"Conservez cette clé dans le secret-store du serveur, puis ajoutez-la à DOCUMENT_ENCRYPTION_KEYS avant de l'activer."}, status=201)

class RetireKeyView(APIView):
    def post(self, request):
        if not admin(request): return Response({"detail":"Réservé au notaire."}, status=403)
        key_id=str(request.data.get("keyId", "")); st=key_status()
        if key_id == st.get("activeKeyId"): return Response({"detail":"Impossible de retirer la clé active. Faites d'abord une rotation."}, status=409)
        if key_id not in st.get("knownKeyIds", []): return Response({"detail":"Clé inconnue."}, status=404)
        if st["documentReferences"].get(key_id,0): return Response({"detail":"Clé encore référencée par des documents ; elle doit rester disponible.","references":st["documentReferences"][key_id]}, status=409)
        log_event(request, "encryption_key_retirement_approved", "encryption_key", key_id)
        return Response({"ok":True,"keyId":key_id,"instruction":"Supprimez maintenant cette clé du secret-store/environnement après vérification du paquet de récupération."})

class RecoveryExportView(APIView):
    def post(self, request):
        if not admin(request): return Response({"detail":"Réservé au notaire."}, status=403)
        try: blob=recovery_export(str(request.data.get("passphrase","")))
        except ValueError as exc: return Response({"detail":str(exc)}, status=400)
        # Les identifiants (jamais les clés) sont tracés : le contrôle de
        # séquestre vérifie ainsi que chaque clé en service a bien été exportée.
        from documents.crypto import _keyring
        log_event(request,"encryption_keys_recovery_exported","encryption","recovery", metadata={"keyIds": sorted(_keyring())})
        r=HttpResponse(blob, content_type="application/json"); r["Content-Disposition"]='attachment; filename="ged-key-recovery.json"'; return r

class RecoveryImportView(APIView):
    def post(self, request):
        if not admin(request): return Response({"detail":"Réservé au notaire."}, status=403)
        upload=request.FILES.get("package"); passphrase=str(request.data.get("passphrase",""))
        if not upload: return Response({"detail":"Le paquet de récupération est obligatoire."}, status=400)
        try: data=recovery_import(upload.read(), passphrase)
        except ValueError as exc: return Response({"detail":str(exc)}, status=400)
        log_event(request,"encryption_keys_recovery_imported","encryption","recovery", metadata={"keyIds":data["keyIds"]})
        return Response(data)

class ActivateKeyView(APIView):
    def post(self, request):
        if not admin(request): return Response({"detail":"Réservé au notaire."}, status=403)
        key_id=str(request.data.get("keyId", ""))
        if key_id not in key_status().get("knownKeyIds", []): return Response({"detail":"La clé doit d'abord être présente dans le secret-store du serveur."}, status=409)
        from .models import CabinetSettings
        item,_=CabinetSettings.objects.get_or_create(pk=1)
        item.data={**item.data,"active_key_id":key_id}; item.save(update_fields=["data","updated_at"])
        log_event(request,"encryption_key_activated","encryption_key",key_id)
        return Response({"ok":True,"activeKeyId":key_id})
