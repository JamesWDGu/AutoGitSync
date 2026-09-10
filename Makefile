PYTHON ?= python3
IMAGE  ?= autogitsync:latest

.PHONY: help test build up down logs restart once dry-run check shell clean

help:
	@echo "make test      run the test suite (needs python3 + git)"
	@echo "make build     build the docker image"
	@echo "make up        start the service (docker compose up -d)"
	@echo "make logs      follow the logs"
	@echo "make down      stop the service"
	@echo "make check     print the effective configuration and sync plan"
	@echo "make dry-run   show what a sync would change, without committing"
	@echo "make once      sync once right now"

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

build:
	docker build -t $(IMAGE) .

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=100

# the three targets below reuse the compose environment
check:
	docker compose run --rm --no-deps --entrypoint python autogitsync /app/main.py --check

dry-run:
	docker compose run --rm --no-deps --entrypoint python autogitsync /app/main.py --dry-run

once:
	docker compose run --rm --no-deps --entrypoint python autogitsync /app/main.py --once

shell:
	docker compose run --rm --no-deps --entrypoint sh autogitsync

clean:
	rm -rf data/repo
