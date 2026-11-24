#!/usr/bin/env bash
# Deploys one commit of origin/main. GitHub Actions calls it over SSH as a forced
# command (see docs/deploy.md), so the key can do nothing else. Manual run:
#   deploy/deploy.sh <full commit sha>
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CA_SHA256="D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31"

fail() { echo "deploy: $*" >&2; exit 1; }

env_value() { sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r'; }

check_env() {
  [[ -f .env ]] || fail "no .env on the server"
  [[ "$(stat -c %a .env)" == 600 ]] || fail ".env must be readable by its owner only: chmod 600 .env"
  local name
  for name in DOMAIN DASHBOARD_USER DASHBOARD_PASSWORD WEBHOOK_SECRET POSTGRES_PASSWORD; do
    [[ -n "$(env_value "$name")" ]] || fail "$name is empty in .env"
  done
  if [[ "$(env_value LLM_PROVIDER)" == gigachat ]]; then
    [[ -n "$(env_value LLM_API_KEY)" ]] || fail "LLM_API_KEY is empty in .env"
    local bundle fingerprint
    bundle="$(env_value GIGACHAT_CA_BUNDLE)"
    [[ -n "$bundle" && -f "backend/$bundle" ]] || fail "GIGACHAT_CA_BUNDLE must name a file inside backend/"
    fingerprint="$(openssl x509 -in "backend/$bundle" -noout -fingerprint -sha256 | cut -d= -f2)"
    [[ "$fingerprint" == "$CA_SHA256" ]] || fail "unexpected GigaChat root certificate"
  fi
}

start() { docker compose up --build --detach --wait --wait-timeout 300; }

main() {
  local sha="${SSH_ORIGINAL_COMMAND:-${1:-}}"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "expected a full commit sha"
  cd "$ROOT"

  exec 9>"$ROOT/.git/deploy.lock"
  flock -n 9 || fail "another deployment is running"

  check_env
  git fetch --quiet origin +refs/heads/main:refs/remotes/origin/main
  git merge-base --is-ancestor "$sha" origin/main 2>/dev/null || fail "commit is not in origin/main"

  local previous
  previous="$(git rev-parse HEAD)"
  git checkout --quiet --detach "$sha"
  if start; then
    echo "deploy: $sha is running"
    return
  fi

  echo "deploy: $sha did not start, returning to $previous" >&2
  git checkout --quiet --detach "$previous"
  start || fail "rollback failed too, check: docker compose ps"
  fail "rolled back to $previous"
}

# Parsed completely before the checkout replaces this file.
main "$@"; exit
