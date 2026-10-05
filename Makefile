.PHONY: install dev test lint format serve watch doctor docker

install:        ## instala com todos os recursos (web, PDF, voz offline)
	pip install -e ".[all]"

dev:            ## instala com ferramentas de desenvolvimento
	pip install -e ".[all,dev]"

test:
	pytest -q

lint:
	ruff check src tests
	ruff format --check src tests

format:
	ruff format src tests
	ruff check --fix src tests

serve:          ## interface web em http://127.0.0.1:8000
	tta serve

watch:          ## converte automaticamente o que for colocado em data/entrada
	tta watch

doctor:
	tta doctor

docker:         ## sobe a interface web e a pasta monitorada
	docker compose up -d --build
