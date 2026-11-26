# 首次設定

需要 Python 3.10+、Git 與本機 skillctl checkout（含 skill_manage.py）。技能資料夾提供入口和流程；核心仍由 checkout 的 skillctl.py 執行，沒有第二份安裝邏輯。

如果已配置，不重建設定或改動現有來源／收據。依序採用使用者指定的 `--repo`、環境變數 `SKILLCTL_REPO`、設定檔的 `repo`，最後才查入口所在的父目錄是否為 checkout。找不到時詢問 checkout 位置；取得新 repo 或更新來源需有使用者要求。

設定檔預設是 `~/.config/skillctl/manager.json`。所有路徑使用實際絕對路徑；下例是格式示意，請換成已核對的位置：

```json
{
  "schema_version": 1,
  "repo": "/absolute/path/to/skillctl",
  "source": "/absolute/path/to/skills-source",
  "state": "/absolute/path/to/skill-manager-state",
  "targets": {
    "Codex": "/absolute/home/.codex/skills",
    "Project Pi": "/absolute/project/.pi/skills"
  },
  "codex_home": "/absolute/home/.codex",
  "usage_report": "/absolute/private-reports/usage.json",
  "quality_report": "/absolute/private-reports/quality.json"
}
```

- `source`：已有技能來源根目錄，可包含 catalog.json 或可發現的技能目錄。
- `state`：沿用原有 skillctl 收據目錄。更換它會失去原有納管及復原歷史的連結。
- `targets`：具名安裝位置，不要把 Codex user scope 的 `.agents/skills` 和既有 `.codex/skills` 自動當成同一份副本；以實際目錄為準。
- `usage_report`、`quality_report` 是選用欄位；沒有報告時省略或設 null。尚無使用報告但已配置 `codex_home` 和輸出位置，可以執行 usage --refresh 建立。
- `codex_home` 只供離線使用量收集，不讀取登入金鑰。品質查詢不生成新的模型評估。

在 Windows 使用 JSON 的正斜線路徑，或正確跳脫反斜線。設定檔不含金鑰，仍不應上傳私人位置。

驗證入口：

```text
python -B -X utf8 "<skill-dir>/scripts/manage.py" --repo "/absolute/path/to/skillctl" --config "/absolute/path/to/manager.json" locations
```

安裝此 skill 可使用 checkout 的原有 CLI（下例先預覽，再於已授權時套用）：

```text
python skillctl.py --source "/absolute/path/to/skillctl" install skillctl-manager --agent codex --scope user --dry-run
python skillctl.py --source "/absolute/path/to/skillctl" install skillctl-manager --agent codex --scope user
```

不同 harness 的技能載入方式由該 harness 決定；安裝後若目前對話尚未發現，可在新對話呼叫，或明確請 AI 讀取安裝後的 SKILL.md。這不要求任何常駐服務。
