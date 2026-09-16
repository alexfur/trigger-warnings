#!/usr/bin/env bash
set -euo pipefail

# Configure and start a self-hosted macOS arm64 Actions runner.
if [[ $# -lt 1 || -z "${1:-}" ]]; then
  echo "Usage: $0 REGISTRATION_TOKEN" >&2
  exit 64
fi

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-trigger-warnings}"
RUNNER_VERSION="2.337.0"
ARCHIVE="actions-runner-osx-arm64-${RUNNER_VERSION}.tar.gz"
DOWNLOAD_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${ARCHIVE}"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This runner setup requires an Apple Silicon macOS host." >&2
  exit 1
fi

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [[ ! -x ./config.sh || ! -x ./run.sh ]]; then
  tmp_archive="$(mktemp "${TMPDIR:-/tmp}/trigger-warnings-runner.XXXXXX.tar.gz")"
  trap 'rm -f "$tmp_archive"' EXIT
  curl --fail --location --retry 3 --output "$tmp_archive" "$DOWNLOAD_URL"
  tar -xzf "$tmp_archive"
fi

cat > .env <<'EOF'
PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
EOF

if [[ ! -f .runner ]]; then
  ./config.sh \
    --url https://github.com/alexfur/trigger-warnings \
    --token "$1" \
    --labels "self-hosted,macOS,ARM64" \
    --unattended \
    --replace
else
  echo "Runner is already registered in $RUNNER_DIR"
fi

service_plist="$(find "$HOME/Library/LaunchAgents" -maxdepth 1 \
  -name 'actions.runner.alexfur-trigger-warnings.*.plist' -print -quit 2>/dev/null || true)"
if [[ -z "$service_plist" ]]; then
  ./svc.sh install
else
  echo "Runner service is already installed: $service_plist"
fi
./svc.sh start
echo "Runner installed and started in $RUNNER_DIR"
