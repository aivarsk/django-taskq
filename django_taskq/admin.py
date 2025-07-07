from django.contrib import admin, messages
from django.db import models
from django.http import HttpResponseRedirect
from django.urls import re_path, reverse
from django.utils.html import format_html

from .models import (
    ActiveTask,
    DirtyTask,
    FailedTask,
    FutureTask,
    PendingTask,
    Task,
    TaskSummary,
)


@admin.register(TaskSummary)
class TaskSummaryAdmin(admin.ModelAdmin):
    change_list_template = "admin/task_summary_change_list.html"

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(
            request,
            extra_context=extra_context,
        )

        try:
            qs = response.context_data["cl"].queryset
        except (AttributeError, KeyError):
            return response

        count = lambda q: models.Sum(models.Case(models.When(q, then=1)))

        response.context_data["summary"] = list(
            qs.values("func")
            .annotate(
                failed_count=count(FailedTask.objects._filter()),
                dirty_count=count(DirtyTask.objects._filter()),
                active_count=count(ActiveTask.objects._filter()),
                pending_count=count(PendingTask.objects._filter()),
                future_count=count(FutureTask.objects._filter()),
            )
            .order_by("func")
        )

        return response

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PendingTask, ActiveTask, FutureTask)
class PendingTaskAdmin(admin.ModelAdmin):
    list_display = (
        "func",
        "arguments",
        "formatted_execute_at",
        "retries",
        "error",
        "queue",
    )
    list_display_links = ("func", "arguments")
    list_filter = (
        "queue",
        "func",
        ("execute_at", admin.DateFieldListFilter),
        ("created_at", admin.DateFieldListFilter),
        ("expires_at", admin.DateFieldListFilter),
        ("alive_at", admin.DateFieldListFilter),
    )
    readonly_fields = tuple(
        [
            field.name
            for field in Task._meta.get_fields()
            if field.name not in ("started", "failed")
        ]
        + ["formatted_execute_at", "error"]
    )

    def has_add_permission(self, request, obj=None):
        return False

    def formatted_execute_at(self, obj):
        return obj.execute_at.strftime("%Y-%m-%d %H:%M:%S")

    formatted_execute_at.admin_order_field = "execute_at"
    formatted_execute_at.short_description = "Execute At"


@admin.register(DirtyTask, FailedTask)
class RestartableTaskAdmin(PendingTaskAdmin):
    actions = ("force_retry",)

    list_display = (
        "func",
        "arguments",
        "task_actions",
        "formatted_execute_at",
        "retries",
        "error",
        "queue",
    )
    readonly_fields = PendingTaskAdmin.readonly_fields + ("task_actions",)

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

    def retry_one(self, request, task_id, *args, **kwargs):
        self.force_retry(request, Task.objects.filter(pk=task_id))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    def get_urls(self):
        return [
            re_path(
                r"^taskq/(?P<task_id>[0-9a-f-]+)/retry$",
                self.admin_site.admin_view(self.retry_one),
                name="taskq-retry",
            ),
        ] + super().get_urls()

    def task_actions(self, obj):
        return format_html(
            '<a class="button" href="{}">Retry now</a>',
            reverse("admin:taskq-retry", kwargs={"task_id": obj.id}),
        )
