# M2 macOS 真机测试 — 发现的问题清单

日期：2026-09-24 · 机器：本机 macOS（Apple Silicon）· 基线 `d110ac9`
过程与证据见 `docs/m2-macos-verification.md` 与同目录截屏/日志。

分三类：**A = 已在本 PR 修**（代码缺陷）· **B = 环境限制**（不是产品缺陷，但决定验收怎么做）·
**C = 已记录、未修**（A2 phase-2 待办）。

## A. 代码缺陷（本 PR 已修，含回归测试）

### A1. REPL 提示符污染 stdout
`skilyst chat` 的 `input("skilyst> ")` 把提示符写进标准输出，与"JSON 进 stdout、过程进 stderr"
的约定冲突：`skilyst chat < request.txt | jq` 会拿到 `skilyst> ` 前缀。
**实测**：管线跑完后 stdout 正好是 `skilyst> skilyst> `（18 字节，无结果）。
**修复**：提示符改写到 stderr；`chat` 的 stdout 只剩结果 JSON。

### A2. `beehive_submit_job` 的默认节点是"第一个声明的"，而不是"第一个必需的"
embed-video 先声明**可选**的静帧节点 `generate:nb2-image`，再声明必需的 `generate:minimax-h3`。
旧逻辑 `default_node = node_ids[0]` 于是让"不带 node_id 的提交"打到静帧节点上——同价、错产物。
**修复**：默认取第一个 `optional: false` 的节点；工具描述里明写默认值。测试
`test_default_node_is_the_required_one_not_the_optional_helper` 钉住。

### A3. manifest 声明了 `binding`，运行时从不解析（M1 遗留的记录缺口）
官方 5 个 skill 的每个节点都写了 `requires.nodes[].binding`（tool / node_id / config_map），
但运行时从未读取：工具参数是硬编码的 `prompt/duration/resolution/ratio`，`images` /
`image_roles` / `audio_refs` / `text` 传不进去，embed-video 与 lipsync 的端到端必须绕过 agent
直接打 API。
**修复**：manifest 层解析+校验 binding；工具参数面 = 声明的 config_map 并集；字段名按 config_map
映射（`ratio → aspect_ratio`）；类型/枚举/required 取自集群 `input_schema`；未声明的参数**拒绝而
非丢弃**；`images`/`image_roles` 位置配对本地先校验。真机付费验证见文档线 4。

### A4. REPL 会话标题恒为 `interactive session`
CLI REPL 建的会话标题是写死的，桌面壳列表里就是一列同名行（截图 `desktop-01`）。
一次性路径（GUI / `run`）用的是首条请求前 60 字符。
**修复**：REPL 首条请求到达时用它命名（保持"首条消息决定标题"的单一约定）。

### A5. 工具契约没说"当前是 dry-run"，模型因此拒绝彩排
用 `--dry-run` 排练 embed-video 时，agent **拒绝调用 `beehive_submit_job`**，理由写得很清楚：
"`beehive_submit_job` 是实付路径，没有 dry-run 标志，成功的调用会排一条付费渲染——不能把
提交当彩排"。它说得对：工具描述里确实没有任何地方表明该 run 处于 dry-run（而实际上 dry-run
的 registry 会校验参数并返回"会发出什么"，一分钱不花）。
**修复**：tool 描述在 dry-run 的 run 里显式写明 "This runtime is running in DRY-RUN: …nothing is
submitted or charged."；非 dry-run 时这句话不出现（不能反过来误导）。测试
`test_dry_run_is_stated_in_the_tool_contract` 双向钉住。
**教训**：钱相关的模式必须写进契约，让模型去"推断"就是让谨慎的模型什么都不做。

## B. 环境限制（记录，非产品缺陷）

### B1. 合成键盘事件被系统丢弃
本会话里 `System Events keystroke` / `key code` 对任何 app 都无效（TextEdit 探测同样无反应），
而鼠标 `click at` 正常。**影响**：无法用键盘驱动桌面窗口。**绕法**（本次实际使用，成功）：
`pbcopy` → 鼠标点输入框 → 菜单 `Edit → Select All` → `Edit → Paste` → 鼠标点发送。
真机消息往返因此仍然拿到了证据（`desktop-12-send-in-empty-session.png` + `POST /message 200` +
`messages.jsonl` 四条）。CI / 人工交互会话没有这个限制。

### B2. AppleEvent 到 Finder 超时 → `.dmg` 步骤失败
`tauri build` 的 dmg 步骤用 `bundle_dmg.sh`，其中靠 AppleScript 驱动 Finder 摆放卷内图标；
本会话该 AppleScript 直接超时 `-1712`，于是 `.app` 产出后 dmg 报错退出（原文见
`tauri-build.log`）。**结论**：本地/无头冒烟用 `npm run tauri build -- --bundles app`；
dmg + 公证留给 CI 或交互式会话。已写入 `desktop/README.md` 的对照表。

## C. 已记录、未修（建议进 A2 phase-2）

### C1. 会话列表不轮询
壳只在挂载和每轮 run 结束后刷新 `GET /sessions`。**实测**：CLI 侧新产生的会话在已运行的窗口里
不出现，重启壳才看到（`desktop-01` vs `desktop-04`）。建议加一个显式刷新动作或低频轮询，
并保留"不打断用户操作"的克制（不要每 2 秒重排列表）。

### C2. 会话视图不滚到最新消息
打开长会话（17 条消息）停在顶部，最新回答在可视区之外；为了截到带 `.mp4` 的最终回答，只能把
窗口拉到 2100px 高（`desktop-11-transcript-tall.png`）。建议发送后与打开会话时
`scrollIntoView` 到最后一条。

### C3. 流式回答没有逐字证据（本次未覆盖）
桌面壳的 SSE `delta` 通道本次没有单独留证（截屏是发送后 25-30s 的终态）。runner 的
`serve_smoke.sh` / `evidence/a2-serve/message.sse` 覆盖了协议层，但"UI 上看着字往外蹦"这一条
建议在 A2 phase-2 补一张中途截屏或录屏。
