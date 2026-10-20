#!/usr/bin/env python3
"""Auto-repair for the launchd-managed Claude Code <-> Telegram session.

Called by check_and_reconnect.sh on every watchdog tick. It reads only the
lines appended to the pinned session's transcript since the last run and
reacts to API errors:

- authentication_failed (Login expired / Not logged in / 401)
    -> launchctl kickstart -k; if that keeps failing, tell the user via
       Telegram to run /login on the computer
- refusal (blocked by safeguards: "Start a new session to continue")
    -> test other models on a --fork-session copy first; if one works,
       switch --model in the plist and keep the SAME session (memory kept)
    -> only if every model fails, rotate to a brand-new session
- "Prompt is too long"
    -> rotate to a new session

Every action is reported through the Bot API directly (works even when
Claude itself is down). See docs/TROUBLESHOOTING_LOG.md.

Config: set LAUNCHD_LABEL in the environment (check_and_reconnect.sh passes
it), or edit the default below.
"""
import fcntl
import json
import os
import plistlib
import re
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

HOME = os.path.expanduser("~")
LABEL = os.environ.get("LAUNCHD_LABEL", "com.YOUR_USERNAME.claude-telegram-channel")  # <-- EDIT ME
PLIST = os.path.join(HOME, "Library/LaunchAgents", LABEL + ".plist")
# Claude Code stores transcripts under ~/.claude/projects/<cwd with non-alnum -> "-">
# The channel runs with WorkingDirectory = $HOME, so derive it from HOME.
PROJECT_DIR = os.path.join(HOME, ".claude/projects", re.sub(r"[^A-Za-z0-9]", "-", HOME))
TG_DIR = os.path.join(HOME, ".claude/channels/telegram")
BASE_DIR = os.path.join(TG_DIR, "watchdog")
STATE = os.path.join(BASE_DIR, "auto_repair_state.json")
LOG = os.path.join(BASE_DIR, "auto_repair.log")
CLAUDE = os.path.join(HOME, ".local/bin/claude")

# 被擋時依序嘗試的模型（跳過目前正在用的）
MODEL_LADDER = ["opus", "sonnet"]
AUTH_COOLDOWN = 600          # kickstart 之間至少隔 10 分鐘
AUTH_NOTIFY_AFTER = 2        # 30 分鐘內 kickstart 兩次還是失敗 → 通知去 /login
REPAIR_COOLDOWN = 600        # 換模型/換 session 後至少隔 10 分鐘再處理
ROTATE_MIN_INTERVAL = 6 * 3600  # 換新 session 最多 6 小時一次，避免無限循環


def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    with open(LOG, "a") as f:
        f.write("%s %s\n" % (ts(), msg))


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE)


def tg_notify(text):
    try:
        token = ""
        with open(os.path.join(TG_DIR, ".env")) as f:
            for line in f:
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.strip().split("=", 1)[1]
        with open(os.path.join(TG_DIR, "access.json")) as f:
            chat_id = json.load(f)["allowFrom"][0]
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(
            "https://api.telegram.org/bot%s/sendMessage" % token, data, timeout=10
        ).read()
    except Exception as e:
        log("TG 通知失敗: %r" % e)


def read_plist():
    with open(PLIST, "rb") as f:
        return plistlib.load(f)


def plist_args(p):
    args = p["ProgramArguments"]
    sid = args[args.index("--resume") + 1] if "--resume" in args else None
    model = args[args.index("--model") + 1] if "--model" in args else None
    return sid, model


def write_plist(p, sid=None, model=None):
    args = p["ProgramArguments"]
    if sid:
        args[args.index("--resume") + 1] = sid
    if model:
        if "--model" in args:
            args[args.index("--model") + 1] = model
        else:
            i = args.index("--resume") + 2
            args[i:i] = ["--model", model]
    with open(PLIST, "rb") as f:
        backup = f.read()
    with open(os.path.join(BASE_DIR, "plist.before_auto_repair.plist"), "wb") as f:
        f.write(backup)
    with open(PLIST, "wb") as f:
        plistlib.dump(p, f)


def reload_service():
    uid = str(os.getuid())
    subprocess.run(["launchctl", "bootout", "gui/%s/%s" % (uid, LABEL)],
                   capture_output=True)
    time.sleep(3)
    r = subprocess.run(["launchctl", "bootstrap", "gui/%s" % uid, PLIST],
                       capture_output=True, text=True)
    log("reload: rc=%s %s" % (r.returncode, (r.stderr or "").strip()))


def kickstart():
    uid = str(os.getuid())
    subprocess.run(["launchctl", "kickstart", "-k", "gui/%s/%s" % (uid, LABEL)],
                   capture_output=True)


def fork_test(sid, model):
    """用 fork 測試原 session 換模型能不能回覆（不會改到原 session）。"""
    try:
        r = subprocess.run(
            [CLAUDE, "-p", "--resume", sid, "--fork-session", "--model", model,
             "測試連線：只回覆 OK 兩個字"],
            cwd=HOME, capture_output=True, text=True, timeout=600)
        out = (r.stdout or "").strip()
        log("fork_test %s → rc=%s out=%r" % (model, r.returncode, out[-200:]))
        return r.returncode == 0 and "OK" in out and "API Error" not in out
    except Exception as e:
        log("fork_test %s 例外: %r" % (model, e))
        return False


