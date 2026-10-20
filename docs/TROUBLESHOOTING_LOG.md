# 實戰排錯紀錄（Q&A）

這份是實際跑了一段時間後遇到的問題、診斷過程和解法，用「問題 → 原因 → 解法」
整理，方便下次遇到同樣症狀直接對照。所有 session id、chat_id、路徑都已換成佔位符。

---

## 2026-10-16：Telegram 傳網址，完全沒回應

**症狀**：`launchctl print` 顯示 `state = running`，process chain 也都在，但
Telegram 傳的訊息全部沒有回覆。

**診斷**：看 launchd 的 stdout log：

```bash
tail -c 4000 ~/.claude/channels/telegram/launchd.out.log
```

畫面裡一直出現：

```
Login expired · Please run /login
```

而且 launchd 那條 chain 底下**沒有 `bun server.ts` 子行程**（bun 沒起來）。

**原因**：常駐 process 的登入憑證過期。process 本身沒死，所以 `KeepAlive`
不會幫你重啟。

**解法**：

1. 在任一個互動式 Claude Code 視窗執行 `/login` 重新登入（憑證是共用的）
2. 原地重啟常駐服務，讓它吃到新憑證：
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel
   ```
3. 確認只剩一組 `bun server.ts`。如果互動視窗自己也開了 telegram plugin，兩邊
   會搶同一個 bot 的 long-poll，要留意不要有兩組同時跑。

**教訓**：「Login expired」不會出現在 `err.log`，只會出現在 `out.log` 的終端機
畫面裡。診斷時兩個 log 都要看。

---

## 2026-10-19：process 全綠燈，但每則訊息都被擋

**症狀**：重啟、重新登入都沒用。傳一句「嗨」也收不到回覆。

**診斷**：`out.log` 畫面裡出現：

```
API Error: <模型> can't help with this. Start a new session to continue.
Details: `[bio]`
```

在 session 的 transcript 裡數一下被擋了幾次：

```bash
grep -c "can't help with this" ~/.claude/projects/<project-dir>/<SESSION_ID>.jsonl
```

另外這個 session 的 transcript 已經長到約 150MB。

**原因**：被固定 `--resume` 的那個長期 session，對話內容裡有東西觸發了
Anthropic 的安全機制（safeguards），之後**每一則新訊息都會被擋**。不是當機、
不是連線斷掉、也不是 context 滿了。只要繼續 resume 同一個 session 就會一直
被擋，重啟沒用。

**常見誤會：「是不是訊息量／context 滿了？」**
不是。context 接近上限時 Claude Code 會自動壓縮（compact）舊內容後繼續，
不會讓 bot 停擺。用「context 到 99% 就重開」防不到這種狀況，而且從外部也
讀不到 context 用量百分比，沒辦法拿來當觸發條件。

**解法 A（會失去對話記憶）：換新 session**

```bash
P=~/Library/LaunchAgents/com.YOUR_USERNAME.claude-telegram-channel.plist
cp "$P" ~/.claude/channels/telegram/plist.backup.plist   # 先備份
NEW=$(uuidgen | tr 'A-Z' 'a-z')
# ProgramArguments 的 index 依你的 plist 調整（前面有 /usr/bin/script -q /dev/null 時是 4、5）
/usr/libexec/PlistBuddy -c "Set :ProgramArguments:4 --session-id" -c "Set :ProgramArguments:5 $NEW" "$P"
launchctl bootout gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel
launchctl bootstrap gui/$(id -u) "$P"
```

注意 `--session-id <新uuid>` **只能用在第一次啟動**。等 bot 收到第一則訊息、
`<NEW>.jsonl` 出現之後，要把參數改回 `--resume <NEW>` 再 bootout + bootstrap
一次。不然下次 launchd 重開時會因為「session id 已存在」而失敗。

**解法 B（保留記憶，最後採用這個）：換模型繼續 resume 原 session**

錯誤訊息是某個特定模型的 safeguards 擋下的。先用 `--fork-session` 測試，
看原 session 換模型後能不能過（fork 不會改到原 session）：

```bash
claude -p --resume <OLD_SESSION_ID> --fork-session --model opus "測試：只回覆 OK"
```

回 `OK` 就代表可行。接著把 plist 改回原 session，並加上 `--model`：

```xml
<string>--resume</string>
<string>OLD_SESSION_ID</string>
<string>--model</string>
<string>opus</string>
<string>--channels</string>
...
```

然後 bootout + bootstrap。

**取捨**：

| 方案 | 記憶 | 代價 |
|------|------|------|
| A 換新 session | 失去 Telegram 對話記憶（`~/CLAUDE.md`、memory 檔、skill 不受影響） | 無 |
| B 換模型 resume | 保留 | 較大的模型用量消耗較多；之後仍有可能再被擋 |

**降低再發生的機率**：真正重要、需要長期記住的東西（偏好、流程、規則）不要只
留在對話裡，要寫進 `~/CLAUDE.md` 或 memory 檔。這樣就算哪天不得不換 session，
重要的記憶也還在。

---

## 2026-10-20：把上面兩個案例做成自動修復

手動修了兩次以後，乾脆寫成 `scripts/auto_repair.py`，掛在 watchdog 上每 2 分鐘
跑一次（說明見 `SETUP.md` 第六節）：

- 判斷依據是 transcript 裡的 API 錯誤列，不是 `out.log` 的終端機畫面。
  `--resume` 重開時畫面會把舊對話（包括舊的錯誤訊息）重畫一遍，看畫面會誤判。
  transcript 的錯誤列是結構化 JSON，例如：
  ```json
  {"isApiErrorMessage": true, "error": "authentication_failed", ...}
  {"isApiErrorMessage": true, "error": "invalid_request",
   "message": {"stop_reason": "refusal", "stop_details": {"category": "..."}}}
  ```
- 被擋時一定先試「換模型、保留記憶」，全部失敗才換新 session。這是使用者
  明確的要求：不希望記憶不見。
- 換新 session 時先用 `claude -p --session-id <uuid>` 把 session 檔建好，
  plist 就能直接寫 `--resume <uuid>`，不必走「先 `--session-id`、等第一則
  訊息、再改回 `--resume`」的兩段式切換。

---

## 小優化：收到訊息先按 👀

讓 bot 一收到訊息就先在那則訊息上加 👀，使用者可以馬上看出「它有收到、正在
處理」，跟「完全沒收到」分得開。在互動終端機執行：

```
/telegram:access set ackReaction 👀
```

這只是在 `access.json` 加上 `"ackReaction": "👀"`。設定檔會被重新讀取，
不需要重啟。設成 `""` 就是關閉。

---

## 診斷順序速查（綜合以上）

1. `launchctl print ...` → state 是不是 running
2. `ps -ef | grep ...` → 是否剛好一組 chain，而且底下有 `bun server.ts`
3. `tail -c 4000 launchd.out.log` → 找 `Login expired`、`can't help with this`、
   `Start a new session`
4. 依症狀處理：
   - Login expired → `/login` + `kickstart -k`
   - 被 safeguards 擋 → 先試解法 B（換模型、保留記憶），不行再用解法 A
     （裝了 `scripts/auto_repair.py` 的話，這一步和 Login expired 都會自動處理）
   - 以上都沒有 → 見 `SETUP.md` 第九節（reply 工具沒被呼叫的 prompt 問題）
5. 最後一定要實際傳一則訊息，做端對端測試
