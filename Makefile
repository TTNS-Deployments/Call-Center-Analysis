# Common tasks. Run `make help` for the list.
PY ?= .venv/bin/python
WEB = Code/webapp
ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: help setup seed run worker test package reset docker-up docker-down

help:            ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup:           ## Create .venv and install everything
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements-dev.txt

seed:            ## Generate and analyse 40 sample calls (local mode)
	$(PY) -m callcenter seed --count 40

run:             ## Start the web app on http://localhost:8000
	cd $(WEB) && ../../$(PY) manage.py runserver 0.0.0.0:8000

worker:          ## Watch the recordings folder and process dropped-in files
	$(PY) -m callcenter worker

test:            ## Run all tests
	$(PY) -m pytest Code/core -q
	cd $(WEB) && ../../$(PY) manage.py test

package:         ## Build build/webapp.zip and build/functions.zip
	Deployment/scripts/package.sh

reset:           ## Delete all local data
	$(PY) -m callcenter reset

docker-up:       ## Run web app + Azurite in Docker, with sample data
	docker compose up --build -d
	docker compose run --rm seed

docker-down:     ## Stop Docker services
	docker compose down