def new_session(model):
    """先用 -p 建立新 session 檔，plist 就能直接用 --resume，不必兩段式切換。"""
    sid = str(uuid.uuid4())
    cmd = [CLAUDE, "-p", "--session-id", sid]
    if model:
        cmd += ["--model", model]
    cmd.append("這是新的 Telegram 常駐 session（舊 session 被擋，自動換新）。"
               "請遵守 ~/CLAUDE.md 的規則。這則只要回覆 OK。")
    r = subprocess.run(cmd, cwd=HOME, capture_output=True, text=True, timeout=600)
    ok = os.path.exists(os.path.join(PROJECT_DIR, sid + ".jsonl"))
    log("new_session %s → rc=%s exists=%s out=%r" % (sid, r.returncode, ok,
                                                     (r.stdout or "")[-200:]))
    return sid if ok else None


def scan_new_errors(path, state):
    """回傳 transcript 從上次讀到的位置之後新增的 API 錯誤。"""
    size = os.path.getsize(path)
    key = "offset:" + os.path.basename(path)
    if key not in state or state[key] > size:
        # 第一次看到這個檔：從尾端開始，不追溯舊錯誤
        state[key] = size
        return []
    errors = []
    with open(path, "rb") as f:
        f.seek(state[key])
        chunk = f.read()
    # 只處理完整的行，最後一行可能還在寫
    last_nl = chunk.rfind(b"\n")
    if last_nl < 0:
        return []
    state[key] += last_nl + 1
    for raw in chunk[:last_nl].split(b"\n"):
        if b'"isApiErrorMessage":true' not in raw:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue
        m = d.get("message") or {}
        text = "".join(b.get("text", "") for b in (m.get("content") or [])
                       if isinstance(b, dict))
        if m.get("stop_reason") == "refusal":
            errors.append("refusal")
        elif d.get("error") == "authentication_failed":
            errors.append("auth")
        elif "Prompt is too long" in text:
            errors.append("too_long")
    return errors


def handle_auth(state, now):
    if now - state.get("last_auth_kick", 0) < AUTH_COOLDOWN:
        return
    kicks = [t for t in state.get("auth_kicks", []) if now - t < 1800]
    if len(kicks) >= AUTH_NOTIFY_AFTER:
        if now - state.get("last_auth_notify", 0) > 3600:
            tg_notify("⚠️ Telegram 機器人登入失效，自動重啟 %d 次都沒恢復。\n"
                      "請在電腦上開 Claude Code 執行 /login，登入後機器人會在幾分鐘內自動恢復。"
                      % len(kicks))
            state["last_auth_notify"] = now
            log("auth: 多次重啟無效，已通知使用者 /login")
        return
    kickstart()
    kicks.append(now)
    state["auth_kicks"] = kicks
    state["last_auth_kick"] = now
    log("auth: 登入錯誤，已 kickstart -k")


def handle_blocked(kind, state, now):
    if now - state.get("last_repair", 0) < REPAIR_COOLDOWN:
        log("%s: 冷卻中，略過" % kind)
        return
    state["last_repair"] = now
    p = read_plist()
    sid, cur_model = plist_args(p)

    if kind == "refusal":
        for m in MODEL_LADDER:
            if m == cur_model or m in state.get("failed_models:" + sid, []):
                continue
            if fork_test(sid, m):
                write_plist(p, model=m)
                reload_service()
                log("refusal: 改用模型 %s 繼續原 session %s" % (m, sid))
                tg_notify("🔧 自動修復：原對話被 %s 的安全機制擋住，已改用 %s 繼續同一段對話，記憶都還在。\n可以再傳一次剛剛的訊息。"
                          % (cur_model or "預設模型", m))
                return
            state.setdefault("failed_models:" + sid, []).append(m)
        # 目前模型也算失敗
        if cur_model:
            state.setdefault("failed_models:" + sid, []).append(cur_model)

    if now - state.get("last_rotate", 0) < ROTATE_MIN_INTERVAL:
        log("%s: 需要換 session，但 6 小時內已換過，改通知使用者" % kind)
        tg_notify("⚠️ Telegram 機器人又被擋住了（%s），但 6 小時內已自動換過一次 session，"
                  "為避免無限循環先停手，請回電腦處理。" % kind)
        return
    new_sid = new_session(cur_model)
    if not new_sid:
        tg_notify("⚠️ Telegram 機器人被擋住（%s），自動換新 session 失敗，請回電腦處理。" % kind)
        return
    p = read_plist()
    write_plist(p, sid=new_sid)
    reload_service()
    state["last_rotate"] = now
    state.setdefault("old_sessions", []).append(sid)
    log("%s: 已換新 session %s（舊 %s 保留）" % (kind, new_sid, sid))
    reason = "安全機制擋住、換模型也無效" if kind == "refusal" else "對話太長"
    tg_notify("🔧 自動修復：原對話%s，已換成新的對話。\n"
              "⚠️ 之前在 Telegram 聊過的內容機器人不記得了（記憶檔、Notion、skill 不受影響）。\n"
              "舊對話紀錄有保留。可以再傳一次剛剛的訊息。" % reason)


def main():
    os.makedirs(BASE_DIR, exist_ok=True)
    lock = open(os.path.join(BASE_DIR, ".auto_repair.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # 上一輪（例如 fork 測試）還在跑
    state = load_state()
    now = int(time.time())
    try:
        sid, _ = plist_args(read_plist())
        path = os.path.join(PROJECT_DIR, "%s.jsonl" % sid)
        if not sid or not os.path.exists(path):
            return
        errors = scan_new_errors(path, state)
        save_state(state)  # 先存 offset，避免處理中途失敗下次重複觸發
        if not errors:
            return
        log("偵測到新錯誤: %s" % errors)
        if "too_long" in errors:
            handle_blocked("too_long", state, now)
        elif "refusal" in errors:
            handle_blocked("refusal", state, now)
        elif "auth" in errors:
            handle_auth(state, now)
    finally:
        save_state(state)


if __name__ == "__main__":
    main()
