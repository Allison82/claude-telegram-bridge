# 群組設定 SOP：讓 bot 在 Telegram 群組裡被 @ 觸發

預設的 telegram plugin 只支援 1 對 1 私訊。要讓 bot 在群組裡工作，需要額外設定——而且官方
`ACCESS.md` 文件裡少寫了一個關鍵步驟（Privacy Mode 改動後必須把 bot 踢除、重新加入群組，
設定才會真正生效），這份 SOP 是實測踩過坑之後整理出來的完整流程，照著做可以少走很多冤枉路。

## 步驟一：取得群組的 chat_id

群組 ID 是負數，Telegram 介面上不會直接顯示，要用工具查：

- **方法 A**：群組裡暫時加入 [@RawDataBot](https://t.me/RawDataBot)，它會自動吐出一包含
  `"chat":{"id": ...}` 的 JSON，複製數字後可以把它移除，不影響群組。
- **方法 B**：用類似「Get My ID」這種查詢 bot，選 "Select group chat"，把你的群組分享給它。

Supergroup（超級群組）的 ID 開頭通常是 `-100`（例：`-1001654782309`）；一般（非超級）群組只是
負數、沒有 `-100` 前綴（例：`-543xxxxxxx`）。兩種實測都能用這份 SOP 打通。

## 步驟二：把群組加進 access.json

```bash
python3 - << 'PYEOF'
import json
import os

path = os.path.expanduser("~/.claude/channels/telegram/access.json")

with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

data.setdefault("groups", {})
data["groups"]["YOUR_GROUP_CHAT_ID"] = {
    "requireMention": True,   # true = 只有 @ 它或回覆它的訊息才會觸發
    "allowFrom": []           # 空陣列 = 群組內任何人都能觸發；要限制特定成員就填他們的數字 ID
}

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print("done")
PYEOF
```

把 `YOUR_GROUP_CHAT_ID` 換成步驟一查到的數字（保留引號，是字串 key）。這個檔案是即時生效的
（server 每次收到訊息都會重新讀取），理論上不用重啟。

## 步驟三（最容易漏掉的一步）：Privacy Mode

官方文件說：預設 `requireMention: true` 的情況下不需要關閉 Privacy Mode，Telegram 本來就會
把 @ 到 bot 的訊息送過去。**但實測發現就算這樣設定完全正確，群組訊息還是完全送不到本機程式**，
原因是：

1. 這個 bot 的 Privacy Mode 當時是開著的
2. 開著的狀態下，就算理論上 @ 訊息該放行，實際行為不一定如文件描述

保險做法：直接把 Privacy Mode 關掉。

1. 私訊 [@BotFather](https://t.me/BotFather)，打 `/mybots`
2. 選你的 bot → **Bot Settings** → **Group Privacy**
3. 按 **Turn off**（目標狀態要變成 `DISABLED`）

**🔴 關鍵：改完 Privacy Mode 之後，一定要把 bot 從群組「移除」、再「重新加入」一次。**
Telegram 不會把新的 Privacy 設定套用到已經存在的 bot-群組綁定上，只改 BotFather 設定、不踢
除重拉，群組還是會完全收不到訊息，這個現象反覆測試確認過，踢除重拉之後才真正打通。

順序錯了也沒關係（例如先踢除重拉、後改 Privacy），只要保證「踢除重拉」這個動作發生在「改完
Privacy Mode 之後」，再做一次踢除重拉即可。

## 步驟四：保險重啟背景程式

```bash
launchctl kickstart -k gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel
```

理論上 access.json 不用重啟，但程式本身的連線狀態保險起見重啟一次。

## 步驟五：測試

回群組 @ 一次 bot，應該會收到回覆。

## 除錯檢查清單（由上而下排查）

- [ ] `access.json` 的 `groups` 裡有沒有這個 chat_id、格式對不對（字串 key、`requireMention`
      拼字正確）
- [ ] 背景程式還活著嗎（`ps -ef | grep channels`）
- [ ] 私訊 bot 正常回覆嗎（排除登入過期這類更根本的問題，私訊都不通的話群組當然也不會通）
- [ ] BotFather 的 Group Privacy 是 `DISABLED` 嗎
- [ ] **改完 Privacy 之後，有沒有把 bot 踢除、重新加入群組？**（最常被漏掉的一步）
- [ ] 如果都做了還是不通，部署一個即時 log 檢視工具（見 `docs/SETUP.md` 的事故複盤章節），
      直接看訊息有沒有送達本機程式，藉此判斷是 Telegram 端沒送達、還是本機程式處理邏輯的問題
