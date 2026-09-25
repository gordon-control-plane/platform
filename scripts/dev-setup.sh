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

# Prompt for secrets if missing and not in CI
if [ "$CI" != "true" ]; then
    if [ ! -f "$ENV_FILE" ]; then
        echo "Creating $ENV_FILE..."

        if command -v gh &> /dev/null && gh auth status &> /dev/null; then
            detected_user=$(gh api user -q .login 2>/dev/null || echo "")
            if [ -n "$detected_user" ]; then
                read -p "Found authenticated GitHub CLI (User: $detected_user). Use this for GHCR auth? [Y/n] " use_gh
                if [[ "$use_gh" =~ ^[Nn] ]]; then
                    read -p "Enter GitHub Username: " gh_user
                    read -p "Enter GitHub PAT (with read:packages scope): " gh_pat
                else
                    gh_user="$detected_user"
                    gh_pat=$(gh auth token)
                    echo "Using GitHub CLI token. (Note: if image pulls fail, ensure your CLI was authenticated with read:packages scope!)"
                fi
            else
                read -p "Enter GitHub Username: " gh_user
                read -p "Enter GitHub PAT (with read:packages scope): " gh_pat
            fi
        else
            read -p "Enter GitHub Username: " gh_user
            read -p "Enter GitHub PAT (with read:packages scope): " gh_pat
        fi

        # Generate secure random passwords
        lf_secret=$(openssl rand -base64 32)
        lf_salt=$(openssl rand -hex 16)

        cat <<EOF > "$ENV_FILE"
GH_USER="${gh_user}"
GH_PAT="${gh_pat}"
TAILSCALE_AUTH_KEY=""
LANGFUSE_NEXTAUTH_SECRET="${lf_secret}"
LANGFUSE_SALT="${lf_salt}"
EOF
        echo "Secrets securely generated and saved to $ENV_FILE"
    else
        echo "Found existing $ENV_FILE, skipping secret generation."
    fi

    # Append Tailscale key if missing from .env
    if ! grep -q "^TAILSCALE_AUTH_KEY=" "$ENV_FILE"; then
        read -p "Enter Tailscale Auth Key (tskey-auth-... or tskey-client-..., leave blank to skip): " ts_key
        echo "TAILSCALE_AUTH_KEY=\"${ts_key}\"" >> "$ENV_FILE"
    fi
else
    echo "CI environment detected. Skipping interactive secret generation."
fi

echo ""
echo "Adding Helm repositories..."
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo add temporal https://go.temporal.io/helm-charts
helm repo add postgres-operator-charts https://opensource.zalando.com/postgres-operator/charts/postgres-operator
helm repo update

echo ""
echo "Syncing Python dependencies..."
uv sync

echo ""
if [ "$CI" == "true" ]; then
    echo "Building Helm dependencies strictly from lockfile..."
    helm dependency build charts/agent-platform
else
    echo "Updating Helm dependencies..."
    helm dependency update charts/agent-platform
fi

echo ""
if [ "$CI" != "true" ]; then
    echo "Installing prek hooks..."
    uvx prek install
fi
echo ""
echo "Setup complete! Run 'make deploy' to spin up the cluster."
