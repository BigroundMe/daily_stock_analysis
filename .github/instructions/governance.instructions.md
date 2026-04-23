---
description: "Use when working on docs, CI/CD workflows, Docker, scripts, or AI governance assets. Covers release flow, workflow configuration, and documentation sync rules."
applyTo: "README.md,docs/**,AGENTS.md,CLAUDE.md,.github/**,.claude/skills/**,scripts/**,docker/**,strategies/**"
---

# Governance Instructions

## 核心原则

- 保持命令、文件路径、工作流名称、配置键、发布路径与实际仓库状态一致。
- `AGENTS.md` 是 AI 协作规则的唯一真源；若其语义变更，需同步 `CLAUDE.md`、`.github/copilot-instructions.md`、`.github/instructions/*.instructions.md` 和 `.claude/skills/`。
- 根目录 `SKILL.md` 和 `docs/openclaw-skill-integration.md` 是产品/外部集成说明，不是仓库协作规则。
- 禁止扩大权限、暴露密钥或引入破坏性自动化。
- 变更中英双语文档之一时，评估另一份是否需要同步；若未同步，交付说明中写明原因。

## CI 流水线

| 检查项 | 工作流 | 阻断级别 |
|---|---|---|
| `ai-governance` | `ci.yml` | 阻断 |
| `backend-gate` | `ci.yml` → `scripts/ci_gate.sh` | 阻断 |
| `docker-build` | `ci.yml` | 阻断 |
| `web-gate` | `ci.yml`（前端变更时触发） | 阻断 |
| `network-smoke` | `network-smoke.yml`（工作日定时） | 观测 |
| `pr-review` | `pr-review.yml` | 辅助 |

### ci_gate.sh 四阶段

1. `syntax`：`python -m py_compile` 编译核心文件
2. `flake8`：仅致命错误（E9/F63/F7/F82）
3. `deterministic`：`./test.sh code` + `./test.sh yfinance`
4. `offline-tests`：`pytest -m "not network"`

支持单阶段运行：`./scripts/ci_gate.sh [all|syntax|flake8|deterministic|offline-tests]`

## 发布流程

```
commit 含 #patch/#minor/#major → push main
  → auto-tag.yml（自动打 annotated tag）
    → 三条并行发布：
      ├─ create-release.yml → GitHub Release
      ├─ docker-publish.yml → Docker 镜像发布（先跑 ci_gate.sh）
      └─ desktop-release.yml → Win/macOS 桌面端打包
```

- 自动 tag 默认 opt-in：仅 commit title 含 `#patch`/`#minor`/`#major` 才触发
- 手动 tag 必须使用 annotated tag
- 初始版本 `2.1.0`，默认 bump 为 patch

## Docker 配置 (docker/)

- 多阶段构建：`node:20-slim` 打包前端 → `python:3.11-slim-bookworm` 运行后端
- 时区固定 `Asia/Shanghai`，端口 `${API_PORT:-8000}`
- 持久化卷：`/app/data`、`/app/logs`、`/app/reports`
- 健康检查：30s 间隔，`/api/health` 或 `/health`
- 双服务模式：`analyzer`（定时任务）+ `server`（API 服务）

## 文档约定

- `README.md`：入门、运行、部署、核心能力总览
- `docs/`：模块行为、页面交互、专题配置与排障说明
- `docs/CHANGELOG.md` `[Unreleased]`：扁平格式，每条 `- [类型] 描述`，禁止在 `[Unreleased]` 内新增 `### 类目标题`
- 若未更新 `README.md`，交付说明中写明原因和实际文档位置

## AI 资产治理

- 修改治理资产时执行：`python scripts/check_ai_assets.py`
- `.claude/skills/` 存放仓库协作 skill，`.claude/reviews/` 存放分析产物
- 禁止手工长期维护多份同义内容，必须先明确单一真源

## 策略文件 (strategies/)

- YAML 格式：元数据（name/display_name/category/required_tools/aliases/priority/market_regimes）+ Markdown `instructions` 正文
- 修改策略时保持元数据字段完整性和 instructions 中的量化判定标准
