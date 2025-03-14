up:
	docker compose up

upd:
	docker compose up -d

pgtail:
	MSYS_NO_PATHCONV=1 docker compose exec -it postgres tail -n 0 -f /tmp/postgresql.log

pgshell:
	docker compose exec -it postgres bash -c 'PGPASSWORD=postgres psql -U postgres -h localhost -d postgres'	

bash:
	docker compose exec -it django bash

admin:
	docker compose exec -it django python ./manage.py migrate
	docker compose exec -it django python ./manage.py collectstatic --noinput
	docker compose exec -it django python ./manage.py runserver 0.0.0.0:8000

test:
	docker compose exec -it django python ./manage.py test

migrate:
	docker compose exec -it django python ./manage.py migrate

lint:
	docker compose exec -it django black django_taskq/*py django_taskq/management/commands/*py
	docker compose exec -it django isort django_taskq/*py django_taskq/management/commands/*py
	docker compose exec -it django black django_taskq/*pyi
	docker compose exec -it django isort django_taskq/*pyi
