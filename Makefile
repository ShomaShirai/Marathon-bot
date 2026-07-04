SERVICE ?= api

.PHONY: build up dev down logs shell

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
