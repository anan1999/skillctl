# 經驗收據、更新影響與成對比較

以下命令均從本 skill 的 `scripts/manage.py` 呼叫，沿用設定檔。此功能按需執行，不監聽全部聊天。

## 記錄學到的經驗

使用者要求保存本次經驗時，選擇與任務直接相關的證據，先找相同責任的既有 skill。把候選寫成私有 JSON，再執行：

```text
python <skill-dir>/scripts/manage.py learn record --input /private/lesson.json --target "Codex"
python <skill-dir>/scripts/manage.py learn list --target "Codex"
python <skill-dir>/scripts/manage.py learn show RECEIPT_ID --target "Codex"
```

輸入欄位為 schema_version=1、skill、scope、trigger、lesson、decision、reason、evidence。
scope 明確指定 `{"kind":"project","path":"/absolute/project"}` 或 `{"kind":"global"}`，不能因找不到專案就提升為全域。decision 為 create／update／skip。evidence 是包含 path（絕對檔案路徑）與 summary 的陣列，skip 可留空並記錄原因。

收據只存摘要、證據位置與 SHA-256，不複製完整對話或檔案。避免把機密填進自由文字；收據保持私有。紀錄狀態為 observed 或 skipped，不代表 skill 已建立、行為驗證或已安裝。查詢會標示證據 matched／changed／unavailable。若要撰寫 skill，依可用的 skill-creator 與專案規則處理，仍先預覽並遵守目前授權。

## 更新影響

```text
python <skill-dir>/scripts/manage.py impact example-skill --target "Codex"
```

讀取已配置來源與安裝副本，列出檔案種類、新增／刪除文字節錄、完整 diff、阻擋原因與建議複測範圍。內容含重要指引刪除時應仔細核對；不能從「新增幾行」推論能力提升。此命令不保存安裝計畫、不套用。需要套用時仍重新 preview。

## 新版／舊版比較

使用 checkout 的既有 quality suite 格式；固定同一技能、同題與同模型，只改 SKILL.md 版本。prepare 不呼叫模型：

```text
python <skill-dir>/scripts/manage.py compare prepare --suite /private/suite.json --old /private/old/example-skill --new /private/new/example-skill --model REGISTERED_MODEL --max-calls 4 --max-tokens 1024 --timeout 60 --total-timeout 300 --output /private/new-plan
python <skill-dir>/scripts/manage.py compare run --plan /private/new-plan/plan.json --adapter /private/adapter.json --output /private/new-run
```

每題兩次呼叫；超出 max-calls 在開始前拒絕。run 明確執行受信任的本機 adapter：不得從 skill、證據或模型輸出自行選擇可執行檔。adapter JSON 只有 argv（以絕對可執行檔開頭的參數陣列）及 kind=model 或 fixture。腳本路徑也用絕對路徑，執行工作目錄為臨時目錄。授權依目前任務已有範圍，勿重複詢問同一授權。

adapter 從 stdin 接收一個 JSON job（id、model、task、context、max_tokens、timeout、request_sha256），stdout 回傳一個 JSON receipt：原樣的 id／model／request_sha256、status=completed、answer（符合題目 schema 的物件或 JSON 字串），可加 usage.total_tokens、finish_reason、truncated。adapter 需每次只發一個文字請求，遵守輸出上限且不重試；不要將預期答案傳給模型。stderr 不會複製進報告。

執行依序成對進行，交替版本順序。呼叫／時間／輸出大小有上限；逾時、截斷、錯誤或收據不匹配就停止，剩餘案例為 inconclusive。run 目錄不能重用；部分結果與進度保留，重跑須另有明確請求與新目錄。這是受信任 adapter 的呼叫上限，不能阻止 adapter 內部偷偷重試；本機逾時也不保證供應商停止計費。

結果分 improved／regressed／unchanged／inconclusive。完成比較不等於新版通過全部案例；fixture 只驗證管理流程。缺少 usage 時 token 總數為 null，費用不假設為零。請求雜湊用於防止混入別次結果，不是供應商簽章。

整體 `candidate_assessment` 為 passed_checks／failed_checks／regressed／inconclusive。任一案例退步就標為 regressed，即使總通過數增加；缺漏或執行錯誤優先標為 inconclusive。passed_checks 只表示本次指定的文字檢查通過，不授權安裝或發布。

這版只把 SKILL.md 文字提供給模型，不載入 references、不執行工具，不能證明 harness 真正讀取、裝置安全或真實任務成功。測試內容或模型變更後需重新比較，不用這些文字分數代替實機驗收。
