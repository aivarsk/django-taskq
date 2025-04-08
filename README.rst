Django Task Queue
=================

A short, simple, boring, and reliable Celery replacement for my Django projects.

* ETA (estimated time of arrival) is a fundamental feature and does not require caching tasks in memory or moving to a different queue.
* Database is the only backend. Successful tasks are removed from the database, the failed ones are kept for manual inspection. Tasks obey the same transaction rules as the rest of your models. No more ``transaction.on_commit``.
* Django Admin integration to view future and failed tasks and to restart or delete the failed ones.
* Tasks produce no result. When your task produces a valuable result, store it in your models.

Installation
------------

Install the package:

.. code-block:: bash
  
  python -m pip install django-taskq


And add the ``django_taskq`` to the ``INSTALLED_APPS``:

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

The main API is the Celery API (``shared_task``) with ``delay``, ``apply_async`` and ``s``. Just to make switching between implementations easier.

.. code-block:: python
  
  from django_taskq.celery import shared_task

  @shared_task(autoretry_for(MyException,), retry_kwargs={"max_retries": 10, "countdown": 5})
  def my_task(foo, bar=None):
      ...

.. code-block:: python
  
  my_task.delay(1,bar=2)
  my_task.appy_async((1,2))
  my_task.s(1,2).apply_async()


To start a worker just running the management command:

.. code-block:: bash

   python manage.py taskq
   python manage.py taskq -Q default -l DEBUG


Tasks also return emulations of ``AsyncResult`` and ``EagerResult``. The main motivation is to provide a UUID of the scheduled task and to be able to revoke it before execution.

.. code-block:: python

   result = my_task.s(1,2).apply_async(countdown=60)
   ...
   result.revoke()
   #
   result = my_task.s(1,2).apply_async(countdown=60)
   store_task_id(result.id)
   ...
   AsyncResult(id=retrieve_task_id()).revoke()

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

Adding a new task to the queue creates a new task model instance.

Executing a task is a bit more expensive:

1. A task is picked up from a queue and the state is updated to "started" within a single transaction.
2. Python code is executed, and a background thread updates the "alive at" field every second ("a liveness probe").
3. Successful tasks are deleted from the table. Failed tasks are marked as such and retried (based on configuration).

This is a bit more expensive than necessary but:

* we can recognize running tasks - the task is "started" and the record is updated in the last few seconds.
* we can recognize "dirty" tasks that got killed or lost database connection in the middle - the task is "started" and the record has not been updated for a while.

In an ideal world, tasks should be idempotent but things happen and I prefer to know which tasks crashed and double-check if some cleanup is necessary.


Performance
-----------

A single process can execute around 150 dummy tasks per second which is more than enough. After years of struggling with Celery, correctness, and observability are more important.
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

There are no priorities. If you need priority or slow background tasks, just add them to another queue. Start as many processors for the queues as you want.
Some of them might be idle but it's under your control unlike trying to come up with a proper algorithm that prioritizes tasks and avoids starvation.

*Non-concurrent tasks:*

You have two options:

- Either synchronize on some database record by taking a lock and enforce it explicitly
- Or keep a dedicated queue with a single worker and have it implicitly

*Storing results:*

Instead of the task storing its results and returning that to the caller or triggering another task to process it either:

- Store the result directly in the target application model
- Call a function or another task to process the result **explicitly**

*Scheduling tasks:*

Call a Python script from the Unix crontab. Use Kubernetes CronJobs.
Or build a simple Django command using the nice `schedule library <https://pypi.org/project/schedule/>`_.

*Scaling workers:*

Start multiple Docker containers, and start multiple Kubernetes pods/scale deployment. Or use something like ``supervisord`` to start multiple processes.

*Boosting performance:*

Instead of executing thousands of tasks (function calls with specific arguments) consider recording thousands of events (domain-specific model) and executing a task once in a while that processes all available events in bulk.

Or do not record any events, just schedule a task that queries models matching certain criteria and does processing for all of them.

