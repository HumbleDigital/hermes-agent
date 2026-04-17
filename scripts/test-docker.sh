#!/bin/bash
# Test script for local Docker Hermes instance
# Validates GCP tools and Google Chat integration

set -e

echo "=== Hermes Local Test Suite ==="
echo ""

# Check container is running
if ! docker ps --format '{{.Names}}' | grep -q hermes-gilfoyle-dev; then
    echo "ERROR: Container hermes-gilfoyle-dev is not running"
    echo "Start it with: docker compose -f docker-compose.dev.yaml up -d"
    exit 1
fi

echo "✓ Container is running"
echo ""

# Test 1: Check Hermes CLI responds
echo "Test 1: Hermes CLI health check..."
docker exec hermes-gilfoyle-dev hermes --version 2>&1 || {
    echo "✗ Hermes CLI not responding"
    exit 1
}
echo "✓ Hermes CLI responds"
echo ""

# Test 2: Check GCP credentials are mounted
echo "Test 2: GCP credentials..."
docker exec hermes-gilfoyle-dev test -f /opt/data/.env && echo "✓ .env exists" || echo "✗ .env missing"
docker exec hermes-gilfoyle-dev bash -c 'source /opt/data/.env && [ -n "$GOOGLE_CLOUD_PROJECT" ]' && echo "✓ GCP project configured" || echo "✗ GCP project not set"
echo ""

# Test 3: Check GCP tools are available
echo "Test 3: GCP tools availability..."
docker exec hermes-gilfoyle-dev python3 -c "
from tools.gcp_secret_manager import check_gcp_secret_manager_requirements
from tools.gcp_tasks import check_gcp_tasks_requirements
from tools.gcp_storage import check_gcp_storage_requirements
print('Secret Manager:', '✓' if check_gcp_secret_manager_requirements() else '✗')
print('Cloud Tasks:', '✓' if check_gcp_tasks_requirements() else '✗')
print('Cloud Storage:', '✓' if check_gcp_storage_requirements() else '✗')
"
echo ""

# Test 4: Check Google Chat adapter
echo "Test 4: Google Chat adapter..."
docker exec hermes-gilfoyle-dev python3 -c "
from gateway.platforms.google_chat import check_google_chat_requirements
print('Google Chat:', '✓' if check_google_chat_requirements() else '✗')
"
echo ""

# Test 5: Run pytest suite (optional)
if [ "$1" = "--full" ]; then
    echo "Test 5: Running pytest suite..."
    docker exec hermes-gilfoyle-dev python3 -m pytest tests/tools/test_gcp_*.py -q
    echo ""
fi

echo "=== Test Summary ==="
echo "To run interactive tests:"
echo "  docker exec -it hermes-gilfoyle-dev hermes"
echo ""
echo "To test GCP tools manually:"
echo "  docker exec -it hermes-gilfoyle-dev python3 scripts/test-phase1-local.py"
echo ""
