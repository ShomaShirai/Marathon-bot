SERVICE ?= api
message ?= update database schema

.PHONY: build up dev down logs shell db-revision db-upgrade db-downgrade

build:
	docker compose build

up:
	docker compose up --build -d

dev:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f $(SERVICE)

shell:
	docker compose run --rm $(SERVICE) sh

db-revision:
	docker compose run --rm $(SERVICE) alembic revision --autogenerate -m "$(message)"

db-upgrade:
	docker compose run --rm $(SERVICE) alembic upgrade head

db-downgrade:
	docker compose run --rm $(SERVICE) alembic downgrade -1
