PYTHON ?= python3
IMAGE  ?= autogitsync:latest

.PHONY: help test build up down logs restart trigger once dry-run check shell

help:
	@echo "make test      run the test suite (needs python3 + git)"
	@echo "make build     build a local development image"
	@echo "make up        start the published image (no local build)"
	@echo "make logs      follow the logs"
	@echo "make down      stop the service (keep its data volume)"
	@echo "make trigger   request a sync from the running service"
	@echo "make check     print the effective configuration and sync plan"
	@echo "make dry-run   preview changes; stop the service first"
	@echo "make once      sync once and exit; stop the service first"

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

build:
	docker build -t $(IMAGE) .

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=100

# Control the running daemon over its internal endpoint; no second work copy user.
trigger:
	docker compose exec -T autogitsync python /app/main.py --trigger

# Read-only inspection can run while the daemon is active.
check:
	docker compose run --rm --no-deps autogitsync --check

# Both offline commands mutate the work copy and respect the daemon's instance lock.
dry-run:
	docker compose run --rm --no-deps autogitsync --dry-run

once:
	docker compose run --rm --no-deps autogitsync --once

shell:
	docker compose run --rm --no-deps --entrypoint sh autogitsync
