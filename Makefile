# Common commands (Git Bash / Linux / macOS). PowerShell users: run the commands on the right directly.
COMPOSE      = docker compose
COMPOSE_DEV  = docker compose -f docker-compose.yaml -f docker-compose.local.yaml
COMPOSE_EVAL = docker compose -f docker-compose.yaml -f docker-compose.eval.yaml
PY ?= python
export PYTHONHASHSEED = 0
export GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

.PHONY: env up local down logs ps test test-a test-b test-pipeline test-api lint eval-pipeline

env:            ; test -f .env || cp .env.example .env
up: env         ; $(COMPOSE) up -d --build
local: env      ; $(COMPOSE_DEV) up --build
down:           ; $(COMPOSE) down
logs:           ; $(COMPOSE) logs -f --tail=100
ps:             ; $(COMPOSE) ps

# Offline test suites, one pytest process each (README 3).
test: test-a test-b test-c test-pipeline test-api
test-a:         ; PYTHONPATH=modules/a $(PY) -B -X utf8 -m pytest modules/a/tests_legacy -q -p no:cacheprovider
test-b:         ; PYTHONPATH=common:modules/b $(PY) -B -X utf8 -m pytest modules/b/tests -q -p no:cacheprovider
test-c:         ; PYTHONPATH=common:modules/c $(PY) -B -X utf8 -m pytest modules/c/tests -q -p no:cacheprovider
test-pipeline:  ; PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline $(PY) -B -X utf8 -m pytest pipeline/tests -q -p no:cacheprovider
test-api:       ; PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline $(PY) -B -X utf8 -m pytest common/tests modules/a/tests_api modules/b/tests_api pipeline/tests_api -q -p no:cacheprovider
lint:           ; $(PY) -m ruff check common modules/*/claview_*_api pipeline/claview_pipeline/api.py && PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline lint-imports

eval-pipeline: env ; $(COMPOSE_EVAL) run --rm eval-pipeline
