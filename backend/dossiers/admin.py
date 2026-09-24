from django.contrib import admin

from .models import ChecklistTemplate


@admin.register(ChecklistTemplate)
class ChecklistTemplateAdmin(admin.ModelAdmin):
    list_display = ("domaine", "label", "type_code", "required", "reminder_days", "order", "active")
    list_filter = ("domaine", "active", "required")
    list_editable = ("required", "order", "active")
    search_fields = ("label",)
