# 使用紀錄與品質檢定

管理頁面選取技能後，可以在品質面板上方切換「最近 7 天」「最近 30 天」，查看確認讀取次數、涉及對話數與最近讀取時間。

## 產生與更新本機快照

```powershell
python skill_usage.py collect --codex-home "$env:USERPROFILE/.codex" --output .demo/usage-report.json
python skill_dashboard.py --source /path/to/upstream --target "Codex=/path/to/.codex/skills" --usage-report .demo/usage-report.json --quality-report .demo/quality-report.json --open
```

請換成自己的技能來源和 Codex 技能目錄。品質報告是選用參數；尚未產生時可以省略。

需要新數字時，再執行同一個 collect 指令。它會原子更新有效的舊使用報告；不會覆蓋其他格式的檔案。回到頁面切換期間，或重新掃描並選取技能，即可讀取更新後的快照。頁面本身不掃描對話，也不自動定期收集。超過 24 小時的快照會提示更新。

兩個期間共用快照截至時間，採用「起點包含、終點不包含」的連續 7／30 天，不是日曆週或月份。日期在報告內使用 UTC，頁面依瀏覽器時區顯示。可用 `--as-of 2026-11-26T00:00:00Z` 重現指定截至時間。

## 數字的意思

- **確認讀取次數**：工具呼叫明確讀取某技能的 SKILL.md，成功輸出含有相符的完整 frontmatter，才計一次。同一呼叫重讀同名技能只算一次。
- **涉及對話數**：上述讀取發生於多少個不同 Codex 對話。
- 統計依技能名稱彙總所有本機 Codex 對話；**不是目前選取安裝副本或版本的使用量**。同名不同版本也會合併。
- 重讀文件、維護技能等操作可能計入。確認讀取不代表遵守了指令、執行了腳本或完成了任務，不能用來推算成功率。
- 品質面板仍獨立核對目前技能檔案的雜湊；使用統計不會替技能頒發品質通過標記。

目前只支援 Codex 的 `sessions/**/*.jsonl` 和 `archived_sessions/**/*.jsonl`。Pi、Claude Code、Cursor 與無法辨識的安裝位置顯示「尚未支援」，不冒充 0 次。Codex 位置以 `.codex/skills` 或 `.agents/skills` 結尾辨識。

## 證據與限制

解析器配對 response_item 的工具呼叫與回傳；不計名稱提及、啟動技能清單或 event_msg 重複事件。重播事件會去重，對話建立時間之前的繼承紀錄會排除。

支援直接的 ReadAllText、Get-Content、cat、head、簡單 sed、Path(...).read_text()、原生 read/read_file，以及 functions.exec 內可解析的 exec_command 字串。只辨識明確的字面路徑；不是完整 shell 直譯器。動態路徑、複雜管線、工具輸出截斷、未配對的非同步回傳、壓縮或刪除的對話可能漏計。不同工具版本的輸出格式也可能尚未支援。因此數字是**可確認的最低次數**，0 只代表沒有找到這類證據。

失敗回傳即使帶有文件文字也不計入。資料損壞、過大的紀錄行、讀取失敗或缺少成功回傳會記錄在 coverage，頁面顯示不完整提示。單行上限 8 MiB；掃描不追蹤符號連結或 junction。收藏的每個檔案以開始讀取時的大小為限，避免追逐仍持續寫入的紀錄。

未指定快照顯示「尚未配置」；檔案讀取失敗、格式錯誤或計數不合理顯示錯誤。只有有效快照中沒有對應證據時才顯示 0。

## 隱私與示範

收集過程離線執行，不呼叫模型、不傳送對話、不操作板子或相機。輸出只有技能名稱、次數、日期和覆蓋統計，不含對話原文、session/call ID 或原始紀錄路徑。技能名稱和使用頻率仍屬個人資訊，建議存放在 Git 忽略的 `.demo/`，不要上傳。

`python quality_demo.py --open` 使用合成資料顯示使用紀錄和品質狀態。選取 failure-triage 可看到 7 天 3 次／2 個對話、30 天 8 次／4 個對話；這不是任何人的真實使用量。

驗證：`python -m unittest discover -s tests -v`。如已安裝 Playwright 與 Chrome，可執行 `node tests/dashboard_usage_browser.cjs`；自訂位置可透過 SKILLCTL_PLAYWRIGHT、SKILLCTL_PYTHON 環境變數提供。
