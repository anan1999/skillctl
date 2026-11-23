# Design references

Reviewed on 2026-11-23. These projects informed workflow choices; no source code
was copied and these notes are not a usage or market-share ranking.

| Tool | Useful pattern | Applied here |
| --- | --- | --- |
| [Vercel Skills](https://github.com/vercel-labs/skills) | Select agent targets, inspect installed state, track content hashes and restore recorded versions | Explicit harness targets, read-only previews, per-file receipts and preserved upstream snapshots |
| [OpenSkills](https://github.com/numman-ali/openskills) | Install ordinary local skill directories; discover skills and load them on demand | Optional catalog, direct local bundle discovery, selected in-place adoption |
| [Skillfish](https://github.com/knoxgraeme/skillfish) | Manage and synchronize skills across agents with an understandable CLI workflow | One set of scan/adopt/diff/reconcile semantics for four harnesses |
| [Anthropic skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) | Evaluate actual prompts and skill behavior | Separate filesystem tests from actual agent load evidence; no claim that copying files proves behavior |
| [Superpowers](https://github.com/obra/superpowers) | Harness-specific installation and runtime skill activation | Test native discovery in Codex and Pi, rather than injecting skill contents into test prompts |

The distinguishing workflow is local customization preservation: explain which
files block an update, show base/local/upstream text, and offer explicit keep or
merge with verified snapshots and rollback. Link-based distribution, URL fetching,
AGENTS.md generation, team lockfile interoperability and a new registry service
are not part of this release. Existing tools can remain the source acquisition
layer; use their local checkout as `--source`.
