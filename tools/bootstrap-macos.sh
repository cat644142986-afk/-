#!/usr/bin/env bash

# Product Atelier macOS zero-state bootstrap.
#
# Safe to run on a Mac with no local project files. The script installs only
# development prerequisites, clones the pinned continuation branch, creates an
# isolated Python environment, and runs offline verification. It never reads an
# API key and never calls a paid image-generation endpoint.

set -Eeuo pipefail

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

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

load_homebrew() {
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

ensure_homebrew() {
  load_homebrew
  if command -v brew >/dev/null 2>&1; then
    return
  fi
  log "Installing Homebrew for missing development prerequisites"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  load_homebrew
  command -v brew >/dev/null 2>&1 || fail "Homebrew was installed but could not be found."
}

load_homebrew

node_major=0
if command -v node >/dev/null 2>&1; then
  node_major="$(node -p 'process.versions.node.split(".")[0]')"
fi
if ! [[ "$node_major" =~ ^[0-9]+$ ]] || [[ "$node_major" -lt 20 ]] || ! command -v npm >/dev/null 2>&1; then
  log "Installing Node.js 20+"
  ensure_homebrew
  brew install node
fi
command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 || fail "Node.js and npm are unavailable after installation."

PYTHON_BIN="$(command -v python3.12 || true)"
if [[ -z "$PYTHON_BIN" ]] && command -v uv >/dev/null 2>&1; then
  log "Provisioning Python 3.12 in the user directory"
  uv python install 3.12
  PYTHON_BIN="$(uv python find --system 3.12 2>/dev/null || true)"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  log "Installing Python 3.12"
  ensure_homebrew
  brew install python@3.12
  PYTHON_BIN="$(command -v python3.12 || true)"
fi
[[ -n "$PYTHON_BIN" ]] || fail "Python 3.12 is unavailable after installation."
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' ||
  fail "The selected Python runtime is not Python 3.12."

if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi
if ! command -v cargo >/dev/null 2>&1 || ! command -v rustc >/dev/null 2>&1; then
  log "Installing the Rust toolchain"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs |
    sh -s -- -y --profile minimal
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi
command -v cargo >/dev/null 2>&1 && command -v rustc >/dev/null 2>&1 ||
  fail "Rust cargo and rustc are unavailable after installation."

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
cargo check --locked --manifest-path src-tauri/Cargo.toml --features custom-protocol

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
