#!/usr/bin/env bash
set -euo pipefail

IMAGE_REPO="${1:-ghcr.io/wlodzimierrr/homelab-api}"
IMAGE_TAG="${2:-0.2.0}"
IMAGE_REF="${IMAGE_REPO}:${IMAGE_TAG}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

echo "Building ${IMAGE_REF}"
docker build -t "${IMAGE_REF}" .

echo "Pushing ${IMAGE_REF}"
docker push "${IMAGE_REF}"

echo "Done: ${IMAGE_REF}"
