from django.contrib import admin, messages

from django_taskq.models import (
    ActiveTask,
    DirtyTask,
    FailedTask,
    FutureTask,
    PendingTask,
)


@admin.register(PendingTask, ActiveTask, FutureTask)
class PendingTaskAdmin(admin.ModelAdmin):
    fields = ("id", "execute_at", "queue", "repr", "created_at")
    list_display = ("id", "execute_at", "queue", "func", "args", "kwargs")
    readonly_fields = (
        "id",
        "execute_at",
        "queue",
        "func",
        "args",
        "kwargs",
        "created_at",
    )
    list_filter = (
        "queue",
        "func",
        ("execute_at", admin.DateFieldListFilter),
        ("created_at", admin.DateFieldListFilter),
    )

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DirtyTask, FailedTask)
class RestartableTaskAdmin(admin.ModelAdmin):
    fields = (
        "id",
        "execute_at",
        "queue",
        "func",
        "args",
        "kwargs",
        "retries",
        "created_at",
        "alive_at",
        "traceback",
    )
    list_display = ("id", "execute_at", "queue", "repr", "retries")
    readonly_fields = (
        "id",
        "execute_at",
        "func",
        "retries",
        "created_at",
        "alive_at",
        "traceback",
        "args",
        "kwargs",
    )
    list_filter = (
        "queue",
        "func",
        ("execute_at", admin.DateFieldListFilter),
        ("created_at", admin.DateFieldListFilter),
        ("alive_at", admin.DateFieldListFilter),
    )
    actions = ("force_retry",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return True

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
