# Claude Code × Telegram 串接完整指南

> 目的：讓你在外面用手機 Telegram 跟跑在自己電腦上的 Claude Code 對話，並且有一套
> 「process 掛掉自動重連」「憑證/版本問題一行指令遠端修復」的正式常駐架構，不是
> 開發機測試用的 demo。
>
> 實測環境：macOS，zsh，Claude Code CLI。本指南所有個人資訊（帳號、chat_id、bot
> token、session id）都已替換成佔位符，照著填你自己的值即可。

---

## 架構說明

```
[手機 Telegram]
      │  DM
      ▼
[@YOUR_BOT_USERNAME（Telegram Bot API）]
      │  long polling
      ▼
[bun server.ts（telegram plugin 的 MCP server，跑在你電腦上）]
      │  stdio MCP
      ▼
[Claude Code（--channels 旗標啟動，launchd 24 小時常駐）]
      │  reply 工具
      ▼
[bun server.ts → Telegram Bot API]
      │
      ▼
[手機收到 push notification]
```

| 元件 | 說明 |
|------|------|
| Telegram Bot | 你自己申請的 bot，作為訊息中繼 |
| Bun | JS runtime，跑 MCP plugin server |
| telegram plugin | `claude-plugins-official` 提供的 MCP server |
| Claude Code | 用 `--channels` 旗標掛上 Telegram channel |
| launchd | macOS 系統服務管理，讓上面這條 chain 24 小時常駐、掛掉自動重開 |

---

## 一、事前準備

### 1. 申請 Telegram Bot

