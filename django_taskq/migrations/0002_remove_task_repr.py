# Generated by Django 5.0 on 2025-02-17 19:29

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("taskq", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="task",
            name="repr",
        ),
    ]
