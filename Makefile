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

test:
	docker compose exec -it django python ./manage.py test

migrate:
	docker compose exec -it django python ./manage.py migrate

lint:
	docker compose exec -it django black django_taskq/*py django_taskq/management/commands/*py
	docker compose exec -it django isort django_taskq/*py django_taskq/management/commands/*py
