Django Task Queue
=================

A short, simple, boring, and reliable Celery replacement for my Django projects.

I think this is just part of the Celery experience: every sufficiently frustrated user eventually writes their own task queue. This one is mine.

What it is:

- A way to run Django functions in the background
- A database-backed job queue (no broker)
- A system that uses your existing models for state and results
- A single dependency: Django

What it is not:

- A distributed computing framework
- An RPC system
- A workflow engine
- A replacement for Celery at scale
- A system for non-Django projects

* ETA (estimated time of arrival) is a fundamental feature and does not require caching tasks in memory or moving to a different queue.
* Retry support built-in (Celery-style)
* Database is the only backend. Successful tasks are removed from the database, the failed ones are kept for manual inspection. Tasks obey the same transaction rules as the rest of your models. No more ``transaction.on_commit``.
* Django Admin integration to view future and failed tasks and to restart or delete the failed ones.
* Tasks produce no result. When your task produces a valuable result, store it in your models.
* The Django 6.0 ``django.tasks`` API implemented on the same engine, for when the standardized API is preferred over the Celery one.

Installation
------------

Install the package:

.. code-block:: bash

  python -m pip install django-taskq


And add ``django_taskq`` to the ``INSTALLED_APPS``:

.. code-block:: python

  INSTALLED_APPS = [
      # ...
      "django_taskq",
  ]

Run the migrations to create the database tables and indexes:

.. code-block:: bash

  python manage.py migrate


Celery API
----------

The original API is the Celery-style ``shared_task`` with ``delay``, ``apply_async`` and ``s``. Just to make switching between implementations easier.

.. code-block:: python

  from django_taskq.celery import shared_task

  @shared_task(autoretry_for=(MyException,), retry_kwargs={"max_retries": 10, "countdown": 5})
  def my_task(foo, bar=None):
      ...

.. code-block:: python

  my_task.delay(1, bar=2)
  my_task.apply_async((1, 2))
  my_task.s(1, 2).apply_async()


To start a worker, run the management command:

.. code-block:: bash

   python manage.py taskq
   python manage.py taskq -Q default -l DEBUG


Tasks also return emulations of ``AsyncResult`` and ``EagerResult``. The main motivation is to provide a UUID of the scheduled task and to be able to revoke it before execution.

.. code-block:: python

   result = my_task.s(1, 2).apply_async(countdown=60)
   ...
   result.revoke()
   #
   result = my_task.s(1, 2).apply_async(countdown=60)
   store_task_id(result.id)
   ...
   AsyncResult(id=retrieve_task_id()).revoke()


It also obeys some of the Celery configuration parameters. ``CELERY_TASK_ALWAYS_EAGER`` in your Django settings will cause the task to be executed immediately and it might be useful in tests:

.. code-block:: python

  CELERY_TASK_ALWAYS_EAGER = True


And ``CELERY_TASK_EAGER_PROPAGATES`` will cause exceptions for eagerly executed tasks to be raised which is another feature often used in tests:

.. code-block:: python

  CELERY_TASK_EAGER_PROPAGATES = True


Django tasks API
----------------

Django 6.0 ships a standardized background tasks API (``django.tasks``) and it is implemented on the same engine: same table, same worker, same properties. On older Django versions install the ``django-tasks`` backport package, it provides the same namespace.

.. code-block:: python

  from django_taskq.tasks import task

  @task(queue_name="emails")
  def my_task(foo, bar=None):
      ...

  result = my_task.enqueue(1, bar=2)
  result.status  # TaskResultStatus.READY

  # defer
  my_task.using(run_after=datetime.timedelta(minutes=5)).enqueue(1, bar=2)

To enqueue after the current transaction commits, wrap ``enqueue()`` in ``transaction.on_commit()`` as usual.

Or point the ``TASKS`` setting at the backend and use the stock decorator:

.. code-block:: python

  TASKS = {"default": {"BACKEND": "django_taskq.tasks.DatabaseBackend"}}

  from django.tasks import task

  @task
  def my_task(foo, bar=None):
      ...

The differences from the Django contract, kept on purpose:

* Arguments are pickled, not JSON-serialized. Datetimes, model instances, and other picklable objects work.
* The worker deletes successful tasks so the queue cleans itself. A ``SUCCESSFUL`` status is never stored and ``get_result()`` raises ``TaskResultDoesNotExist`` for finished tasks. Use it to inspect pending, running, and failed tasks.
* Priorities are accepted and silently ignored, for the reasons explained below.
* Retries use the ``Retry`` exception or ``task.retry()`` inside the task.


NOT Celery API
--------------

This task queue is unintrusive, all you get is the execution of a function. How you organize the code after that is up to you.
There are no Celery bound tasks and task inheritance, naming, task requests, special logging, etc. You get the idea.

Retry can't change the args/kwargs. That is not a retry but a new task.

Tasks have no result. If you can wait for the result, you can execute the function directly.

