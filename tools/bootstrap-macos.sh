#!/usr/bin/env bash

# Product Atelier macOS zero-state bootstrap.
#
# Safe to run on a Mac with no local project files. The script installs only
# development prerequisites, clones the pinned continuation branch, creates an
# isolated Python environment, and runs offline verification. It never reads an
# API key and never calls a paid image-generation endpoint.

set -Eeuo pipefail

REPO_URL="https://github.com/cat644142986-afk/-.git"
BRANCH="codex/master-roadmap-phase-0-1"
PROJECT_DIR="${PRODUCT_ATELIER_DIR:-$HOME/ProductAtelier-Desktop}"

log() {
  printf '\n[Product Atelier] %s\n' "$1"
}

fail() {
  printf '\n[Product Atelier] ERROR: %s\n' "$1" >&2
  exit 1
}

on_error() {
  local line="$1"
  printf '\n[Product Atelier] Setup stopped near line %s. Fix the reported error, then run the same one-line command again.\n' "$line" >&2
}
trap 'on_error "$LINENO"' ERR

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "This bootstrap is for macOS only."
fi

log "Checking Apple Command Line Tools"
if ! xcode-select -p >/dev/null 2>&1; then
  printf 'Apple will show an installation window. Approve it; this terminal will wait.\n'
  xcode-select --install >/dev/null 2>&1 || true
  command_line_tools_ready=0
  for _ in $(seq 1 360); do
    if xcode-select -p >/dev/null 2>&1; then
      command_line_tools_ready=1
      break
    fi
    sleep 15
  done
  if [[ "$command_line_tools_ready" -ne 1 ]]; then
    fail "Apple Command Line Tools were not ready after 90 minutes. Complete their installation and rerun this command."
  fi
fi

if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

if ! command -v brew >/dev/null 2>&1; then
  log "Installing Homebrew"
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  else
    fail "Homebrew was installed but could not be found."
  fi
fi

node_major=0
if command -v node >/dev/null 2>&1; then
  node_major="$(node -p 'process.versions.node.split(".")[0]')"
fi
if [[ "$node_major" -lt 20 ]]; then
  log "Installing Node.js 20+"
  brew install node
fi

if ! command -v python3.12 >/dev/null 2>&1; then
  log "Installing Python 3.12"
  brew install python@3.12
fi
PYTHON_BIN="$(command -v python3.12 || true)"
[[ -n "$PYTHON_BIN" ]] || fail "Python 3.12 is unavailable after installation."

if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi
if ! command -v cargo >/dev/null 2>&1; then
  log "Installing the Rust toolchain"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs |
    sh -s -- -y --profile minimal
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi

log "Synchronizing the Product Atelier source"
if [[ ! -e "$PROJECT_DIR" ]]; then
  git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$PROJECT_DIR"
elif [[ -d "$PROJECT_DIR/.git" ]]; then
  cd "$PROJECT_DIR"
  current_origin="$(git remote get-url origin 2>/dev/null || true)"
  if [[ "$current_origin" != "$REPO_URL" && "$current_origin" != "${REPO_URL%.git}" ]]; then
    fail "$PROJECT_DIR already contains a different Git repository. Set PRODUCT_ATELIER_DIR to another folder and rerun."
  fi
  if [[ -n "$(git status --porcelain)" ]]; then
    fail "$PROJECT_DIR has uncommitted work. Commit or preserve it before rerunning; the bootstrap will not overwrite it."
  fi
  git fetch origin "$BRANCH"
  if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git switch "$BRANCH"
  else
    git switch --track -c "$BRANCH" "origin/$BRANCH"
  fi
  git pull --ff-only origin "$BRANCH"
else
  fail "$PROJECT_DIR already exists but is not a Git repository. Move it or set PRODUCT_ATELIER_DIR to another folder."
fi

cd "$PROJECT_DIR"

log "Creating the isolated Python environment"
"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r python/requirements.txt
python -m pip install -r python/requirements-test.txt

log "Installing deterministic frontend dependencies"
npm ci

log "Running offline verification (no image-generation charges)"
npm run test:frontend
python -m unittest discover -s tests -p 'test_*.py'
npm run build
cargo check --manifest-path src-tauri/Cargo.toml --features custom-protocol

resolved_head="$(git rev-parse HEAD)"
cat > .macos-bootstrap-state <<EOF
branch=$BRANCH
commit=$resolved_head
verified_at_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
EOF

log "macOS source workstation is ready"
printf 'Project: %s\n' "$PROJECT_DIR"
printf 'Branch:  %s\n' "$BRANCH"
printf 'Commit:  %s\n' "$resolved_head"
printf '\nNext: open docs/macos-zero-state-handoff-2026-08-28.md and docs/product-atelier-master-execution-plan-2026-08-22.md.\n'
printf 'To launch the macOS development app later:\n  cd "%s" && source .venv/bin/activate && npm run tauri dev\n' "$PROJECT_DIR"
printf '\nWindows EXE/NSIS/portable-release approval still has to run on a Windows machine.\n'

open "$PROJECT_DIR/docs/macos-zero-state-handoff-2026-08-28.md" >/dev/null 2>&1 || true
