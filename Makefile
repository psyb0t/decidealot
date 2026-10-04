SHELL := /bin/bash
.DEFAULT_GOAL := help

DEV_IMAGE := psyb0t/decidealot-dev
CPU_IMAGE := psyb0t/decidealot
CUDA_IMAGE := psyb0t/decidealot
TORCHBASE_CPU_IMAGE := psyb0t/torchbase:py3.12-torch2.14-latest-cpu
TORCHBASE_CUDA_IMAGE := psyb0t/torchbase:py3.12-torch2.14-latest-cu126
TORCH_VENV_DIRECTORY := /opt/torch-venv
MIN_TEST_COVERAGE := 90
TAG = $(shell awk -F\" '/^version *= */ {print "v" $$2; exit}' pyproject.toml)
UID := $(shell id -u)
GID := $(shell id -g)
DOCKER_SOCK := /var/run/docker.sock
DOCKER_GID := $(shell stat -c '%g' $(DOCKER_SOCK) 2>/dev/null || echo 0)
DEPENDENCY_CUTOFF := 2026-09-16T22:52:02Z
MODEL_EXCEPTION_CUTOFF := 2026-09-23T13:10:08Z

DEV_RUN := docker run --rm --init --user $(UID):$(GID) -e HOME=/tmp \
	-e PYTHONPATH=/work/src -e VIRTUAL_ENV=/opt/venv -e PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
	-v $(CURDIR):/work -w /work $(DEV_IMAGE)
DEV_TOOL_RUN := docker run --rm --init --user $(UID):$(GID) -e HOME=/tmp \
	-v $(CURDIR):/work -w /work $(DEV_IMAGE):tools
# Sibling fixtures publish on the host daemon's loopback interface.
DEV_RUN_DIND := docker run --rm --init --network host --user $(UID):$(GID) \
	--group-add $(DOCKER_GID) -e HOME=/tmp -e PYTHONPATH=$(CURDIR)/src -e VIRTUAL_ENV=/opt/venv -e PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
	-v $(CURDIR):$(CURDIR) -w $(CURDIR) \
	-v $(DOCKER_SOCK):$(DOCKER_SOCK) $(DEV_IMAGE)

.PHONY: help dev-image dev-tools-image shell pkg-lock pkg-add pkg-update pkg-upgrade pkg-remove model-lock dep format lint lint-fix audit sec test test-unit test-integration test-coverage test-real test-real-cuda test-real-clm test-real-jev generate build build-cuda build-all build-test build-test-cuda run run-cuda restart restart-cuda stop status audit-compose audit-compose-cuda version clean

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

pkg-add: dev-tools-image ## Add one exact application dependency with PKG=name==version
	test -n "$(PKG)"
	case "$(PKG)" in *==*) ;; *) echo "PKG must pin an exact version with ==" >&2; exit 2;; esac
	$(DEV_TOOL_RUN) bash -ceu 'uv add --no-sync --exclude-newer $(DEPENDENCY_CUTOFF) "$(PKG)"'

pkg-update: dev-tools-image ## Update one application dependency with PKG=name==version
	test -n "$(PKG)"
	case "$(PKG)" in *==*) ;; *) echo "PKG must pin an exact version with ==" >&2; exit 2;; esac
	$(DEV_TOOL_RUN) bash -ceu 'uv lock --exclude-newer $(DEPENDENCY_CUTOFF) --upgrade-package "$(PKG)"'

pkg-upgrade: dev-tools-image ## Upgrade every application dependency under the age gate
	$(DEV_TOOL_RUN) bash -ceu 'uv lock --exclude-newer $(DEPENDENCY_CUTOFF) --upgrade'

pkg-remove: dev-tools-image ## Remove one application dependency with PKG=name
	test -n "$(PKG)"
	$(DEV_TOOL_RUN) bash -ceu 'uv remove --no-sync --exclude-newer $(DEPENDENCY_CUTOFF) "$(PKG)"'

model-lock: dev-tools-image ## Generate hash-locked CPU and CUDA model dependency files
	$(DEV_TOOL_RUN) bash -ceu 'uv pip compile requirements-providers-cpu.in --output-file requirements-providers-cpu.txt --generate-hashes --quiet --torch-backend cpu --exclude-newer $(DEPENDENCY_CUTOFF) --exclude-newer-package laya=$(MODEL_EXCEPTION_CUTOFF) --exclude-newer-package von-sdk=$(MODEL_EXCEPTION_CUTOFF)'
	$(DEV_TOOL_RUN) bash -ceu 'uv pip compile requirements-providers-cuda.in --output-file requirements-providers-cuda.txt --generate-hashes --quiet --torch-backend cu126 --exclude-newer $(DEPENDENCY_CUTOFF) --exclude-newer-package laya=$(MODEL_EXCEPTION_CUTOFF) --exclude-newer-package von-sdk=$(MODEL_EXCEPTION_CUTOFF)'

dep: pkg-lock model-lock ## Refresh every locked dependency artifact

format: dev-image ## Format source
	$(DEV_RUN) python -m ruff format src tests scripts

lint: dev-image ## Run all static checks
	$(DEV_RUN) bash -ceu 'python -m ruff check src tests scripts && python -m pyright && python -m mypy src tests && python -m bandit -q -r src && shellcheck scripts/*.sh tests/integration/*.sh'

lint-fix: format ## Apply safe formatter fixes
	@$(MAKE) lint

test: test-unit test-integration ## Run the normal complete test suite

test-unit: dev-image ## Run the unit suite
	$(DEV_RUN) python -m pytest -q --ignore=tests/integration --ignore=tests/real

