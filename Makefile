PYTHON ?= python3
IMAGE  ?= autogitsync:latest
CONFIG ?= config/config.toml

.PHONY: help test build up down logs restart once dry-run check shell clean

help:
	@echo "make test      运行全部测试（需要 python3 + git）"
	@echo "make build     构建 docker 镜像"
	@echo "make up        启动服务（docker compose up -d）"
	@echo "make logs      查看日志"
	@echo "make down      停止服务"
	@echo "make check     校验配置并打印同步计划（容器内）"
	@echo "make dry-run   试运行一次，显示将要发生的变更"
	@echo "make once      立即同步一次"

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

check:
	docker compose run --rm --no-deps --entrypoint python autogitsync /app/main.py --check -c /config/config.toml

dry-run:
	docker compose run --rm --no-deps --entrypoint python autogitsync /app/main.py --dry-run -c /config/config.toml

once:
	docker compose run --rm --no-deps --entrypoint python autogitsync /app/main.py --once -c /config/config.toml

shell:
	docker compose run --rm --no-deps --entrypoint sh autogitsync

clean:
	rm -rf data/repo
