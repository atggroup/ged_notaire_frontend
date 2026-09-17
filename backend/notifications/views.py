from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from audit.services import log_event
from ged_backend.api import ContractSerializer
from .models import Notification
from .models import Task
from accounts.models import User
from dossiers.models import Dossier
from django.utils.dateparse import parse_datetime
from django.utils import timezone


class APIView(GenericAPIView):
    serializer_class = ContractSerializer


class NotificationsView(APIView):
    def get(self, request):
        items = Notification.objects.filter(recipient=request.user).order_by("-created_at")
        return Response([
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "read": n.read,
                "createdAt": n.created_at.isoformat(),
            }
            for n in items
        ])


class MarkNotificationReadView(APIView):
    def patch(self, request):
        qs = Notification.objects.filter(recipient=request.user)
        if request.data.get("id"): qs = qs.filter(pk=request.data["id"])
        elif request.data.get("title"): qs = qs.filter(title=request.data["title"])
        else: qs = qs.filter(read=False)
        count = qs.update(read=True)
        log_event(request, "notification_read", "notification", str(request.data.get("id", "")))
        return Response({"ok": True, "updated": count})


class UnreadNotificationsCountView(APIView):
    def get(self, request):
        count = Notification.objects.filter(recipient=request.user, read=False).count()
        return Response({"count": count})


def task_payload(task):
    return {"id": task.id, "title": task.title, "description": task.description,
            "assignedTo": task.assigned_to.display_name, "assignedBy": task.assigned_by.display_name,
            "dossier": task.dossier.reference if task.dossier_id else None, "dueAt": task.due_at.isoformat() if task.due_at else None,
            "reminderAt": task.reminder_at.isoformat() if task.reminder_at else None,
            "priority": task.priority, "status": task.status, "createdAt": task.created_at.isoformat(),
            "completedAt": task.completed_at.isoformat() if task.completed_at else None}


class TasksView(APIView):
    def get(self, request):
        tasks = Task.objects.filter(assigned_to=request.user).select_related("assigned_to", "assigned_by", "dossier").order_by("due_at", "-created_at")
        if request.user.role == "admin" and request.query_params.get("scope") == "cabinet":
            tasks = Task.objects.select_related("assigned_to", "assigned_by", "dossier").order_by("due_at", "-created_at")
        return Response([task_payload(task) for task in tasks])

    def post(self, request):
        if request.user.role not in {"admin", "clerc"}:
            return Response({"detail": "Création de tâche non autorisée."}, status=403)
        assignee_id = request.data.get("assignedTo") or request.data.get("assigned_to")
        assignee = User.objects.filter(pk=assignee_id).first() if assignee_id else request.user
        if not assignee or not str(request.data.get("title", "")).strip():
            return Response({"detail": "Titre et destinataire valides requis."}, status=400)
        dossier_ref = request.data.get("dossier")
        dossier = Dossier.objects.filter(reference=dossier_ref).first() if dossier_ref else None
        if dossier_ref and not dossier:
            return Response({"dossier": ["Dossier introuvable."]}, status=400)
        due_at = parse_datetime(request.data.get("dueAt", "")) if request.data.get("dueAt") else None
        reminder_at = parse_datetime(request.data.get("reminderAt", "")) if request.data.get("reminderAt") else None
        if due_at and timezone.is_naive(due_at): due_at = timezone.make_aware(due_at)
        if reminder_at and timezone.is_naive(reminder_at): reminder_at = timezone.make_aware(reminder_at)
        if reminder_at and due_at and reminder_at > due_at:
            return Response({"reminderAt": ["Le rappel ne peut pas être postérieur à l'échéance."]}, status=400)
        priority = request.data.get("priority", Task.Priority.NORMAL)
        if priority not in Task.Priority.values:
            return Response({"priority": ["Priorité invalide."]}, status=400)
        task = Task.objects.create(title=str(request.data["title"])[:255], description=str(request.data.get("description", "")), assigned_to=assignee, assigned_by=request.user, dossier=dossier, due_at=due_at, reminder_at=reminder_at, priority=priority)
        Notification.objects.create(recipient=assignee, type="task", title="Nouvelle tâche", message=f"{task.title}{' — échéance : ' + task.due_at.strftime('%d/%m/%Y') if task.due_at else ''}")
        log_event(request, "task_created", "task", str(task.pk))
        return Response(task_payload(task), status=201)

    def patch(self, request):
        task = Task.objects.filter(pk=request.data.get("id"), assigned_to=request.user).first()
        if not task:
            return Response({"detail": "Tâche introuvable."}, status=404)
        new_status = request.data.get("status")
        if new_status not in Task.Status.values:
            return Response({"status": ["Statut invalide."]}, status=400)
        task.status = new_status
        task.completed_at = timezone.now() if new_status == Task.Status.DONE else None
        task.save(update_fields=["status", "completed_at"])
        log_event(request, "task_updated", "task", str(task.pk))
        return Response(task_payload(task))
