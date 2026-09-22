#!/bin/bash
set -e

NAMESPACE="gordon"

if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "Skipping Kubernetes port-forward smoke test: No cluster accessible."
    exit 0
fi

echo "Verifying agent-platform-worker egress network policy..."
WORKER_POD=$(kubectl get pod -l app=workers -n "$NAMESPACE" -o jsonpath="{.items[0].metadata.name}")

if kubectl exec "$WORKER_POD" -n "$NAMESPACE" -- curl -s -m 2 https://google.com >/dev/null 2>&1; then
    echo "Smoke test failed: NetworkPolicy is NOT dropping external egress from worker pod!"
    exit 1
else
    echo "Egress blackhole verified (curl timed out as expected)."
fi


echo "Attempting to port-forward unified-api..."
kubectl port-forward svc/unified-api 8000:8000 -n "$NAMESPACE" >/dev/null 2>&1 &
PF_PID=$!

# Use a retry loop instead of hardcoded sleep
MAX_RETRIES=30
RETRY_COUNT=0
READY=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl --silent --fail http://127.0.0.1:8000/health | grep '"status"'; then
        echo "unified-api is healthy!"
        READY=true
        break
    fi
    echo "Waiting for unified-api health endpoint... ($RETRY_COUNT/$MAX_RETRIES)"
    sleep 1
    RETRY_COUNT=$((RETRY_COUNT+1))
done

kill $PF_PID || true

if [ "$READY" = false ]; then
    echo "Smoke test failed: unified-api not healthy."
    exit 1
fi
