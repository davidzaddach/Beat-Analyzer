#!/bin/zsh
# Beats Analyzer – Drag a folder onto this file to analyze it.
# Or double-click to open the app and browse for a folder.

cd "$(dirname "$0")"
source "$HOME/.local/bin/env" 2>/dev/null

if [ -n "$1" ]; then
    exec uv run python -m beats_cli "$1"
else
    exec uv run python -m beats_cli
fi
