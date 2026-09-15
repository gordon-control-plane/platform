.PHONY: up down lint test

up:
	helm upgrade --install agent-platform ./charts/agent-platform -n agent-platform --create-namespace

down:
	helm uninstall agent-platform -n agent-platform || true

lint:
	helm lint charts/agent-platform

test:
	pytest tests/
