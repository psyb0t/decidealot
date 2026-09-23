SHELL := /bin/bash
.DEFAULT_GOAL := help

DEV_IMAGE := psyb0t/decidealot-dev
CPU_IMAGE := psyb0t/decidealot
CUDA_IMAGE := psyb0t/decidealot
MIN_TEST_COVERAGE := 90
TAG = $(shell awk -F\" '/^version *= */ {print "v" $$2; exit}' pyproject.toml)
UID := $(shell id -u)
GID := $(shell id -g)
DOCKER_SOCK := /var/run/docker.sock
DOCKER_GID := $(shell stat -c '%g' $(DOCKER_SOCK) 2>/dev/null || echo 0)
DEPENDENCY_CUTOFF := 2026-09-16T13:10:08Z
MODEL_EXCEPTION_CUTOFF := 2026-09-23T13:10:08Z

DEV_RUN := docker run --rm --init --user $(UID):$(GID) -e HOME=/tmp \
	-e PYTHONPATH=/work/src -e VIRTUAL_ENV=/opt/venv -e PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
	-v $(CURDIR):/work -w /work $(DEV_IMAGE)
DEV_TOOL_RUN := docker run --rm --init --user $(UID):$(GID) -e HOME=/tmp \
	-v $(CURDIR):/work -w /work $(DEV_IMAGE):tools
DEV_RUN_DIND := docker run --rm --init --user $(UID):$(GID) \
	--group-add $(DOCKER_GID) -e HOME=/tmp -e PYTHONPATH=$(CURDIR)/src -e VIRTUAL_ENV=/opt/venv -e PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
	-v $(CURDIR):$(CURDIR) -w $(CURDIR) \
	-v $(DOCKER_SOCK):$(DOCKER_SOCK) $(DEV_IMAGE)

.PHONY: help dev-image dev-tools-image shell pkg-lock model-lock dep format lint lint-fix audit sec test test-unit test-integration test-coverage test-real test-real-cuda generate build build-cuda build-all build-test build-test-cuda run run-cuda restart restart-cuda stop status audit-compose audit-compose-cuda version clean

help: ## List supported operations
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "%-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dev-image: ## Build the isolated development toolchain
	docker build -f Dockerfile.dev -t $(DEV_IMAGE) .

dev-tools-image: ## Build the lockfile-only development toolchain
	docker build --target tools -f Dockerfile.dev -t $(DEV_IMAGE):tools .

shell: dev-image ## Open an interactive development shell
	docker run --rm -it --init --user $(UID):$(GID) -e HOME=/tmp -e PYTHONPATH=/work/src -e VIRTUAL_ENV=/opt/venv -e PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin -v $(CURDIR):/work -w /work $(DEV_IMAGE) bash

pkg-lock: dev-tools-image ## Regenerate the age-gated application dependency lock
	$(DEV_TOOL_RUN) bash -ceu 'uv lock --exclude-newer $(DEPENDENCY_CUTOFF)'

model-lock: dev-tools-image ## Generate hash-locked CPU and CUDA model dependency files
	$(DEV_TOOL_RUN) bash -ceu 'uv pip compile requirements-laya-cpu.in --output-file requirements-laya-cpu.txt --generate-hashes --quiet --torch-backend cpu --exclude-newer $(DEPENDENCY_CUTOFF) --exclude-newer-package laya=$(MODEL_EXCEPTION_CUTOFF)'
	$(DEV_TOOL_RUN) bash -ceu 'uv pip compile requirements-von-cpu.in --output-file requirements-von-cpu.txt --generate-hashes --quiet --torch-backend cpu --exclude-newer $(DEPENDENCY_CUTOFF) --exclude-newer-package von-sdk=$(MODEL_EXCEPTION_CUTOFF)'
	$(DEV_TOOL_RUN) bash -ceu 'uv pip compile requirements-laya-cuda.in --output-file requirements-laya-cuda.txt --generate-hashes --quiet --torch-backend cu126 --exclude-newer $(DEPENDENCY_CUTOFF) --exclude-newer-package laya=$(MODEL_EXCEPTION_CUTOFF)'
	$(DEV_TOOL_RUN) bash -ceu 'uv pip compile requirements-von-cuda.in --output-file requirements-von-cuda.txt --generate-hashes --quiet --torch-backend cu126 --exclude-newer $(DEPENDENCY_CUTOFF) --exclude-newer-package von-sdk=$(MODEL_EXCEPTION_CUTOFF)'

dep: pkg-lock model-lock ## Refresh every locked dependency artifact

format: dev-image ## Format source
	$(DEV_RUN) python -m ruff format src tests scripts

lint: dev-image ## Run all static checks
	$(DEV_RUN) bash -ceu 'python -m ruff check src tests scripts && python -m pyright && python -m mypy src tests && python -m bandit -q -r src && shellcheck scripts/*.sh'

lint-fix: format ## Apply safe formatter fixes
	@$(MAKE) lint

test: test-unit test-integration ## Run the normal complete test suite

test-unit: dev-image ## Run the unit suite
	$(DEV_RUN) python -m pytest -q --ignore=tests/integration --ignore=tests/real

test-integration: dev-image ## Run local provider-process integration tests
	$(DEV_RUN) python -m pytest -q -m integration tests/integration

test-coverage: dev-image ## Enforce coverage and write the badge input
	$(DEV_RUN) bash -ceu 'COVERAGE_MINIMUM="$(MIN_TEST_COVERAGE)" bash scripts/test-coverage.sh'

test-real: build dev-image ## Run requests against downloaded CPU Laya and Von weights
	$(DEV_RUN_DIND) bash scripts/test-real.sh

test-real-cuda: build-cuda dev-image ## Run requests against downloaded CUDA Laya and Von weights
	$(DEV_RUN_DIND) bash scripts/test-real.sh --cuda

generate: dev-image ## Regenerate every owned artifact
	$(DEV_RUN) python scripts/generate.py

build: ## Build the CPU production image
	docker build -f Dockerfile -t $(CPU_IMAGE):local -t $(CPU_IMAGE):$(TAG) -t $(CPU_IMAGE):latest .

build-cuda: ## Build the CUDA production image
	docker build -f Dockerfile.cuda -t $(CUDA_IMAGE):local-cuda -t $(CUDA_IMAGE):$(TAG)-cuda -t $(CUDA_IMAGE):latest-cuda .

build-all: build build-cuda ## Build both production images

build-test: build ## Import the CPU image and verify its immutable variant metadata
	docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=8m --entrypoint python $(CPU_IMAGE):local -c 'from decidealot.settings import Settings; assert Settings().image_variant == "cpu"'

build-test-cuda: build-cuda ## Import the CUDA image and verify its immutable variant metadata
	docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=8m --entrypoint python $(CUDA_IMAGE):local-cuda -c 'from decidealot.settings import Settings; assert Settings().image_variant == "cuda"'

run: build ## Build and start the local hardened Compose service
	docker compose up -d

run-cuda: build-cuda ## Build and start the local CUDA Compose service
	docker compose -f docker-compose.yml -f docker-compose.cuda.yml up -d

restart: ## Rebuild and replace the local compose stack
	docker compose up -d --build --remove-orphans

restart-cuda: ## Rebuild and replace the local CUDA Compose service
	docker compose -f docker-compose.yml -f docker-compose.cuda.yml up -d --build --remove-orphans

stop: ## Stop this project's compose stack
	docker compose down

status: ## Show this project's compose services
	docker compose ps

audit: dev-image ## Scan the resolved Python dependency graph
	$(DEV_RUN) python -m pip_audit

sec: dev-image ## Write Python security findings to sec.sarif
	$(DEV_RUN) bash scripts/sec.sh

audit-compose: ## Enforce the production Compose hardening floor
	bash scripts/audit-compose.sh

audit-compose-cuda: ## Enforce hardening after applying the CUDA Compose override
	bash scripts/audit-compose.sh --cuda

version: ## Print the canonical version
	@echo $(TAG)

clean: ## Remove only project-owned build artifacts
	docker image rm $(DEV_IMAGE) 2>/dev/null || true
