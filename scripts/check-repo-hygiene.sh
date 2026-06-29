#!/usr/bin/env bash
#
# Repo hygiene guard — catches leaks that secret scanners miss.
#
# Two checks, both fail the build (exit 1) on any finding:
#   1. Tracked-but-gitignored files. A file listed in .gitignore that is still
#      tracked is almost always a machine-local artifact committed by accident
#      (e.g. .claude/settings.local.json leaking a developer's home path).
#   2. Developer-specific home/profile paths in source & config. Absolute paths
#      like C:\Users\<name>\ or /Users/<name>/ embed a contributor's username
#      and never belong in shared code. Built bundles, lockfiles, and vendored
#      assets are excluded to avoid false positives on minified content.
#
# Run locally:  bash scripts/check-repo-hygiene.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

fail=0

echo "==> Checking for tracked-but-gitignored files..."
ignored_tracked="$(git ls-files -ci --exclude-standard)"
if [[ -n "$ignored_tracked" ]]; then
  echo "::error::Tracked files match .gitignore (likely machine-local artifacts):"
  echo "$ignored_tracked" | sed 's/^/    /'
  echo "    Fix: git rm --cached <file>"
  fail=1
else
  echo "    OK — no tracked files are gitignored."
fi

echo "==> Checking for developer-specific home/profile paths..."
# Personal home/profile path shapes. Generic CI/container names and obvious
# placeholders are allow-listed since they carry no PII:
#   - CI/container homes: runner, vscode, node, appuser, the devcontainer's user
#   - placeholder user segments: ..., <name>, %USERNAME%, you, your, username, name
path_pattern='([A-Za-z]:\\Users\\|/c/Users/|/mnt/c/Users/|/(home|Users)/[A-Za-z0-9._-]+/)'
allow='/home/(runner|user|vscode|node|appuser)/|/Users/(runner|vscode)/|[Uu]sers[\\/](\.\.\.|<|%|you|your|username|name)'
if hits="$(git grep -nIE "$path_pattern" -- \
      ':(exclude)static/**' \
      ':(exclude)frontend/public/assets/**' \
      ':(exclude)**/*.min.js' \
      ':(exclude)**/package-lock.json' \
      ':(exclude)**/*.svg' \
    | grep -vE "$allow")"; then
  echo "::error::Developer-specific absolute paths found (embed a username):"
  echo "$hits" | sed 's/^/    /'
  fail=1
else
  echo "    OK — no developer home/profile paths."
fi

if [[ "$fail" -ne 0 ]]; then
  echo "Repo hygiene check FAILED."
  exit 1
fi
echo "Repo hygiene check passed."
