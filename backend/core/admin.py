from django.contrib import admin

from .models import JobRun, WorkerHeartbeat


@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "trigger", "started_at", "finished_at", "items", "attempt")
    list_filter = ("status", "name", "trigger")
    readonly_fields = [f.name for f in JobRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(WorkerHeartbeat)
class WorkerHeartbeatAdmin(admin.ModelAdmin):
    list_display = ("name", "host", "groups", "last_seen")