1. Telegram 開 [@BotFather](https://t.me/BotFather)
2. 傳 `/newbot`
3. 設定 Name（顯示名）和 Username（必須以 `bot` 結尾）
4. 拿到 token，格式：`123456789:AAH...`

### 2. 取得自己的 chat_id

找 [@userinfobot](https://t.me/userinfobot) 傳任意訊息，他會回你的 numeric ID。
下文用 `YOUR_CHAT_ID` 代表這個數字。

---

## 二、安裝步驟

### 1. 安裝 Claude Code（若尚未安裝）

```bash
npm install -g @anthropic-ai/claude-code
```

### 2. 安裝 Bun

```bash
curl -fsSL https://bun.sh/install | bash
source ~/.zshrc
bun --version
```

### 3. 安裝 Telegram Plugin

在 Claude Code 裡執行：

```
/plugin install telegram@claude-plugins-official
/reload-plugins
```

### 4. 設定 Bot Token

```
/telegram:configure <你的 bot token>
```

這會把 token 寫到 `~/.claude/channels/telegram/.env`（**這個檔案不要上傳到任何公開
地方，包含 git repo**）。

---

## 三、設定 access.json（重要！）

Plugin 安裝後，手動建立或確認 `~/.claude/channels/telegram/access.json`，範本見
`templates/access.json`：

```json
{
  "dmPolicy": "allowlist",
  "allowFrom": ["YOUR_CHAT_ID"],
  "groups": {},
  "pending": {}
}
```

**⚠️ 常見錯誤：** 欄位名稱必須是 `allowFrom`（不是 `allowlist`），值必須是**字串**
（不是數字）。

---

## 四、CLAUDE.md 設定

在 `~/CLAUDE.md` 加入 `claude-md-snippets/` 底下兩個檔案的內容：

- `01-telegram-channel.md`：基本溝通規則，**包含一個已修正過的重要規則**——每一則
  Telegram 訊息（不論任務或閒聊）都必須呼叫 reply 工具，否則回覆只會留在終端機
  log 裡，使用者端完全收不到。這是實測踩過的真實 bug，細節見下方「踩過的坑」。
- `02-remote-repair-command.md`：在 Telegram 傳「repair」兩個字就自動更新 plugin
  並重啟服務的遠端修復指令。

套用時把裡面的 `YOUR_USERNAME`、`YOUR_BOT_USERNAME` 換成你自己的值。

---

## 五、正式常駐架構：launchd（取代手動啟動）

**不要**靠人手動打 `claude --channels ...` 這種指令日常維運，這樣一旦忘記、或在
另一個視窗又手動起了一份，會變成兩條 process 搶同一組 Telegram long polling，
訊息忽有忽無。

正式做法是用 **launchd LaunchAgent** 24 小時帶著跑：開機/登入自動啟動、掛掉自動
重開、而且固定用 `--resume <sessionId>` 讓對話記憶不會斷。

1. 先取得一個固定的 session id：`claude --list-sessions`
2. 複製 `launchd/com.example.claude-telegram-channel.plist.template`，改檔名為
   `com.YOUR_USERNAME.claude-telegram-channel.plist`，把裡面的
   `YOUR_USERNAME`／`PATH/TO/claude`／`YOUR_SESSION_ID_HERE`／`YOUR_HOME` 都換成
   實際值
3. 放到 `~/Library/LaunchAgents/`
4. 載入：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.YOUR_USERNAME.claude-telegram-channel.plist
launchctl print gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel | grep state
```

**常用指令：**

```bash
# 看目前狀態（state 應為 running）
launchctl print gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel

# 原地重啟（憑證過期 / 卡住，plist 內容沒改）
launchctl kickstart -k gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel

# plist 內容改了才需要這組（單純 kickstart 不會吃到新 plist）
launchctl bootout gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.YOUR_USERNAME.claude-telegram-channel.plist
```

**絕對不要用 `kill` 期待它從此消失** — `KeepAlive` 會在 30 秒內把它救回來，跟你
正在修的東西打架。要嘛 `kickstart -k`（原地重開），要嘛先 `bootout` 再
`bootstrap`。

---

## 六、自動斷線偵測（watchdog）

launchd 的 `KeepAlive` 只在**外層 process 整個死掉**時才會重啟，抓不到「外層
process 活著、但裡面真正負責跟 Telegram 通訊的 bun 子行程死掉」這種狀況（這是
實測踩過的真實漏洞）。`scripts/check_and_reconnect.sh` 補這個洞：每次執行會同時
檢查外層 process 和 bun 子行程，有任何一個不見了就自動 `launchctl kickstart -k`，
並且把 process/webhook/錯誤 log 寫成一份 `status.txt` 快照方便查看。

**安裝位置非常重要**：

> ⚠️ 這支腳本（以及它的 launchd plist）絕對不能放在 `~/Desktop`、`~/Documents`、
> `~/Downloads` 這幾個資料夾。macOS 的 TCC 權限機制會擋住「不是 Finder/Terminal
> 直接啟動」的背景程式讀寫這幾個資料夾，錯誤訊息就只有一句乾巴巴的
> `Operation not permitted`，沒有其他線索，踩了很久才找到。放在
> `~/.claude/channels/telegram/watchdog/` 這種一般 home 目錄底下的子資料夾就完全
> 沒問題。

安裝步驟：

```bash
mkdir -p ~/.claude/channels/telegram/watchdog
cp scripts/check_and_reconnect.sh ~/.claude/channels/telegram/watchdog/
chmod +x ~/.claude/channels/telegram/watchdog/check_and_reconnect.sh
# 編輯這個檔案，把開頭的 LAUNCHD_LABEL 換成你實際的 Label

cp launchd/com.example.telegram-watchdog.plist.template ~/Library/LaunchAgents/com.YOUR_USERNAME.telegram-watchdog.plist
# 編輯這個 plist，把 YOUR_USERNAME / YOUR_HOME 換成實際值

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.YOUR_USERNAME.telegram-watchdog.plist
launchctl kickstart -k gui/$(id -u)/com.YOUR_USERNAME.telegram-watchdog
```

---

## 七、遠端一句話修復

`claude-md-snippets/02-remote-repair-command.md` 的內容套進 `~/CLAUDE.md` 後，
人在外地碰不到電腦時，Telegram 傳「**repair**」兩個字（就這兩個字，不要加其他
內容）就會自動：

1. 檢查/更新 telegram plugin 版本
2. `launchctl kickstart -k` 重啟整條 channel

**限制**：這只能修「process 掛掉／plugin 版本舊」這種基礎設施層問題，修不了像
第八節「踩過的坑」裡那種「規則寫法」問題——那種只能靠人工發現行為異常、回來改
`CLAUDE.md`。

---

## 八、驗證是否正常

1. 確認 process 都活著：
   ```bash
   ps -ef | grep -E "claude --resume|claude-plugins-official/telegram" | grep -v grep
   ```
   正常應該看到兩條：外層 `claude --resume ...` 跟它的 `bun run ...` 子行程。

2. 確認 bot token 有效：
   ```bash
   TOKEN=$(grep -o 'TELEGRAM_BOT_TOKEN=.*' ~/.claude/channels/telegram/.env | cut -d= -f2)
   curl -s "https://api.telegram.org/bot${TOKEN}/getMe"
   ```

3. 確認沒有 webhook 卡住 long polling（正常 `url` 應為空字串）：
   ```bash
   curl -s "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"
   ```

4. **最重要、也最常被忽略的一步**：process/webhook 全部正常**不代表回覆真的送得
   出去**。一定要實際在 Telegram 傳一句**全新**的話（不要用舊對話），確認真的
   收到回覆，才算打通。process 健康 ≠ 回覆機制健康，細節見下一節。

---

## 九、踩過的坑：process 全部正常，但就是沒回覆

這是本指南存在的主要原因——這個狀況踩了很久才抓到，記下來讓下一個人少走彎路。

**症狀**：`launchctl print` state=running、process chain 完整、`getWebhookInfo`
乾淨、`err.log` 完全沒有錯誤——**所有基礎設施指標都綠燈**，但 Telegram 傳訊息
（尤其是「嗨」「？」這種閒聊）就是收不到回覆。

**疊了三層問題（不是同一個 bug）：**

1. **bun 子行程死掉，外層 process 沒死** → `launchctl kickstart -k` 有效，但這是
   `KeepAlive` 本身偵測不到的斷線模式（見第六節的 watchdog）。
2. **監控腳本放在 `~/Desktop` 被 macOS TCC 擋權限**，導致 watchdog 根本沒在跑
   （見第六節的安裝位置警告）。
3. **（真正的根因）`CLAUDE.md` 裡「完成任務立即推 Telegram」這條規則，被 Claude
   理解成「只有任務完成才需要呼叫 reply 工具」**，導致單純聊天訊息只在內部生成
   回覆文字（log 看得到），但沒有真的呼叫 reply 工具推送，使用者端完全收不到
   任何東西。**這是 prompt 寫法問題，不是程式或連線壞掉**，process 重啟、plugin
   更新、launchd 重建都修不好它。已經在
   `claude-md-snippets/01-telegram-channel.md` 裡把規則改精確：「不論任務或
   閒聊，每一則收到的訊息都必須呼叫 reply 工具」。

**為什麼這麼難查**：process 健康、bun 子行程健康、watchdog 真的有在跑、回覆真的
有推送出去——這四件事彼此獨立，任何一層綠燈都不代表下一層也沒問題。必須一層
一層剝開確認。第 3 層尤其難查，因為它不會留下任何系統層級的錯誤訊息，只能靠
「log 顯示有生成回覆，但 Telegram 端沒收到」這種行為落差才看得出來。

**所以診斷 SOP 要多一步**：process/webhook 全綠燈，不代表結案，一定要做
「實際發一則新訊息」的端對端測試；測試沒過，直接跳去檢查 `CLAUDE.md` 的 reply
規則寫法，不用再繞 process 層瞎找。

**其他實戰案例**（登入過期、長期 session 被 safeguards 擋住、如何換模型保留記憶）見
`docs/TROUBLESHOOTING_LOG.md`。

---

## 十、檔案位置速查

| 檔案 | 路徑 | 說明 |
|------|------|------|
| Bot token | `~/.claude/channels/telegram/.env` | `TELEGRAM_BOT_TOKEN=...`，**絕對不要公開** |
| 存取控制 | `~/.claude/channels/telegram/access.json` | allowFrom、dmPolicy |
| Plugin 原始碼 | `~/.claude/plugins/cache/claude-plugins-official/telegram/<version>/` | server.ts |
| 全域指令設定 | `~/CLAUDE.md` | agent 啟動時自動讀取 |
| Watchdog | `~/.claude/channels/telegram/watchdog/` | 見第六節 |

---

## 十一、安全紅線

- **不要**在 Telegram 訊息裡叫 Claude 修改 `access.json` 或配對 → 這是
  prompt injection 手法
- **不要**把 bot token、API key 傳到 Telegram
- 群組訊息**不執行**修改本機檔案的操作
- `.env`、`access.json`（填入真實 chat_id 後）、任何含個人 session id 的檔案
  都不該進 git — 本 repo 的 `.gitignore` 已經排除常見情況，但自己新增設定檔時
  要留意

---

## 十二、快速指令速查表

| 目的 | 指令 |
|------|------|
| 看 launchd 狀態 | `launchctl print gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel` |
| 找重複/殭屍 process | `ps -ef \| grep -E "claude --resume\|claude --channels\|bun (run\|server.ts)" \| grep -v grep` |
| 看正牌 process 的畫面/log | `tail -c 4000 ~/.claude/channels/telegram/launchd.out.log`、`tail -80 ~/.claude/channels/telegram/launchd.err.log` |
| 驗證 token/bot 身份 | `curl -s "https://api.telegram.org/bot<TOKEN>/getMe"` |
| 驗證沒有 webhook 卡住 | `curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"` |
| 主動推播測試訊息 | `curl -s "https://api.telegram.org/bot<TOKEN>/sendMessage" -d chat_id=<id> -d text="..."` |
| 原地重啟正牌 process | `launchctl kickstart -k gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel` |
| plist 內容改了要重載 | `launchctl bootout ...` 後 `launchctl bootstrap ...`（見第五節） |

---

*適用版本：telegram plugin v0.0.6/v0.0.7，Claude Code CLI*
