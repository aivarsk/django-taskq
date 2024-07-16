from django.apps import AppConfig


class TaskQConfig(AppConfig):
    name = "django_taskq"
    label = "taskq"
    verbose_name = "Task Queue"
    default_auto_field = "django.db.models.BigAutoField"
