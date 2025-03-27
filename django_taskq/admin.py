from django.contrib import admin, messages

from django_taskq.models import (
    ActiveTask,
    DirtyTask,
    FailedTask,
    FutureTask,
    PendingTask,
    Task,
)


@admin.register(PendingTask, ActiveTask, FutureTask)
class PendingTaskAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "execute_at",
        "queue",
        "func",
        "args",
        "kwargs",
        "retries",
        "error",
    )
    list_filter = (
        "queue",
        "func",
        ("execute_at", admin.DateFieldListFilter),
        ("created_at", admin.DateFieldListFilter),
        ("expires_at", admin.DateFieldListFilter),
    )
    readonly_fields = [
        field.name
        for field in Task._meta.get_fields()
        if field.name not in ("started", "failed")
    ]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DirtyTask, FailedTask)
class RestartableTaskAdmin(PendingTaskAdmin):
    list_filter = PendingTaskAdmin.list_filter + (
        ("alive_at", admin.DateFieldListFilter),
    )
    actions = ("force_retry",)

    @admin.action(description="Retry selected tasks")
    def force_retry(self, request, queryset):
        count = 0
        for task in queryset.iterator():
            count += 1
            task.force_retry()
        self.message_user(
            request,
            f"{count} task(s) will be retried",
            messages.SUCCESS,
        )
