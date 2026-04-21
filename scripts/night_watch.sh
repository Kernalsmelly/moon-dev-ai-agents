#!/usr/bin/env bash
set -euo pipefail

BASE="/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents"
# Ensure core binaries are available under nohup/non-interactive shells.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
cd "$BASE"

RG_BIN="$(command -v rg || true)"
if [[ -z "${RG_BIN}" ]]; then
  echo "night_watch: missing required binary: rg" >&2
  exit 1
fi

while true; do
  run="$("${RG_BIN}" --text -o 'run_id=\S+' logs/meme_bot_early_edge_auto.log | tail -n1 | cut -d= -f2 || true)"
  if [[ -z "${run}" ]]; then
    run="unknown"
  fi
  stats="$(
    sqlite3 -readonly data/positions.db \
      "select count(*),
              printf('%.3f', coalesce(sum(pnl_usd),0)),
              coalesce(sum(case when pnl_usd>0 then 1 else 0 end),0),
              coalesce(sum(case when pnl_usd<0 then 1 else 0 end),0)
       from trades
       where side='SELL'
         and json_extract(metadata,'$.run_id')='${run}';"
  )"
  sig="$(wc -l < data/meme_launch_signals.jsonl 2>/dev/null || echo 0)"
  printf '%s run=%s n,pnl,w,l=%s signals=%s\n' \
    "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$run" "$stats" "$sig"
  sleep 900
done
