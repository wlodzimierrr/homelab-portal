#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8000}"
PORTAL_BASE_URL="${PORTAL_BASE_URL:-http://127.0.0.1:5173}"
SERVICE_ID="${SERVICE_ID:-homelab-api}"
AUTH_TOKEN="${AUTH_TOKEN:-}"
AUTH_COOKIE="${AUTH_COOKIE:-}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage: live_data_smoke.sh [options]

Options:
  --api-base-url URL      Backend API base URL (default: $API_BASE_URL or http://127.0.0.1:8000)
  --portal-base-url URL   Portal base URL for sample asset checks (default: $PORTAL_BASE_URL or http://127.0.0.1:5173)
  --service-id ID         Service id for service-level checks (default: homelab-api)
  --auth-token TOKEN      Bearer token for API checks
  --auth-cookie COOKIE    Cookie header value for API checks
  --help                  Show this help

Environment alternatives:
  API_BASE_URL, PORTAL_BASE_URL, SERVICE_ID, AUTH_TOKEN, AUTH_COOKIE
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base-url)
      API_BASE_URL="${2:-}"
      shift 2
      ;;
    --portal-base-url)
      PORTAL_BASE_URL="${2:-}"
      shift 2
      ;;
    --service-id)
      SERVICE_ID="${2:-}"
      shift 2
      ;;
    --auth-token)
      AUTH_TOKEN="${2:-}"
      shift 2
      ;;
    --auth-cookie)
      AUTH_COOKIE="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

api_url() {
  local path="$1"
  echo "${API_BASE_URL%/}${path}"
}

portal_url() {
  local path="$1"
  echo "${PORTAL_BASE_URL%/}${path}"
}

curl_json() {
  local url="$1"
  local body_file="$2"
  local status_file="$3"

  local -a headers
  headers=(-H "Accept: application/json")
  if [[ -n "$AUTH_TOKEN" ]]; then
    headers+=(-H "Authorization: Bearer ${AUTH_TOKEN}")
  fi
  if [[ -n "$AUTH_COOKIE" ]]; then
    headers+=(-H "Cookie: ${AUTH_COOKIE}")
  fi

  curl -sS "${headers[@]}" -o "$body_file" -w "%{http_code}" "$url" > "$status_file"
}

assert_no_mock_markers() {
  local body_file="$1"
  if grep -E -q 'mock-|\"sample_fallback\"|\"-mock-\"' "$body_file"; then
    echo "FAIL: mock/sample marker detected in response: $body_file" >&2
    exit 1
  fi
}

assert_json_response() {
  local url="$1"
  local name="$2"
  local body_file="${TMP_DIR}/${name}.json"
  local status_file="${TMP_DIR}/${name}.status"

  curl_json "$url" "$body_file" "$status_file"
  local status
  status="$(cat "$status_file")"

  if [[ "$status" -ne 200 ]]; then
    echo "FAIL: ${name} returned HTTP ${status} (${url})" >&2
    if grep -qiE '<html|sign in|oauth2' "$body_file"; then
      echo "Hint: auth/session gate detected (HTML response). Re-authenticate or pass AUTH_TOKEN/AUTH_COOKIE." >&2
    fi
    exit 1
  fi

  if ! grep -qE '^[[:space:]]*[\{\[]' "$body_file"; then
    echo "FAIL: ${name} did not return JSON payload (${url})" >&2
    exit 1
  fi

  assert_no_mock_markers "$body_file"
  echo "PASS: ${name}"
}

assert_sample_asset_not_served() {
  local path="$1"
  local url
  url="$(portal_url "$path")"
  local status
  status="$(curl -sS -o /dev/null -w "%{http_code}" "$url")"

  if [[ "$status" -eq 200 ]]; then
    echo "FAIL: sample asset is reachable in deployed UI path: ${url}" >&2
    exit 1
  fi
  echo "PASS: sample asset blocked (${path}, status=${status})"
}

echo "Running live-data smoke checks"
echo "API_BASE_URL=${API_BASE_URL}"
echo "PORTAL_BASE_URL=${PORTAL_BASE_URL}"
echo "SERVICE_ID=${SERVICE_ID}"

assert_json_response "$(api_url "/releases?limit=20")" "dashboard_releases"
assert_json_response "$(api_url "/services/${SERVICE_ID}/metrics/summary?range=24h")" "service_metrics_summary"
assert_json_response "$(api_url "/services/${SERVICE_ID}/health/timeline?range=24h&step=5m")" "service_health_timeline"
assert_json_response "$(api_url "/services/${SERVICE_ID}/logs/quickview?preset=errors&range=1h&limit=50")" "service_logs_quickview"
assert_json_response "$(api_url "/alerts/active")" "platform_alerts_active"

assert_sample_asset_not_served "/release-dashboard.sample.json"
assert_sample_asset_not_served "/services.sample.json"
assert_sample_asset_not_served "/platform-health.sample.json"
assert_sample_asset_not_served "/service-health-timeline.sample.json"
assert_sample_asset_not_served "/service-metrics.sample.json"

echo "All live-data smoke checks passed."
