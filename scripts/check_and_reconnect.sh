#!/bin/bash
# check_and_reconnect.sh
#
# Watchdog for the launchd-managed Claude Code <-> Telegram channel.
# Checks BOTH the outer `claude --channels` process AND the inner `bun`
# subprocess that actually talks to the Telegram Bot API. launchd's
# KeepAlive only restarts the job when the *outer* process exits, so it
# cannot detect "outer process alive, inner bun subprocess died" — which
# is a real failure mode we hit in production. This script catches that
# case and forces a full restart via `launchctl kickstart -k`.
#
# Install this under a plain home-directory subfolder such as
# ~/.claude/channels/telegram/watchdog/ — NOT under ~/Desktop, ~/Documents
# or ~/Downloads. On macOS those three folders are protected by TCC, and
# a script launched by launchd (not by Finder/Terminal) will fail with
# "Operation not permitted" when it tries to read or write there, with no
# other error message. This cost real debugging time — don't repeat it.
#
# Fill in LAUNCHD_LABEL below to match the Label in your main channel's
# plist (see launchd/com.example.claude-telegram-channel.plist.template).

LAUNCHD_LABEL="com.YOUR_USERNAME.claude-telegram-channel"   # <-- EDIT ME
LABEL_PATH="gui/$(id -u)/${LAUNCHD_LABEL}"

BASE_DIR="$HOME/.claude/channels/telegram/watchdog"
LOG="$BASE_DIR/watchdog.log"
STATUS_FILE="$BASE_DIR/status.txt"
STATE_FILE="$BASE_DIR/.last_kickstart"
COOLDOWN_SECONDS=300   # don't kickstart more than once per 5 minutes
ENV_FILE="$HOME/.claude/channels/telegram/.env"
ERR_LOG="$HOME/.claude/channels/telegram/launchd.err.log"

mkdir -p "$BASE_DIR"
ts() { date "+%Y-%m-%d %H:%M:%S"; }

outer_all=$(pgrep -af "claude --resume.*--channels plugin:telegram@claude-plugins-official")
bun_all=$(pgrep -af "claude-plugins-official/telegram" | grep -v "/bin/bash")
outer_count=$(echo "$outer_all" | grep -c .)
bun_count=$(echo "$bun_all" | grep -c .)
[ -z "$outer_all" ] && outer_count=0
[ -z "$bun_all" ] && bun_count=0

ps_ppid_check=$(ps -eo pid,ppid,comm,args | grep -E "claude --resume.*--channels plugin:telegram|claude-plugins-official/telegram" | grep -v grep)

need_restart=0
reason=""
if [ "$outer_count" -eq 0 ]; then
  need_restart=1
  reason="outer claude process is gone"
elif [ "$bun_count" -eq 0 ]; then
  need_restart=1
  reason="outer process alive, but the bun/telegram subprocess is gone"
fi

webhook_info=""
getme_info=""
if [ -f "$ENV_FILE" ]; then
  TOKEN=$(grep -o 'TELEGRAM_BOT_TOKEN=.*' "$ENV_FILE" | cut -d= -f2)
  if [ -n "$TOKEN" ]; then
    webhook_info=$(curl -s --max-time 8 "https://api.telegram.org/bot${TOKEN}/getWebhookInfo")
    getme_info=$(curl -s --max-time 8 "https://api.telegram.org/bot${TOKEN}/getMe")
  fi
fi

recent_err=$(tail -40 "$ERR_LOG" 2>/dev/null)

{
  echo "=== updated: $(ts) ==="
  echo "outer_process_count: $outer_count"
  echo "bun_process_count: $bun_count"
  echo "--- full process list (pid ppid comm args) ---"
  echo "$ps_ppid_check"
  echo "webhook_info: $webhook_info"
  echo "getme_info: $getme_info"
  echo "--- err.log last 40 lines ---"
  echo "$recent_err"
} > "$STATUS_FILE"

if [ "$need_restart" = "1" ]; then
  now=$(date +%s)
  last=0
  [ -f "$STATE_FILE" ] && last=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
  elapsed=$((now - last))
  if [ "$elapsed" -ge "$COOLDOWN_SECONDS" ]; then
    echo "$(ts) disconnect detected: ${reason} -> launchctl kickstart -k" >> "$LOG"
    launchctl kickstart -k "$LABEL_PATH" >> "$LOG" 2>&1
    echo "$now" > "$STATE_FILE"
  else
    echo "$(ts) disconnect detected: ${reason}, but last kickstart was ${elapsed}s ago, skipping" >> "$LOG"
  fi
fi

# Watch the pinned session's transcript for new API errors (login expired /
# blocked by safeguards / prompt too long) and auto-repair. See auto_repair.py.
LAUNCHD_LABEL="$LAUNCHD_LABEL" /usr/bin/python3 "$BASE_DIR/auto_repair.py" >> "$BASE_DIR/auto_repair.log" 2>&1
