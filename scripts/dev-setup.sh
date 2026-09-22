#!/bin/bash
set -eo pipefail

echo "========================================"
echo " Gordon Control Plane - Dev Setup"
echo "========================================"

# Check dependencies
for cmd in uv helm kubectl jq openssl; do
    if ! command -v $cmd &> /dev/null; then
        echo "Error: '$cmd' is not installed."
        exit 1
    fi
done

ENV_FILE=".env"

GH_PAT="${GH_PAT:-}"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --gh-pat) GH_PAT="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# Prompt for secrets if missing
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating $ENV_FILE..."

    if [ -z "$GH_PAT" ]; then
        echo "Error: GH_PAT is required for initial setup. Provide via --gh-pat or GH_PAT environment variable."
        exit 1
    fi
    # Generate secure random passwords
    lf_secret=$(openssl rand -base64 32)
    lf_salt=$(openssl rand -hex 16)
    minio_pw=$(openssl rand -base64 32)

    cat <<EOF > "$ENV_FILE"
GH_PAT="${GH_PAT}"
LANGFUSE_NEXTAUTH_SECRET="${lf_secret}"
LANGFUSE_SALT="${lf_salt}"
MINIO_ROOT_PASSWORD="${minio_pw}"
EOF
    echo "Secrets securely generated and saved to $ENV_FILE"
else
    echo "Found existing $ENV_FILE, skipping secret generation."
fi

echo ""
echo "Syncing Python dependencies..."
uv sync

echo ""
echo "Updating Helm dependencies..."
helm dependency update charts/agent-platform

echo ""
echo "Installing prek hooks..."
uvx prek install
echo ""
echo "Setup complete! Run 'make deploy' to spin up the cluster."
