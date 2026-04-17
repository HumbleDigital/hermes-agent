#!/bin/bash
# Build and run Hermes locally for development
# Mounts your gilfoyle profile and GCP credentials

set -e

cd "$(dirname "$0")/.."

echo "Building Hermes image..."
docker build -t hd-hermes-agent:dev -f docker/Dockerfile .

echo ""
echo "Starting Hermes container (gilfoyle profile)..."
echo "  - Hermes home: ~/.hermes/profiles/gilfoyle -> /opt/data"
echo "  - GCP creds: ~/.config/gcloud/application_default_credentials.json"
echo "  - Webhook port: 8080"
echo ""
echo "Commands:"
echo "  docker exec -it hermes-gilfoyle-dev hermes           # Interactive CLI"
echo "  docker exec -it hermes-gilfoyle-dev hermes gateway   # Gateway mode"
echo "  docker logs -f hermes-gilfoyle-dev                   # Watch logs"
echo "  docker compose -f docker-compose.dev.yaml down        # Stop"
echo ""

docker compose -f docker-compose.dev.yaml up --build
