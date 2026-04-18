#!/bin/bash
# secret-bootstrap.sh — Pull secrets from GCP Secret Manager and export as env vars.
# Runs before hermes starts, inside the Docker entrypoint.
#
# Usage: source this script (not execute) so the exports propagate to the parent.
#   source /opt/hermes/scripts/secret-bootstrap.sh
#
# Required env vars:
#   GOOGLE_CLOUD_PROJECT — GCP project ID containing the secrets
#   GOOGLE_APPLICATION_CREDENTIALS — (optional) path to ADC credentials
#   HERMES_SECRET_PREFIX — (optional) only fetch secrets starting with this prefix
#   HERMES_SECRET_MAPPING — (optional) comma-separated src=dst pairs, e.g. "my-api-key=GEMINI_API_KEY,other-key=OPENAI_API_KEY"
#
# Naming convention:
#   Secrets named "hermes-google-ai-studio-api-key" become GOOGLE_AI_STUDIO_API_KEY
#   unless overridden by HERMES_SECRET_MAPPING.
#   hyphens in secret names are converted to underscores, "hermes-" prefix is stripped.

set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
SECRET_PREFIX="${HERMES_SECRET_PREFIX:-hermes-}"
MAPPING="${HERMES_SECRET_MAPPING:-}"

if [ -z "$PROJECT_ID" ]; then
    echo "[secret-bootstrap] GOOGLE_CLOUD_PROJECT not set, skipping secret injection"
    return 0 2>/dev/null || exit 0
fi

# Check for python3 + google-cloud-secret-manager
if ! python3 -c "from google.cloud import secretmanager" 2>/dev/null; then
    echo "[secret-bootstrap] google-cloud-secret-manager not installed, skipping"
    return 0 2>/dev/null || exit 0
fi

echo "[secret-bootstrap] Fetching secrets from GCP Secret Manager (project: $PROJECT_ID)"

# Build a mapping from secret name -> env var name
declare -A SECRET_ENV_MAP

# Parse explicit mapping if provided
if [ -n "$MAPPING" ]; then
    IFS=',' read -ra PAIRS <<< "$MAPPING"
    for pair in "${PAIRS[@]}"; do
        src="${pair%%=*}"
        dst="${pair#*=}"
        SECRET_ENV_MAP["$src"]="$dst"
    done
fi

# List secrets from GCP Secret Manager
SECRETS_JSON=$(python3 -c "
import json, sys
from google.cloud import secretmanager

client = secretmanager.SecretManagerServiceClient()
try:
    response = client.list_secrets(parent=f'projects/$PROJECT_ID')
    for secret in response:
        # Extract short name from full path: projects/XXX/secrets/NAME
        name = secret.name.split('/')[-1]
        print(name)
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1)

if [ $? -ne 0 ]; then
    echo "[secret-bootstrap] Failed to list secrets: $SECRETS_JSON"
    return 0 2>/dev/null || exit 0
fi

INJECTED_COUNT=0
SKIPPED_COUNT=0

while IFS= read -r secret_name; do
    [ -z "$secret_name" ] && continue
    
    # Skip secrets that don't match the prefix
    if [[ ! "$secret_name" == "${SECRET_PREFIX}"* ]]; then
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
        continue
    fi
    
    # Determine the env var name
    if [ -n "${SECRET_ENV_MAP[$secret_name]+x}" ]; then
        env_name="${SECRET_ENV_MAP[$secret_name]}"
    else
        # Strip prefix, convert hyphens to underscores, uppercase
        env_name="${secret_name#$SECRET_PREFIX}"
        env_name="${env_name//-/_}"
        env_name=$(echo "$env_name" | tr '[:lower:]' '[:upper:]')
    fi
    
    # Don't overwrite existing env vars (allows docker-compose env_file to take precedence)
    if [ -n "${!env_name:-}" ]; then
        echo "[secret-bootstrap]   $secret_name -> $env_name (skipped, already set)"
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
        continue
    fi
    
    # Fetch the secret value
    secret_value=$(python3 -c "
import json, sys
from google.cloud import secretmanager

client = secretmanager.SecretManagerServiceClient()
name = f'projects/$PROJECT_ID/secrets/$secret_name/versions/latest'
try:
    response = client.access_secret_version(name=name)
    print(response.payload.data.decode('utf-8'))
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1)
    
    if [ $? -ne 0 ]; then
        echo "[secret-bootstrap]   $secret_name -> $env_name (failed: $secret_value)"
        continue
    fi
    
    # Export the env var
    export "$env_name"="$secret_value"
    echo "[secret-bootstrap]   $secret_name -> $env_name (injected)"
    INJECTED_COUNT=$((INJECTED_COUNT + 1))
    
done <<< "$SECRETS_JSON"

echo "[secret-bootstrap] Done: $INJECTED_COUNT secrets injected, $SKIPPED_COUNT skipped"