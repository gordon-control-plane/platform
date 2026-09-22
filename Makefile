SHELL := /bin/bash
.PHONY: cluster-up cluster-down setup deploy teardown test-e2e test lint format up test-ci

NAMESPACE ?= gordon
RELEASE_NAME ?= agent-platform

GIT_SHA ?= $(shell git rev-parse HEAD)
CURRENT_API_URL = $(shell kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null)
# Auto-detect local cluster if present, otherwise require explicit input
KUBE_API_URL ?= $(if $(findstring 127.0.0.1,$(CURRENT_API_URL)),$(CURRENT_API_URL),$(if $(findstring localhost,$(CURRENT_API_URL)),$(CURRENT_API_URL),))


cluster-up:
	@echo "Creating local kind cluster..."
	@cat <<EOF | kind create cluster --name gordon-dev --config=- || true
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  disableDefaultCNI: true
EOF
	@echo "Installing Calico CNI for network policies..."
	@kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.27.3/manifests/calico.yaml

cluster-down:
	@echo "Deleting local kind cluster..."
	@kind delete cluster --name gordon-dev || true
setup:
	./scripts/dev-setup.sh --gh-pat "$$GH_PAT"

check-cluster:
	@if [ -z "$(KUBE_API_URL)" ]; then \
	echo "ERROR: KUBE_API_URL is not set. Explicitly define it to prevent accidental deployments."; \
	echo "Current active API is: $(CURRENT_API_URL)"; \
	echo "Run: make [target] KUBE_API_URL=$(CURRENT_API_URL)"; \
	exit 1; \
	fi
	@if [ "$(KUBE_API_URL)" != "$(CURRENT_API_URL)" ]; then \
	echo "ERROR: Safety check failed!"; \
	echo "Active:   $(CURRENT_API_URL)"; \
	echo "Please log into the correct cluster to proceed."; \
	exit 1; \
	fi

deploy: setup check-cluster
	@echo "Deploying to cluster API: $(KUBE_API_URL)..."
	@# Install Zalando operator if not present
	@helm repo add postgres-operator-charts https://opensource.zalando.com/postgres-operator/charts/postgres-operator || true
	@helm upgrade --install postgres-operator postgres-operator-charts/postgres-operator \
	--namespace $(NAMESPACE) --create-namespace
	@echo "Waiting for Postgres Operator CRD..."
	@kubectl wait --for condition=established --timeout=60s crd/postgresqls.acid.zalan.do || true
	@kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	@. .env && kubectl create secret docker-registry ghcr-secret \
	--namespace $(NAMESPACE) \
	--docker-server=ghcr.io \
	--docker-username=gordon-control-plane \
	--docker-password="$$GH_PAT" \
	--dry-run=client -o yaml | kubectl apply -f -
	@. .env && helm upgrade --install $(RELEASE_NAME) charts/agent-platform \
	--namespace $(NAMESPACE) \
	--set global.image.tag="$(GIT_SHA)" \
	--set secrets.langfuseNextauthSecret="$$LANGFUSE_NEXTAUTH_SECRET" \
	--set secrets.langfuseSalt="$$LANGFUSE_SALT" \
	--set minio.auth.rootPassword="$$MINIO_ROOT_PASSWORD" \
	--wait --timeout 600s

up: cluster-up
	@echo "Building local images..."
	@docker build -t ghcr.io/gordon-control-plane/unified-api:$(GIT_SHA) -f build/Dockerfile.unified-api .
	@docker build -t ghcr.io/gordon-control-plane/agent-platform-worker:$(GIT_SHA) -f build/Dockerfile.worker .
	@echo "Loading images into kind..."
	@kind load docker-image ghcr.io/gordon-control-plane/unified-api:$(GIT_SHA) --name gordon-dev
	@kind load docker-image ghcr.io/gordon-control-plane/agent-platform-worker:$(GIT_SHA) --name gordon-dev
	@$(MAKE) deploy

test-ci:
	@echo "Starting Temporal dev server in Docker..."
	docker run --rm -d --name temporal-dev -p 7233:7233 -p 8233:8233 temporalio/admin-tools:latest temporal server start-dev --ui-port 8233 --ip 0.0.0.0
	@echo "Waiting for Temporal to be ready..."
	@while ! curl --retry 5 --retry-all-errors -sSf http://localhost:8233 > /dev/null 2>&1; do sleep 1; done
	uv run pytest tests/ --ignore=tests/e2e/ || (docker stop temporal-dev && exit 1)
	docker stop temporal-dev
	./tests/smoke_test.sh

teardown: check-cluster
	./scripts/teardown.sh $(NAMESPACE) $(RELEASE_NAME)

test:
	uv run pytest tests/

test-e2e: check-cluster
	@echo "Running E2E tests against API: $(KUBE_API_URL)..."
	uv run pytest tests/e2e/

lint:
	uvx prek run --all-files

format:
	uvx ruff format .
