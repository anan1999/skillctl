# 技能品質評估（文字模擬）

`skill_quality.py` 以一組固定的合成案例，檢查「模型只讀到某個技能的 `SKILL.md` 時，文字回答是否符合預期」。
它只使用 Python 標準庫，不連網、不呼叫模型、不執行任何技能腳本；模型呼叫由你自己的 Gateway 或其他供應商完成，再把回應帶回來評分。

## 限制（請先閱讀）

- 這是**文字模擬**：模型沒有工具、沒有裝置、沒有檔案系統，所有「動作」只是 JSON 裡的識別字串。通過不代表 agent 實際載入技能、遵守安全規則，或在真實 harness／裝置上行為正確。
- 只有 `SKILL.md` 會放進提示。技能資料夾內被引用的檔案（例如 `refs/notes.md`）會被雜湊並納入版本指紋，但**不會自動載入**給模型。
- 評分完全是確定性的 `eq` 比對（布林不等於數字），不用模型評分模型。
- 12 題是小型 pilot，只能作為回歸訊號，不是完整品質保證。報告不含簽章，只是本機匯入的證據，不證明來源真實性。

## 隱私與本機提示

- `plan.json` 與 `gateway-batch-*.json` 含有技能的 `SKILL.md` 全文，屬於**私有本機檔案**，不要公開或提交到版本庫。
- 提示不含預期答案、`checks`、技能根目錄路徑或任何憑證；請不要在技能或案例文字中放入密碼、Token 或個人資料。
- 評分器不會把完整提示、`SKILL.md` 或技能根目錄附加到報告；報告包含相對檔名、SHA-256、模型名稱、案例結果與回答中的受檢欄位。模型可能在回答中重述輸入，因此公開報告前仍需檢查內容。

## 可重現流程

```
python skill_quality.py prepare --suite quality/pilot.json --skill-root DIR --model MODEL --output OUT
python skill_quality.py grade --plan OUT/plan.json --receipt RECEIPT1.json [--receipt RECEIPT2.json ...] --output report.json
```

1. `prepare`：`DIR` 下需有套件使用的每個技能資料夾（資料夾名稱即技能名稱，不可是連結）。會寫出 `OUT/plan.json`，以及每批最多 4 個工作的 `OUT/gateway-batch-1.json`、`gateway-batch-2.json`…，格式為 `{"jobs": [...]}`。每個工作含 `id`（案例 ID）、`model`、`task`、`context`、`max_tokens`（1800）、`timeout`（120）。
2. 把每個批次交給 Gateway 執行，取得 receipt（含 `jobs` 清單）。
3. `grade`：`--receipt` 可重複，所有 receipt 的 `jobs` 會合併。輸出報告並以結束碼表示結果：`0` 全部通過、`1` 有案例未通過或錯誤、`2` 輸入格式錯誤（不寫報告）。檔案可為含 BOM 的 UTF-8。

`plan.json` 記錄 `suite_sha256`（套件正規化 JSON 的 SHA-256）；評分時會重新驗證套件雜湊、案例與技能的對應。CLI 匯入還會核對 receipt 原始 `task`、`context`、`model`，或核對 `request_sha256`。舊技能／不同提示產生的回答不能直接套進新計畫。

### 如何提供其他供應商的回應

不需要 Gateway：自行用任何供應商執行每個工作的 `task` + `context`（`max_tokens`/`timeout` 為建議值），再把結果整理成 receipt：

```
{"jobs": [{"id": "log-first-error", "model": "MODEL", "status": "completed",
           "request_sha256": "<對實際送出的工作呼叫 request_digest(job) 所得的值>",
           "finish_reason": "stop", "response": "{\"first_error_line\": 3, ...}",
           "elapsed_seconds": 4.2, "usage": {"input_tokens": 900, "output_tokens": 80}}]}
```

- `id` 必須對應案例 ID、`model` 必須等於 plan 的模型；`status` 必須明確是 `completed`，缺少也不會通過。`finish_reason`／`stop_reason` 若提供必須是正常結束（`stop`、`end_turn`、`completed`），`length`、`max_tokens` 或 `truncated: true` 視為截斷。
- Adapter 應對實際送出的 `id/model/task/context` 計算 `skill_quality.request_digest(job)`，並隨回答保存 `request_sha256`；也可直接保存原始 `task/context`。Gateway delegate 的原始 receipt 已保留這些欄位。雜湊是避免誤配的完整性檢查，不是供應商簽章。
- 回應文字放在 `response`（也接受 `text`、`output`、`content`、`result`、`message`、`answer`）。內容必須是**單一 JSON 物件**，不能有 markdown 圍欄、重複鍵、`NaN`，欄位必須與 `response_schema` 的鍵完全相同。
- `elapsed_seconds` 與 `usage` 只在提供時才記錄；不會推算費用。

缺少回應、重複回應、模型不符、未完成／截斷、無效 JSON、欄位缺漏或多餘，都不會通過，且仍計入 12 題分母（案例狀態為 `error`）。

## 報告與在管理頁顯示

報告為 `schema_version: 1`、`mode: "text-simulation"`，含 `model`、`created_at`、`suite_sha256`，以及每個技能的 `files`（檔名→SHA-256）與 `cases`（每題的 `status`、`checks` 的 `path/expected/actual/passed`、必要時的 `error`）。

要在技能詳情顯示結果，啟動管理頁時指定報告（可選）：

```
python skill_dashboard.py --quality-report report.json
```

- 管理頁只讀取這個明確指定的檔案（上限 2 MiB），不接受從網頁傳入任何路徑，也不會自動執行評估或新增寫入操作。
- `GET /api/quality?target=ID&name=SKILL` 與其他 API 使用相同的 Host／Origin／Token 驗證；未知位置或不合法名稱回傳 400。報告本身無效時回傳 HTTP 200 與 `status: "error"`，不影響差異、診斷與安裝功能。
- 面板顯示通過比例、`未通過`、`未測試`、`已過期` 或 `報告錯誤`，並說明這是「文字模擬」、列出模型／日期／版本、提示是否核對，以及每題的預期與實際值。重新計算每個檢查，不信任報告內的 `passed` 旗標。直接使用 Python `grade()` 的離線測試資料若缺少請求雜湊，會標示尚無提示對應紀錄。

### 雜湊與過期

評估時記錄技能每個檔案的 SHA-256。目前已安裝的技能檔案只要有任何新增、刪除或修改（包含被引用的檔案），狀態就會變成 `已過期`，必須重新 `prepare`／評估。技能尚未安裝但報告有紀錄，同樣視為過期。報告中沒有該技能則為 `未測試`。

## 測試

```
python -B -X utf8 -m unittest discover -s tests -p "test_quality*.py"
```

品質瀏覽器測試使用 Playwright 與 Chrome：`node tests/dashboard_quality_browser.cjs`。
可用 `SKILLCTL_PLAYWRIGHT` 指定 Playwright 模組、`SKILLCTL_PYTHON` 指定 Python。
所有會改檔案的測試都在獨立暫存 fixture 執行。

另可對已安裝的 `failure-triage` 摘錄腳本執行合成日誌測試（一般日誌、十萬行日誌、無錯誤日誌）：

```
python quality/helper_smoke.py --skill-dir /path/to/failure-triage --output .demo/helper-result.json
```

這項是實際本機腳本測試，與模型文字模擬分開；不連接任何設備。輸出目錄須已存在。
