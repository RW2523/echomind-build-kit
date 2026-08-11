#!/usr/bin/env bash
# Register this machine as the self-hosted runner the nightly job targets.
#
#     scripts/setup_runner.sh <REGISTRATION_TOKEN>
#
# Get the token from
#     https://github.com/RW2523/echomind-build-kit/settings/actions/runners/new
# (or `gh api -X POST repos/RW2523/echomind-build-kit/actions/runners/registration-token`
# with a token that has administration:write — a fine-grained PAT without it returns 403,
# which is why this is a script you run rather than something already done).
#
# The token is short-lived (about an hour) and is consumed by registration; it is not
# stored anywhere by this script.
#
# READ THIS FIRST — the repository is public.
#
# A self-hosted runner attached to a public repository will execute code from pull
# requests on this machine, with the privileges of the user running it: your .env, your
# database, your model endpoints, your SSH keys. GitHub's own advice is to use
# self-hosted runners only with private repositories.
#
# Two settings are what stand between a fork and this box, and neither can be set by
# this script — do them in the web UI before you register:
#
#   1. Settings > Actions > General > "Fork pull request workflows from outside
#      collaborators" -> "Require approval for all outside collaborators".
#      The default only asks for first-time contributors.
#
#   2. Settings > Actions > General > "Actions permissions" -> "Allow <owner>, and
#      select non-<owner>, actions", allowing only `astral-sh/setup-uv@*`. That stops a
#      pull request pulling an arbitrary marketplace action onto this machine.
#
# The runner is deliberately NOT installed as a root service. It runs as you, under your
# own systemd user session, so it can never do more than you can.

set -euo pipefail

TOKEN="${1:-}"
REPO_URL="${RUNNER_REPO_URL:-https://github.com/RW2523/echomind-build-kit}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
RUNNER_NAME="${RUNNER_NAME:-$(hostname)}"
LABELS="${RUNNER_LABELS:-self-hosted,gpu}"
VERSION="${RUNNER_VERSION:-2.321.0}"

if [[ -z "$TOKEN" ]]; then
  echo "usage: $0 <REGISTRATION_TOKEN>" >&2
  echo "get one at ${REPO_URL}/settings/actions/runners/new" >&2
  exit 2
fi

case "$(uname -m)" in
  aarch64|arm64) ARCH=arm64 ;;
  x86_64)        ARCH=x64 ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [[ ! -x ./config.sh ]]; then
  TARBALL="actions-runner-linux-${ARCH}-${VERSION}.tar.gz"
  echo "==> downloading ${TARBALL}"
  curl -fsSL -o "$TARBALL" \
    "https://github.com/actions/runner/releases/download/v${VERSION}/${TARBALL}"
  tar xzf "$TARBALL"
  rm -f "$TARBALL"
fi

echo "==> registering ${RUNNER_NAME} with labels ${LABELS}"
# --replace so re-running this after a token expires does not accumulate dead runners.
# --unattended so it never blocks waiting on a prompt.
./config.sh \
  --url "$REPO_URL" \
  --token "$TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$LABELS" \
  --work _work \
  --unattended \
  --replace

# A user-level unit rather than `svc.sh install`, which wants root. `loginctl
# enable-linger` is what keeps it running when you are not logged in.
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/github-runner.service" <<UNIT
[Unit]
Description=GitHub Actions runner (${RUNNER_NAME})
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=${RUNNER_DIR}/run.sh
WorkingDirectory=${RUNNER_DIR}
Restart=always
RestartSec=10
KillMode=process

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now github-runner.service
loginctl enable-linger "$USER" 2>/dev/null || \
  echo "note: could not enable linger; the runner will stop when you log out"

echo
echo "==> status"
systemctl --user --no-pager status github-runner.service | head -12 || true
echo
echo "Runner registered. Trigger the nightly job with:"
echo "    gh workflow run ci.yml"
echo "and watch it with:"
echo "    gh run watch \$(gh run list --limit 1 --json databaseId --jq '.[0].databaseId')"
echo
echo "To remove it later:"
echo "    systemctl --user disable --now github-runner.service"
echo "    cd ${RUNNER_DIR} && ./config.sh remove --token <REMOVAL_TOKEN>"
