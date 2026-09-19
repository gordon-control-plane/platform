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

# Prompt for secrets if missing
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating $ENV_FILE..."

    read -p "Enter GitHub PAT (with read:packages scope) for GHCR: " gh_pat

    # Generate secure random passwords
    lf_secret=$(openssl rand -base64 32)
    lf_salt=$(openssl rand -hex 16)

    cat <<EOF > "$ENV_FILE"
GH_PAT="${gh_pat}"
LANGFUSE_NEXTAUTH_SECRET="${lf_secret}"
LANGFUSE_SALT="${lf_salt}"
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
