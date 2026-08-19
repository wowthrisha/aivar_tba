#!/bin/sh
# Installs versioned git hooks from scripts/ into .git/hooks/
# (git does not version .git/hooks/, so this script bridges the gap).
set -e
cp "$(dirname "$0")/pre-commit" .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
echo "Installed pre-commit hook."
