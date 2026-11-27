---
name: skillctl-manager
description: "用 skillctl 按需管理本機 skills。適用於搜尋、最近 7／30 天使用次數、品質、安裝更新、本機差異、提煉經驗收據及新版／舊版比較；不用常駐服務。管理技能套件，不執行技能所控制的裝置或業務任務。"
---

# Skills 管理

以中文需求選擇一次性的 CLI 操作，回報實際結果。此 skill 使用本機 Python 3.10+、Git 與 skillctl checkout；不用開網頁或 HTTP server。

## 入口與範圍

從本 skill 的實際安裝目錄執行：

```text
python -B -X utf8 "<skill-dir>/scripts/manage.py" locations
```

入口預設讀取 `~/.config/skillctl/manager.json`；可用 `--config PATH` 選擇另一份設定，`--repo PATH` 指定 skillctl checkout。首次設定或找不到依賴時讀 [references/setup.md](references/setup.md)，依已知工作區與使用者指定位置設定，不猜測安裝目的地，也不自行下載或更換模型設定。

`locations` 會列出來源、具名目標和報告位置。涉及安裝副本的操作須選擇正確的 `--target "名稱"`；只有一個目標時可省略。若使用者說 Pi 或某專案，先匹配現有目標；無法唯一判定時才詢問。管理範圍只限這次要求，不擴大到所有 harness。

## 中文需求的處理

需要命令細節時讀 [references/commands.md](references/commands.md)。

- 「有哪些 skill／找能處理相機時間的技能」：`list --query "時間" --target "名稱"`。依名稱與描述搜尋；可根據症狀換少量關鍵字，不聲稱是完整語意搜尋。
- 「最近用了幾次／哪些常用」：`usage --days 7` 或 `--days 30`，可帶技能名稱。需要最新資料時加 `--refresh`，僅在本機掃描 Codex 紀錄；同一次詢問先刷新一次，再查另一期間，避免重複掃描。
- 「品質如何」：`quality [技能名稱] --target "名稱"`。顯示既有文字模擬結果及檔案版本是否相符；結構或安裝問題另用 `doctor`。沒有評估就是未測試，不自動呼叫模型或執行技能腳本。
- 「安裝／更新／保留修改／合併」：先用 `preview` 取得具體目標、差異和計畫 ID，再按下節處理。更新只比較已配置來源目前檔案；未取得遠端新版本時，不稱為「網路最新版本」。
- 「這次學到什麼／把經驗記下來」：用 `learn record` 保存候選理由、專案範圍與證據雜湊；用 `learn list` 或 `learn show` 查詢。這不會產生或安裝 skill，也不把經驗宣稱當作驗證通過。
- 「更新會影響什麼」：`impact 技能名稱 --target "名稱"`，查看新增／刪除文字、阻擋原因與複測建議。摘要是檔案差異，行為意義仍需核對。
- 「新版有比較好嗎」：讀 [references/learning-and-comparison.md](references/learning-and-comparison.md)。先準備同題、同模型的比較計畫與執行上限；只有獲授權的模型 adapter 才執行。記錄收據與影響摘要不會呼叫模型。

## 安裝與更新

For project version pinning or restoring skills on another machine, read
[references/project-lockfile.md](references/project-lockfile.md). Use `lock create`
or `lock restore` to preview, then `lock apply` only within the current authorization.
Protect destination edits; report kept copies separately from exact hash matches.

先預覽，向使用者說明目標、受影響檔案和衝突。預覽會保存一份本機計畫，技能內容保持不變。若使用者已明確授權該技能在該目標的安裝／更新，且預覽符合範圍、沒有衝突，可接續 `apply PLAN_ID --target "名稱"`，不重複索取同一授權；僅問「有什麼更新／先看看」則停在預覽。

遇到 local drift、未納管或保留的自訂內容，展示差異並說明接管、保留或合併的影響。未指定策略時請使用者選擇，不以重新安裝、刪除副本、手改收據等方式解除阻擋。計畫十分鐘到期、已使用或檔案／收據有變時，重新預覽；不要自行修改計畫或重試舊 ID。

套用後重新查 `list` 與 `doctor`，核對檔案與收據。檔案安裝成功不代表另一個 harness 已載入；需要實際載入驗證時須另依使用者要求執行。

## 回報尺度

使用量是 **Codex 本機紀錄中確認讀取 SKILL.md 的最低次數**，按技能名稱跨副本彙總；重讀與維護可能計入，不等於完成任務、成功率或目前版本用量。附上期間、快照時間及不完整／過期提示；不支援的 harness、沒有設定或錯誤不能報成 0。

用中文摘要結果，必要時列出技能、讀取次數、對話數與品質狀態。不要把品質「通過」說成實際裝置驗證。保持本機紀錄、報告、路徑與計畫私有；本 skill 不自行上傳、啟動服務或操作板子／相機。
