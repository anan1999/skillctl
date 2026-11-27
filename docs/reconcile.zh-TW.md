# 掃描、接管與處理本機修改

在 skillctl 專案目錄執行以下命令。請把來源、專案路徑與技能名稱換成自己的值。
Windows 可用 `python -X utf8`，避免終端機中文字編碼問題。

## 1. 找出現有技能

```powershell
python skillctl.py scan --all-agents --project C:/work/my-project
```

會讀取 Pi、Codex、Claude Code、Cursor 的標準位置，列出技能路徑、檔案雜湊、
同名副本與是否可接管。加 `--scope user` 只看使用者目錄；加 `--scope project`
只看專案及向上到 Git 根目錄的標準位置。掃描不修改技能。

## 2. 接管選定的技能

`--source` 可指向本機 Git checkout、技能集合目錄或單一技能資料夾，不必先建立
`catalog.json`。若已有 catalog，則以 catalog 為準。

```powershell
python skillctl.py --source C:/work/my-skills adopt my-skill --agent pi --project C:/work/my-project
python skillctl.py --source C:/work/my-skills adopt my-skill --agent pi --project C:/work/my-project --apply
```

第一行只預覽，第二行才記錄收據與備份。接管保留原檔案內容；來源與本機不同時，
來源會成為「接管當下」的比較基準，較早的歷史並不會被推測出來。

沒有上游來源也能接管：將 `--source` 指向一個空目錄。之後會顯示 `local-only`。
等選定上游，再先看差異並使用 keep 建立基準。巢狀技能請以 `--target` 指向該技能
資料夾的直接上層，原地納管。

## 3. 看懂更新為什麼被擋

```powershell
python skillctl.py --source C:/work/my-skills diff my-skill --agent pi --project C:/work/my-project --text
```

會顯示阻擋原因、檔案名稱、原始基準到本機／上游的文字差異。原有的 `diff` JSON
摘要仍保留；`--json` 可以取得含文字 patch 的 JSON。

## 4. 選擇保留或合併

```powershell
# 保留：全部本機內容不變，接受「這次上游更新先不採用」
python skillctl.py --source C:/work/my-skills reconcile my-skill --agent pi --project C:/work/my-project --strategy keep --text

# 合併：把上游更新併入，保留不衝突的本機修改
python skillctl.py --source C:/work/my-skills reconcile my-skill --agent pi --project C:/work/my-project --strategy merge --text
```

兩者預設都只預覽，確認結果後在選定命令末尾加 `--apply`。
同一段文字兩邊都修改、刪除與修改衝突、二進位檔兩邊不同等情況會阻擋整次套用。
不會直接把衝突標記寫進正在使用的 skill。

需要手動解決時，先將本機技能複製到另一個資料夾，依文字差異編輯成完整結果，
再使用 `--strategy merge --resolved C:/work/reviewed/my-skill` 預覽及套用。
這是以完整的審閱結果取代目標，請確認必要的檔案都在裡面。

保留過的修改會標為 `customized`，一般 `install` 仍會保護它；後續更新請繼續使用
reconcile。再次手動修改後會標為 `local-drift`。

## 5. 還原

```powershell
python skillctl.py --source C:/work/my-skills rollback my-skill --agent pi --project C:/work/my-project
```

會還原前一次操作的技能、收據與比較基準。如果又有未處理的本機修改，rollback
會先擋下來。接管的 rollback 則保留原副本並撤回管理收據。

Codex 使用 `--agent codex`；Orca 的 Pi Chat 使用 `--agent pi`。安裝與 doctor 成功
只代表檔案層通過，實際載入要在新聊天檢查讀取 `SKILL.md` 的工具紀錄。
本次結果見 [Windows 驗證](windows-verification.md)。
