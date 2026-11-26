# 一次性操作

以下 `manage` 代表 `python -B -X utf8 "<skill-dir>/scripts/manage.py"`。入口的 --repo 與 --config 可放在命令前；所有輸出是 JSON，請由 AI 轉為中文摘要，不需讓使用者記住參數。

| 中文需求 | 指令 |
|---|---|
| 有哪些管理位置 | `manage locations` |
| 找時間相關 skill | `manage list --target "Codex" --query "時間"` |
| 最近 7 天哪些常用 | `manage usage --days 7 --refresh` |
| 某技能最近 30 天 | `manage usage example-skill --days 30` |
| 看品質結果 | `manage quality example-skill --target "Codex"` |
| 看結構、收據、重複副本問題 | `manage doctor example-skill --target "Codex"` |
| 預覽安裝 | `manage preview install example-skill --target "Project Pi"` |
| 預覽更新 | `manage preview update example-skill --target "Project Pi"` |
| 納管現有副本，不改內容 | `manage preview adopt example-skill --target "Project Pi"` |
| 保留修改並更新比較基準 | `manage preview keep example-skill --target "Project Pi"` |
| 合併本機與來源修改 | `manage preview merge example-skill --target "Project Pi"` |
| 預覽還原上一筆操作 | `manage preview rollback example-skill --target "Project Pi"` |
| 套用已授權的預覽 | `manage apply PLAN_ID --target "Project Pi"` |

usage 未選目標時是全機 Codex 統計。指定 Pi 等不支援的目標會回覆 unsupported，不掃描、不產生 0。支援的 Codex 目標以 `.codex/skills` 或 `.agents/skills` 辨識。7／30 天是相同快照截至時間往前的連續期間。

品質狀態：passed＝文字模擬案例通過；failed＝有失敗案例；stale＝目前檔案已不同；untested＝無對應評估；error＝報告或檔案有問題。doctor 是結構檢查，不能替代品質案例。

預覽結果若 `ok=false` 或 `plan_id=null`，沒有可套用的計畫。unchanged 表示已相同，不須重裝。可用的計畫在 state 下保存十分鐘，綁定來源、目標、檔案及收據，套用只用一次。這是版本與併發保護，不是使用者授權的替代品。CLI 離開碼：0＝命令完成；1＝檢查未通過或操作被阻擋；2＝參數、設定或檔案錯誤。查詢完成後仍須查看各技能 status，不能只憑離開碼稱為通過。

不修改服務、模型設定或設備。需要手動解決重疊衝突時先提供衝突檔案與位置，保留現況；原有 skillctl CLI 也支援明確提供已解決目錄的進階流程，但不要自行推測解決內容。
