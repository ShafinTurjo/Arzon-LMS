from django.contrib import admin
from .models import AuditEvent

# Routine changes go through the role-aware application and its audit trail.
@admin.register(AuditEvent)
class AuditAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'actor', 'entity', 'object_id', 'action']
    readonly_fields = ['created_at', 'actor', 'entity', 'object_id', 'action', 'before', 'after']
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