No Redis, Flower, or Django:Celery integrations are needed.


Admin page
----------

The Django admin page shows tasks in the following groups:

- Failed tasks -- Tasks that failed after retries and countdowns. You should inspect them and remove them by hand or with a script. You can execute them again as well.
- Dirty tasks -- Tasks that got started but failed without reaching a final state due to killed processes or crashing machines. Review them and either delete or execute again.
- Active tasks -- Tasks being executed right now. You might catch some longer-running tasks here
- Pending tasks -- Tasks that should be executed now but are not due to lack of available workers. You might start some extra ones to catch up.
- Future tasks -- Tasks scheduled to be executed in the future.


Internals
---------

Adding a new task to the queue creates a new task model instance. When there is an active transaction, the task creation is atomic with the rest of the model updates: either all of that is persisted or none.

Executing a task is a bit more expensive:

1. A task is picked up from a queue and the state is updated to "started" within a single transaction. Think of it as taking a lease.
2. Python code is executed, and a background thread updates the "alive at" field every second ("a liveness probe").
3. Successful tasks are deleted from the table. Failed tasks are marked as such and retried (based on configuration).

This is a bit more expensive than necessary but:

* we can recognize running tasks - the task is "started" and the record is updated in the last few seconds. There is no need to guess the right lease timeout ahead of time.
* we can recognize "dirty" tasks that got killed or lost database connection in the middle without reaching a final state - the task is "started" and the record has not been updated for a while.

In an ideal world, tasks should be idempotent and it would be safe to retry "dirty" tasks automatically but things happen and I prefer to know which tasks crashed and double-check if some cleanup is necessary.


Performance
-----------

A single process can execute over 200 dummy tasks per second which is more than enough. After years of struggling with Celery, correctness, and observability are more important.
On the other hand, to handle more "tasks" you probably want to store many events not tasks, and have a single task that processes them in batches.

Known issues
------------

Tests checking for a specific query limit might fail because creating new tasks does queries as well.

Recipes
-------

*Exactly once, at most once, at least once, idempotency:*

Implementing these semantics presents too many design questions to answer *on the task level*. Instead, treat the tasks as function calls that are decoupled in time. We do not enforce these semantics on functions, we write code inside functions to perform the necessary checks.

Within the task do this:

1. Lock the application model
2. Check that all conditions still apply
3. Perform the action

*Task priorities:*

There are no priorities. Priority is a capacity problem, not a queue feature: if some work is urgent, give it its own queue and start more workers for it. If we cannot afford the workers, that's a resource problem no priority integer will fix.

So if you need priority or slow background tasks, just add them to another queue. Start as many processors for the queues as you want. Some of them might be idle but it's under your control, unlike trying to come up with a proper algorithm that prioritizes tasks and avoids starvation.

*Non-concurrent tasks:*

You have two options:

- Either synchronize on some database record by taking a lock and enforce it explicitly
- Or keep a dedicated queue with a single worker and have it implicitly

*Storing results:*

The task queue handles execution. The result lives in the database (or S3, or the cache): when the task finishes, it writes there and the caller polls the database, not the queue. This keeps the queue stateless and the data in a system designed for querying.

So instead of the task storing its results and returning that to the caller or triggering another task to process it either:

- Store the result directly in the target application model
- Call a function or another task to process the result **explicitly**

*Chaining tasks:*

Chaining is workflow orchestration, not a queue feature. The task queue runs individual steps; the workflow state decides which step runs next.

Keep a state machine in the database. Each task checks the state, does its work, and updates the state. The next task is triggered by the state change, not by the previous task. The steps stay decoupled, each one can be retried on its own, and the workflow state can be inspected with the same tools as the rest of the data.

*Scheduling tasks:*

Call a Python script from the Unix crontab. Use Kubernetes CronJobs.
Or build a simple Django command using the nice `schedule library <https://pypi.org/project/schedule/>`_.

*Scaling workers:*

Start multiple Docker containers, and start multiple Kubernetes pods/scale deployment. Or use something like ``supervisord`` to start multiple processes.

*Boosting performance:*

If you think you need to process 10,000 tasks per second, you probably need batching, streaming, or aggregation, not a faster task queue. 10,000 individual tasks per second is usually a design mistake, not a scalability requirement.

The patterns for high-volume work:

- Batching: collect 10,000 events and process them as a single task
- Streaming: read from a log or a queue continuously and process in chunks
- Aggregation: reduce events to a summary and act on the summary

None of these require a queue that handles 10,000 individual jobs. They require a consumer that reads efficiently and processes in bulk. So instead of executing thousands of tasks (function calls with specific arguments) consider recording thousands of events (a domain-specific model) and executing a task once in a while that processes all available events in bulk.

Or do not record any events at all. Schedule a task that queries models matching certain criteria and does the processing for all of them.

Celery scales to 10,000 tasks per second. django-taskq doesn't, because 10,000 individual tasks per second is usually a mistake. Batch them.
