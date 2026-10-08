# claude-telegram-bridge

24 小時跑在你自己電腦上的 Claude Code，透過 Telegram 遠端操控/對話 —— 不是玩具
demo，是一套實測過、踩過好幾個坑之後整理出來的正式常駐架構：

- **launchd 常駐**：開機/登入自動啟動、process 掛掉自動重開、固定 session 讓
  對話記憶不會斷
- **Watchdog 補洞**：launchd 的 `KeepAlive` 抓不到「外層活著、內層子行程死掉」
  這種斷線，另外補一支背景監控腳本
- **遠端一句話修復**：人在外地碰不到電腦時，Telegram 傳「repair」自動更新
  plugin + 重啟服務
- **把實測踩過的坑都記下來**：包含一個「process/webhook 全部正常、但就是沒
  回覆」的真實案例，根因其實是 prompt 寫法問題，花了很久才抓到，詳見
  `docs/SETUP.md`

## 快速開始

1. 讀 `docs/SETUP.md` 完整走一遍
2. 用 `templates/`、`launchd/` 底下的範本建立你自己的設定檔（把
   `YOUR_USERNAME`、`YOUR_CHAT_ID`、`YOUR_BOT_USERNAME`、`YOUR_SESSION_ID_HERE`
   換成實際值）
3. 把 `claude-md-snippets/` 兩個檔案的內容加進你的 `~/CLAUDE.md`

## 目錄結構

| 路徑 | 說明 |
|------|------|
| `docs/SETUP.md` | 完整安裝教學（中文），含 launchd 常駐、watchdog、遠端修復、踩過的坑 |
| `launchd/*.plist.template` | launchd plist 範本，填入你的路徑和 session id |
| `scripts/check_and_reconnect.sh` | 斷線自動偵測 + 重連腳本 |
| `templates/access.json` | access.json 範本，填入你的 chat_id |
| `claude-md-snippets/` | 要加進 `~/CLAUDE.md` 的規則片段 |

## 相容性

- macOS（zsh）
- Claude Code CLI
- telegram plugin v0.0.6 / v0.0.7
- Bun runtime

## 安全提醒

這個 repo 裡**沒有**、也**不應該**包含任何真實的 bot token、chat_id、session id
或其他個人資料——全部都是佔位符，照著文件填你自己的值。如果你 fork 或修改後要
推自己的版本，推送前務必自己檢查一次有沒有不小心把 `.env`、填了真實值的
`access.json` 之類的檔案加進去。

---

*詳細說明見 `docs/SETUP.md`*
