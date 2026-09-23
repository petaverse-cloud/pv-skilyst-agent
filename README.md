# Skilyst Agent（pv-skilyst-agent）

音视频垂直 AI agent 客户端——单体核心 + Tauri 桌面壳。

> Skilyst AI（skilyst.ai）的官方 agent。关联：pv-beehive-core#585（M1 里程碑真源——七项已锁裁决+风险表 v2+任务表）。

## 产品形态（Wesley 2026-09-23 拍板）

- **客户端产品**（类 Claude Code / Codex / opencode 形态），**GUI 优先，CLI optional**
- **跨平台**：Windows / Linux / macOS OS 级可执行程序 + Web
- **默认模型**：deepseek-4.1-flash（备选 MiMo-V2.6-Flash）

## 架构（三档裁决落地）

| 层 | 决策 | 来源 |
|---|---|---|
| 客户端范式 | **单体自建 + 定向借鉴**（DSH 的 skill 机制/沙盒三档契约/热配置设计 + opencode 的事件钩子边界） | DSH 范式深度调研结论：Cordis 万物皆插件对垂类产品是错配 |
| 运行时 | **纯自建**（T1 PoC 路径 A 基线：1,264 行三线全 PASS——skill 加载器/manifest 协议/受限 key/沙盒声明） | 不 fork 不做兼容层，agent 核心自己打磨 |
| 桌面壳 | **Tauri**（Pawly Studio 工程链复用：签名/公证/自动更新） | 团队栈 |

## 核心能力（M1 范围）

- **SKILL.md 兼容运行时**：agentskills.io 规范零改动吃入社区 skill（T1 实测 22/22）+ 我方 manifest sidecar 规范（PR #584：fork 血缘/供应链/沙盒四维/node 依赖/i18n）
- **beehive 集成**：job 提交/轮询/素材管理/workflow 关联（受限 token：禁 admin/billing 写——服务端 scope 中间件为最紧急依赖）
- **沙盒**：三档契约（read-only / workspace-write / danger-full-access + escalation 审批流）
- **官方 skills 预载**：安装即带 + `update skills` 独立更新通道（T4 首版 5 个官方 skill：`doctor` / `embed-video` / `lipsync-audio-refs` / `prompt-craft` / `video-15s`，清单与契约见 `skills/official/README.md`）
- **作品墙 deep link**：fork-onboarding 入口

## 文档

- `docs/` — 架构决策记录（ADR）/ 规范引用
- `docs/adr/0001-runtime-layout.md` — 运行时模块划分与 agent loop 的不变量（A1）
- 调研底稿：`~/wigowago-local/research/skill-ecosystem/`（选型报告/DSH 深度调研/manifest 规范草案）

## Runtime (A1 — self-built core)

Standard library only; no install step in development.

```bash
export SKILYST_ENV_FILE=~/.skilyst/env          # 0600, holds BEEHIVE_PLATFORM_* + SKILYST_LLM_*
./bin/skilyst preload skills/official           # preload the platform-signed official bundle
python3 tools/pack_official_bundle.py --check   # bundle integrity: digests + both manifests agree
./bin/skilyst list                              # installed skills
./bin/skilyst doctor skilyst/video-15s          # integrity + live node pre-flight
./bin/skilyst inventory <skill-dir>             # community package, zero modification
./bin/skilyst run skilyst/video-15s --request "…"   # skill -> agent loop -> tool -> artifact URL
./bin/skilyst run … --dry-run                   # same chain without submitting a paid job
./bin/skilyst chat --skill skilyst/video-15s    # conversation (REPL; --message for one-shot)
./bin/skilyst authz-probe --bypass-gate         # what a restricted key gets, client- and server-side
```

Layout: `src/skills` (loader/digest/store) · `src/manifest` (sidecar spec) · `src/beehive`
(scope gate + API client) · `src/sandbox` (permission gate + resource resolution) ·
`src/llm` (chat client + router) · `src/session` (transcript/trace/artifacts) ·
`src/agent` (prompt/tools/loop) · `src/cli.py`.

Rules the loop enforces: tool availability is manifest-driven (no `permission.secrets`, no
platform tools), the credential can never reach billing/admin (client-side scope gate plus a
per-run job budget), a missing required node is blocking unless `--allow-fallback`, installed
skills are digest-verified on every load, and a job's artifact URL is HEAD-verified before the
runtime reports success. See `docs/adr/0001-runtime-layout.md`.

Tests: `PYTHONPATH=src python3 -m unittest discover -s tests` (85 tests, offline, <1s).

## 状态

M1 启动中（2026-09-23）。任务表：pv-beehive-core#585 §四。
A1 phase-1（基线迁移 + agent loop 最小核心 + CLI）已交付——见 `docs/a1-verification.md`。