test-integration: dev-image ## Run local provider-process integration tests
	$(DEV_RUN) python -m pytest -q -m integration tests/integration

test-coverage: dev-image ## Enforce coverage and write the badge input
	$(DEV_RUN) bash -ceu 'COVERAGE_MINIMUM="$(MIN_TEST_COVERAGE)" bash scripts/test-coverage.sh'

test-real: build ## Run real HTTP requests against downloaded CPU Laya and Von weights
	bash tests/integration/e2e_local_models.sh

test-real-cuda: build-cuda ## Run real HTTP requests against downloaded CUDA Laya and Von weights
	bash tests/integration/e2e_local_models.sh --cuda

test-real-clm: build dev-image ## Run the real CLM head against a strict mock embeddings endpoint
	$(DEV_RUN_DIND) bash tests/integration/e2e_clm.sh

test-real-jev: dev-image ## Run one live hosted Jev decision with a private TypeSafe key
	@test -n "$$DECIDEALOT_TYPESAFE_API_KEY" || { echo "DECIDEALOT_TYPESAFE_API_KEY is required" >&2; exit 2; }
	docker run --rm --init --user $(UID):$(GID) -e HOME=/tmp \
		-e PYTHONPATH=/work/src -e VIRTUAL_ENV=/opt/venv -e PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
		-e DECIDEALOT_TYPESAFE_API_KEY -v $(CURDIR):/work -w /work $(DEV_IMAGE) \
		python -m pytest -q -m real tests/real/test_jev_live.py

generate: dev-image ## Regenerate every owned artifact
	$(DEV_RUN) python scripts/generate.py

build: ## Build the CPU production image
	docker build --build-arg TORCHBASE_IMAGE=$(TORCHBASE_CPU_IMAGE) -f Dockerfile -t $(CPU_IMAGE):local -t $(CPU_IMAGE):$(TAG) -t $(CPU_IMAGE):latest .

build-cuda: ## Build the CUDA production image
	docker build --build-arg TORCHBASE_IMAGE=$(TORCHBASE_CUDA_IMAGE) -f Dockerfile.cuda -t $(CUDA_IMAGE):local-cuda -t $(CUDA_IMAGE):$(TAG)-cuda -t $(CUDA_IMAGE):latest-cuda .

build-all: build build-cuda ## Build both production images

build-test: build ## Import the CPU image and verify its immutable runtime metadata
	docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=8m --entrypoint python $(CPU_IMAGE):local -c 'import os; from decidealot.settings import Settings; assert Settings().image_variant == "cpu"; assert os.environ["HOME"] == "/tmp"; assert (os.getuid(), os.getgid()) == (1000, 1000)'
	docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=8m --entrypoint $(TORCH_VENV_DIRECTORY)/bin/python $(CPU_IMAGE):local -c 'import pathlib, torch, laya, von; assert torch.__version__ == "2.14.0+cpu"; assert pathlib.Path("$(TORCH_VENV_DIRECTORY)/bin/laya-serve").is_file(); assert pathlib.Path("$(TORCH_VENV_DIRECTORY)/bin/von").is_file(); assert not pathlib.Path("/opt/laya-venv").exists(); assert not pathlib.Path("/opt/von-venv").exists()'

build-test-cuda: build-cuda ## Import the CUDA image and verify its immutable runtime metadata
	docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=8m --tmpfs /var/cache:rw,exec,nosuid,nodev,size=8m,uid=1000,gid=1000,mode=0755 --entrypoint python $(CUDA_IMAGE):local-cuda -c 'import os, shutil; from decidealot.settings import Settings; assert Settings().image_variant == "cuda"; assert os.environ["HOME"] == "/tmp"; assert (os.getuid(), os.getgid()) == (1000, 1000); assert os.environ["TRITON_CACHE_DIR"] == "/var/cache/triton"; assert os.access("/var/cache", os.W_OK); assert shutil.which("cc"); assert os.path.isfile("/usr/include/python3.12/Python.h")'
	docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=8m --tmpfs /var/cache:rw,exec,nosuid,nodev,size=8m,uid=1000,gid=1000,mode=0755 --entrypoint $(TORCH_VENV_DIRECTORY)/bin/python $(CUDA_IMAGE):local-cuda -c 'import pathlib, torch, laya, von; assert torch.__version__ == "2.14.0+cu126"; assert torch.version.cuda == "12.6"; assert pathlib.Path("$(TORCH_VENV_DIRECTORY)/bin/laya-serve").is_file(); assert pathlib.Path("$(TORCH_VENV_DIRECTORY)/bin/von").is_file(); assert not pathlib.Path("/opt/laya-venv").exists(); assert not pathlib.Path("/opt/von-venv").exists()'

run: build ## Build and start the local hardened Compose service
	DECIDEALOT_UID="$(UID)" DECIDEALOT_GID="$(GID)" docker compose up -d

run-cuda: build-cuda ## Build and start the local CUDA Compose service
	DECIDEALOT_UID="$(UID)" DECIDEALOT_GID="$(GID)" docker compose -f docker-compose.yml -f docker-compose.cuda.yml up -d

restart: ## Rebuild and replace the local compose stack
	DECIDEALOT_UID="$(UID)" DECIDEALOT_GID="$(GID)" docker compose up -d --build --remove-orphans

restart-cuda: ## Rebuild and replace the local CUDA Compose service
	DECIDEALOT_UID="$(UID)" DECIDEALOT_GID="$(GID)" docker compose -f docker-compose.yml -f docker-compose.cuda.yml up -d --build --remove-orphans

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
