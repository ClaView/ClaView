# Common commands (Git Bash / Linux / macOS). PowerShell users: run the commands on the right directly.
COMPOSE      = docker compose
COMPOSE_DEV  = docker compose -f docker-compose.yaml -f docker-compose.local.yaml
COMPOSE_EVAL = docker compose -f docker-compose.yaml -f docker-compose.eval.yaml
PY ?= python
export PYTHONHASHSEED = 0
export GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

.PHONY: test-c test-common test-frontend env up local down logs ps test test-a test-a-tl test-b test-pipeline test-api lint eval-pipeline

env:            ; test -f .env || cp .env.example .env
up: env         ; $(COMPOSE) up -d --build
local: env      ; $(COMPOSE_DEV) up --build
down:           ; $(COMPOSE) down
logs:           ; $(COMPOSE) logs -f --tail=100
ps:             ; $(COMPOSE) ps

# Offline test suites, one pytest process each (README 3).
test:           ; $(PY) pipeline/scripts/check.py
test-a:         ; PYTHONPATH=modules/a $(PY) -B -X utf8 -m pytest modules/a/tests -q -p no:cacheprovider
test-a-tl:      ; PYTHONPATH=modules/a $(PY) -B -X utf8 -m pytest modules/a/tests_tl -q -p no:cacheprovider
test-b:         ; PYTHONPATH=common:modules/b $(PY) -B -X utf8 -m pytest modules/b/tests -q -p no:cacheprovider
test-c:         ; PYTHONPATH=common:modules/c $(PY) -B -X utf8 -m pytest modules/c/tests -q -p no:cacheprovider
test-pipeline:  ; PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline $(PY) -B -X utf8 -m pytest pipeline/tests -q -p no:cacheprovider
test-api:       ; $(PY) pipeline/scripts/check.py --suite common --suite a-api --suite b-api --suite pipeline-api
lint:           ; $(PY) -m ruff check . && PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline lint-imports

eval-pipeline: env ; $(COMPOSE_EVAL) run --rm eval-pipeline

test-common:    ; PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline $(PY) -B -m pytest common/tests -q -p no:cacheprovider
test-frontend:  ; node --test frontend/tests/*.cjs
