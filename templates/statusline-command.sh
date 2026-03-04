#!/usr/bin/env bash
# Claude Code statusLine command
# Mirrors the zsh PROMPT: cyan path + yellow git branch
# Also shows session identity, model name, context usage, and token counts
#
# Each Claude Code instance passes its own JSON to stdin, so all fields
# (model, context, session_id) are scoped to the specific instance.
# session_name or a short session_id prefix is shown to distinguish
# parallel instances at a glance.

input=$(cat)
cwd=$(echo "$input" | jq -r '.cwd')

# Shorten path: replace $HOME with ~
home="$HOME"
short_path="${cwd/#$home/~}"

# Get git branch (skip optional locks)
branch=$(git -C "$cwd" --no-optional-locks branch --show-current 2>/dev/null || true)

# Session identity: prefer human-readable name, fall back to first 8 chars of session_id
session_name=$(echo "$input" | jq -r '.session_name // empty')
session_id=$(echo "$input" | jq -r '.session_id // empty')
if [ -n "$session_name" ]; then
  session_label="$session_name"
elif [ -n "$session_id" ]; then
  session_label="${session_id:0:8}"
else
  session_label=""
fi

# Model display name — read from the per-instance JSON (never shared between instances)
model=$(echo "$input" | jq -r '.model.display_name // empty')
# Fallback: use model ID if display_name is absent
if [ -z "$model" ]; then
  model=$(echo "$input" | jq -r '.model.id // empty')
fi

# Context usage percentage
used_pct=$(echo "$input" | jq -r '.context_window.used_percentage // empty')

# ANSI colors
CYAN='\033[36m'
YELLOW='\033[33m'
GREEN='\033[32m'
MAGENTA='\033[35m'
RED='\033[31m'
BLUE='\033[34m'
DIM='\033[2m'
RESET='\033[0m'

# Build progress bar (10 chars wide)
build_bar() {
  local pct="$1"
  local width=10
  local filled=$(( pct * width / 100 ))
  local empty=$(( width - filled ))
  local bar=""
  local i=0
  while [ $i -lt $filled ]; do
    bar="${bar}█"
    i=$(( i + 1 ))
  done
  i=0
  while [ $i -lt $empty ]; do
    bar="${bar}░"
    i=$(( i + 1 ))
  done
  echo "$bar"
}

# --- Left side: path + branch ---
if [ -n "$branch" ]; then
  printf "${CYAN}%s${RESET} ${YELLOW}(%s)${RESET}" "$short_path" "$branch"
else
  printf "${CYAN}%s${RESET}" "$short_path"
fi

# --- Session label (distinguishes parallel instances) ---
if [ -n "$session_label" ]; then
  printf " ${DIM}[${RESET}${BLUE}%s${RESET}${DIM}]${RESET}" "$session_label"
fi

# --- Model (scoped to this instance's stdin JSON) ---
if [ -n "$model" ]; then
  printf " ${DIM}|${RESET} ${MAGENTA}%s${RESET}" "$model"
fi

if [ -n "$used_pct" ]; then
  # Round to integer
  pct=$(printf "%.0f" "$used_pct")
  bar=$(build_bar "$pct")

  # Color the bar based on usage level
  if [ "$pct" -ge 80 ]; then
    bar_color="$RED"
  elif [ "$pct" -ge 50 ]; then
    bar_color="$YELLOW"
  else
    bar_color="$GREEN"
  fi

  printf " ${DIM}[${RESET}${bar_color}%s${RESET}${DIM}]${RESET} ${DIM}%s%%%${RESET}" "$bar" "$pct"
fi

# --- Token counts ---
total_in=$(echo "$input" | jq -r '.context_window.total_input_tokens // empty')
total_out=$(echo "$input" | jq -r '.context_window.total_output_tokens // empty')

# Format numbers with k/M suffix for readability
format_tokens() {
  local n="$1"
  if [ "$n" -ge 1000000 ]; then
    printf "%.1fM" "$(echo "scale=1; $n / 1000000" | bc)"
  elif [ "$n" -ge 1000 ]; then
    printf "%.1fk" "$(echo "scale=1; $n / 1000" | bc)"
  else
    printf "%d" "$n"
  fi
}

if [ -n "$total_in" ] && [ -n "$total_out" ]; then
  in_fmt=$(format_tokens "$total_in")
  out_fmt=$(format_tokens "$total_out")
  printf " ${DIM}in:${RESET}${CYAN}%s${RESET} ${DIM}out:${RESET}${CYAN}%s${RESET}" "$in_fmt" "$out_fmt"
fi
