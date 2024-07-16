import threading
import time

from django.core.management.base import BaseCommand

from django_taskq.models import Retry, Task


class Heartbeat(threading.Thread):
    def __init__(self, command):
        super().__init__()
        self.finished = threading.Event()
        self.command = command

    def run(self):
        previous = None
        while not self.finished.is_set():
            self.finished.wait(1)
            current = self.command.task_id

            # Skip the first second update
            # TODO: make this smarter
            if current and current == previous:
                Task.alive(current)
                self.command.stdout.write(
                    self.command.style.SUCCESS(f"Task({current}) alive")
                )
            previous = current

    def cancel(self):
        self.finished.set()


class Command(BaseCommand):
    help = "Process tasks from a queue specified by -Q or 'default'"
    task_id = None

    def add_arguments(self, parser):
        parser.add_argument("-Q", action="store", dest="queue_name", help="Queue name")

    def handle(self, *_, **options):
        heartbeat = Heartbeat(self)
        heartbeat.start()
        try:
            while True:
                task = Task.next_task(queue=options.get("queue_name"))
                if not task:
                    self.stdout.write(self.style.SUCCESS("No new tasks"))
                    time.sleep(1)
                else:
                    self._execute_one(task)
        finally:
            heartbeat.cancel()

    def _execute_one(self, task):
        self.stdout.write(self.style.SUCCESS(f"Processing Task({task.pk}) {task.repr}"))

        try:
            self.task_id = task.id
            task.execute()

            self.stdout.write(self.style.SUCCESS(f"Completed Task({task.pk})"))
            task.delete()
        except Retry as retry:
            self.stdout.write(self.style.ERROR(f"Failed Task({task.pk}), will retry"))
            task.retry(retry)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Failed Task({task.pk}): {exc!r}"))
            task.fail(exc)
        finally:
            self.task_id = None
