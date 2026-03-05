#!/usr/bin/env bash
# Claude Code statusLine command
# Mirrors the zsh PROMPT: cyan path + yellow git branch
# Also shows session identity, model name, context usage, and token counts
#
# Branch is suppressed when the cwd is a git worktree (non-primary checkout),
# because the worktree name already implies the branch context.
#
# Each Claude Code instance passes its own JSON to stdin, so all fields
# (model, context, session_id) are scoped to the specific instance.

input=$(cat)
cwd=$(echo "$input" | jq -r '.cwd')

# Shorten path: replace $HOME with ~
home="$HOME"
short_path="${cwd/#$home/~}"

# Detect whether cwd is a git worktree (non-primary checkout).
# `git worktree list` always lists the main worktree first; if our cwd
# appears in any subsequent line it is a linked worktree.
is_worktree=false
worktree_list=$(git -C "$cwd" --no-optional-locks worktree list --porcelain 2>/dev/null || true)
if [ -n "$worktree_list" ]; then
  # Count how many worktree blocks have a worktree path matching cwd
  # The first block is the main worktree; extras are linked worktrees.
  main_wt=$(echo "$worktree_list" | awk '/^worktree /{print $2; exit}')
  if [ "$cwd" != "$main_wt" ]; then
    is_worktree=true
  fi
fi

# Get git branch (only needed when not in a worktree)
branch=""
if [ "$is_worktree" = false ]; then
  branch=$(git -C "$cwd" --no-optional-locks branch --show-current 2>/dev/null || true)
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
DIM='\033[2m'
RESET='\033[0m'

# Format token count with k/M suffix
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

# --- Path (always shown) ---
printf "${CYAN}%s${RESET}" "$short_path"

# --- Branch: only when NOT in a worktree ---
if [ -n "$branch" ]; then
  printf " ${YELLOW}(%s)${RESET}" "$branch"
fi

# --- Model (scoped to this instance's stdin JSON) ---
if [ -n "$model" ]; then
  printf " ${DIM}|${RESET} ${MAGENTA}%s${RESET}" "$model"
fi

# --- Context percentage ---
if [ -n "$used_pct" ]; then
  pct=$(printf "%.0f" "$used_pct")

  if [ "$pct" -ge 80 ]; then
    pct_color="$RED"
  elif [ "$pct" -ge 50 ]; then
    pct_color="$YELLOW"
  else
    pct_color="$GREEN"
  fi

  printf " ${DIM}[${RESET}${pct_color}%s%%${RESET}${DIM}]${RESET}" "$pct"
fi

# --- Token counts ---
total_in=$(echo "$input" | jq -r '.context_window.total_input_tokens // empty')
total_out=$(echo "$input" | jq -r '.context_window.total_output_tokens // empty')

if [ -n "$total_in" ] && [ -n "$total_out" ]; then
  in_fmt=$(format_tokens "$total_in")
  out_fmt=$(format_tokens "$total_out")
  printf " ${DIM}in:${RESET}${CYAN}%s${RESET} ${DIM}out:${RESET}${CYAN}%s${RESET}" "$in_fmt" "$out_fmt"
fi
