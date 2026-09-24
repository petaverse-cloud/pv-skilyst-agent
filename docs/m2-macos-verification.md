# M2 — macOS 真机端到端验证（CLI / 桌面壳 / 打包）+ A1 phase-2 工具透传

日期：2026-09-24 · 机器：本机 macOS（Apple Silicon, arm64）· 基线：`main` @ `d110ac9`（M1 六件）
证据目录：`evidence/m2-macos/` · 里程碑真源：pv-beehive-core#585

Wesley 的指令是「我们用 MAC 测试」：三平台里先在 macOS 上把整条链路跑通。本文按三条验收线
+ 一条透传线记录**实际执行结果**，包括真机上暴露的问题（不藏）。

---

## 线 1 — CLI 全链路（真 job、真产物、真钱）

| 步骤 | 命令 | 结果 |
|---|---|---|
| 预载 | `./bin/skilyst preload skills/official` | exit 0，5 个 skill 装入 `~/.skilyst/store`（`cli-preload.json`） |
| 体检 | `./bin/skilyst doctor` | integrity **5/5 ok**，全部 skill 摘要一致（`cli-doctor.json`） |
| 交互对话 | `./bin/skilyst chat --skill skilyst/video-15s`（stdin 投喂请求） | `stop_reason=completed`，turns=6 / tools=8 / 398.8s，exit 0（`cli-chat.stderr`、`cli-chat.stdout`） |

对话请求：「用 video-15s 生成一条 15 秒竖版短视频：雨夜的东京街角便利店……」。agent 的实际动作
（`cli-chat-trace.jsonl`）：`read_skill video-15s` → `read_skill doctor` → `read_skill prompt-craft`
→ `list_skills` → `write_workspace_file rainy-tokyo-corner/prompt.txt` → `beehive_submit_job`
（minimax-h3, 15s, 768P, 9:16）→ `beehive_get_job` → `beehive_verify_artifact`。

**产物独立复核**（不看 agent 的话，自己下载再验）：

```
job_id       job-1790229409388-3526e6fdfe702d9e   status completed
artifact     https://beehive-cdn.verse4.pet/beehive-temp/beehive/job-1790229409388-.../node-...-0-1790229717982.mp4
curl         HTTP 200, 3,751,755 bytes, video/mp4            （cli-chat-artifact-url.txt）
ffprobe      duration 15.084s · h264 768x1344 · aac stereo   （cli-chat-ffprobe.json）
```

与技能文档写的交付契约（15s / 768x1344 / h264 + aac）逐项吻合。花费：一条 minimax-h3
15s/768P ≈ $1.035。

### 线 1 发现的问题（已修，见本 PR）

- **REPL 的提示符写进了 stdout**：`input("skilyst> ")` 把提示符写到标准输出，于是
  `skilyst chat < request.txt | jq` 的输入被 `skilyst> ` 污染（实测 stdout 里出现
  `skilyst> skilyst> `）。修复：提示符改写到 stderr（`cli-chat-pipe.stderr` 里能看到
  `skilyst> `，stdout 干净）。修完顺带明确了语义：**REPL 是交互面**，过程与摘要都走 stderr，
  机器可读的一次性调用用 `skilyst run` / `skilyst chat --message`（stdout 出 JSON），REPL
  启动横幅里写明这一点。
- **REPL 会话一律叫 `interactive session`**：桌面壳的会话列表因此是一列同名行（对比第 2 线的
  截图可以看到）。修复：首条请求到达时用它的前 60 字符命名，与一次性路径一致。

---

## 线 2 — 桌面壳真机（Tauri 窗口）

`cd desktop && npm run tauri dev`（窗口 = React 19 + Mantine 9；壳只拥有运行时**进程**）。

| 验收项 | 证据 | 结果 |
|---|---|---|
| 窗口渲染 | `desktop-01-initial.png` | 深色窗口正常渲染，标题栏 "Skilyst Agent"，状态徽章 `DRY RUN · PORT 64549` |
| 会话列表（读 `~/.skilyst/sessions/`） | `desktop-01/04/13` | 列出来自磁盘的会话（标题 + 时间 + 消息数徽章）；新会话实时出现在顶部 |
| serve 模式连接 | `tauri-dev.log` | `OPTIONS /sessions 204` → `GET /sessions 200`；页脚显示 `runtime pid · sessions ...` |
| 会话详情渲染 | `desktop-05-transcript.png`、`desktop-08-transcript-tall.png` | 点击侧栏 → `GET /session/<id> 200`，主区渲染用户气泡、**8 张 tool-call 卡片**（read_skill ×3 / list_skills / write_workspace_file / beehive_submit_job / beehive_get_job / beehive_verify_artifact）、最终回答与产物 URL |
| 设置页渲染 | `desktop-06/07-settings*.png` | Model routing（endpoint / `API key: SET` / 默认模型 / fallbacks / 配置文件）、凭据状态 `RUNNABLE` + 5 个 skill 的 Integrity 全 OK、运行时端点 `http://127.0.0.1:<port>` |
| **发消息 → agent loop → 回答渲染** | `desktop-12-send-in-empty-session.png` | 在真空会话里发送 "In one line: which skills can you drive?" → `POST /message 200` → 渲染 `list_skills` 工具卡 → 回答（点名 5 个 skill）；`messages.jsonl` 4 条一一对应 |
| 壳生命周期（真机） | `tauri-dev-2.log`、`packaged-app.log` | SIGTERM 掉壳（无清理路径）后 2 秒内 `skilyst serve` 自行退出；打包后的 app 退出同样不留孤儿进程 |

### 真机操作方式的限制（重要，必须记录）

这台机器给我的自动化权限里 **合成键盘事件被系统丢弃**（`System Events keystroke` 无效果；
连向 Finder 发 AppleEvent 都直接超时 `-1712`）。鼠标点击正常。因此窗口内的输入是这样完成的：

1. `pbcopy` 写入剪贴板；
2. 鼠标点击输入框（点击生效，光标出现）；
3. 菜单栏 `Edit → Select All` → `Edit → Paste`（鼠标/AX 动作，生效）；
4. 鼠标点击发送按钮（坐标由截图像素分析定位）。

结果是**真窗口里真的发出去了一条消息并渲染了回答**（上表第 6 行），只是驱动手段受限；
这一点在 CI/交互式会话里不是问题，但对"用 agent 脚本驱动 macOS GUI"这类任务是个硬约束。

### 线 2 发现的问题（记录，未在本 PR 改）

- **会话列表不轮询**：壳只在挂载和一轮 run 结束后刷新一次。CLI 侧新产生的会话不会自动出现
  （实测：付费 run 结束后，已在运行的窗口里看不到它，重启壳才看到）。A2 phase-2 应该给列表
  加一个刷新动作或低频轮询。
- **会话视图不滚到最新消息**：长会话（17 条消息）打开后停在顶部，最新回答在可视区之外
  （实测为拿到那张带 `.mp4` 的截图，需要把窗口拉到 2100px 高）。A2 phase-2 需要在发送后
  `scrollIntoView`。

---

## 线 3 — mac 打包冒烟

无签名证书（`tauri.conf.json` 里没有 identity，签名/公证参数按 `desktop/README.md` 走环境变量
注入，密钥是 Wesley 的决策项，不入库）。

| 步骤 | 结果 |
|---|---|
| `npm run tauri build`（前端 + Rust release + 全部 bundle） | ⚠️ `.app` **成功**（9.5MB），`.dmg` **失败** |
| `npx tauri build --bundles app` | ✅ exit 0 → `Skilyst Agent.app`（arm64, version 0.1.0） |
| 未签名 `.app` 真机启动 | ✅ 窗口起来 + 自行拉起 `skilyst serve --port 0 --orphan-guard --dry-run`（`packaged-app.log`：`GET /sessions 200`）；截图 `desktop-13-packaged-app.png` |
| 退出清理 | ✅ 杀掉 app 后 serve 子进程同步消失，无孤儿 |

**`.dmg` 失败的原因（已定位）**：Tauri 用 `bundle_dmg.sh`，其中用 AppleScript 驱动 Finder 摆放
卷内图标；在本会话（非 GUI 自动化受限）该 AppleScript 超时，脚本退出码非 0 → tauri 报
`failed to bundle project error running bundle_dmg.sh`。`.app` 已经产出，dmg 只是最后一步；
CI/交互式会话（以及带公证密钥的发布流程）才是它的正确执行环境。已写进 `desktop/README.md`
的对照表，本地冒烟用 `--bundles app`。

---

## 线 4 — A1 phase-2：工具透传面（images / image_roles / audio_refs / text）

### 问题：manifest 声明了 binding，运行时不读

M1 时 `beehive_submit_job` 的参数是硬编码的 `prompt/duration/resolution/ratio`；官方 manifest 里
每个节点都声明了 `binding.config_map`，但运行时从未解析它。结果：`images` / `image_roles` /
`audio_refs` / `text` 传不进去，embed-video 与 lipsync 的端到端必须绕过 agent 直接打 API。

### 现在的做法（不是把四个字段加进白名单）

工具的参数面**由 manifest 推导**，字段名**由 manifest 映射**，取值形状**由集群节点 schema 约束**：

1. `src/manifest/spec.py` 解析并校验 `requires.nodes[].binding`（tool / node_id / config_map），
   规则包括：config_map 不能映射运行时控制参数（`node_id`/`workflow_id`/`wait`/`timeout_s`）、
   两个参数不能映射到同一个节点字段、binding 必须绑定自己那个 node。
2. `beehive_submit_job` 的参数 = 该 skill 声明的 config_map 键的并集 + 控制参数；每个参数按
   `config_map` 换成节点字段名发出（例：静帧节点 `ratio → aspect_ratio`）。
3. 类型 / 枚举 / **节点 required 列表**来自 `GET /api/v1/nodes` 的 `input_schema`（preflight 顺手
   带回），所以在花钱之前就能告诉模型缺哪个字段；数组类参数（images/image_roles/audio_refs/
   video_refs）按"非空字符串数组"校验，`image_roles` 还要落在节点声明的枚举内。
4. **没声明的参数一律拒绝，不静默丢弃**——丢一个 `images` 就是把 i2v 变成同价位的 t2v。
5. `images` 与 `image_roles` 位置配对长度不一致时本地就拒（平台在提交时才会拒的 frame/reference
   混用，提前拦掉）。
6. 默认节点从「第一个声明的」改为「第一个 **required** 的」：embed-video 先声明的是可选静帧
   节点，过去不带 `node_id` 的调用会打到静帧上。
7. dry-run 模式写进工具契约：`--dry-run` 的 run 里描述会明说"当前是 DRY-RUN，调用会被校验并
   返回将要发出的 node config，不提交不扣费"。**这是审计轮发现的**：没有这句话时，谨慎的 agent
   会以"提交就是花钱，不能算彩排"为由拒绝排练整条链路（原文见 `findings.md` A5）。

### 真机验证（一条 embed-video，两条 job，$0.481）

`./bin/skilyst run skilyst/embed-video --max-jobs 2 --max-turns 10 --request '…'`
（`embed-video-run.json` / `.stderr` / `embed-video-ffprobe.json`）：

| 步 | 实际发生（run 摘要原文） | 说明 |
|---|---|---|
| 1 | `read_skill embed-video` | 按技能 Rule 2：先出静帧再动画 |
| 2 | `beehive_submit_job {"node_id":"generate:nb2-image","prompt":"…yellow raincoat…","ratio":"9:16"}` → 节点收到 `{"prompt": "…", "aspect_ratio": "9:16"}` | **config_map 改名在真付费调用里生效**（`ratio → aspect_ratio`） |
| 3 | 静帧完成 + HEAD 校验 → `job-1790232255875-43bb799ca8836d4c`（768×1376 PNG，已下载复核） | 产物即为下一步的锚 |
| 4 | `beehive_submit_job {"node_id":"generate:minimax-h3","prompt":"She slowly lifts her head…","images":[<静帧 URL>],"image_roles":["first_frame"],"duration":6,"resolution":"768P","ratio":"9:16"}` → 节点收到 `{"duration":6,"image_roles":["first_frame"],"images":["https://…/…png"],"prompt":"…","ratio":"9:16","resolution":"768P"}` | **phase-2 打通的那条路**：M1 时这条路必须在技能体外直接调 API |
| 5 | 视频 job 完成 + 产物 HEAD 校验 → `job-1790232288287-e384e7bbc6bd8eab` | 独立复核：HTTP 200 / 3,011,524 bytes / **6.584s** / h264 768×1344 + aac |

运行摘要：`stop_reason=completed`，turns=5，265.3s，exit 0，**`jobs: {submitted: 2, budget: 2}`**
（预算由 `--max-jobs 2` 给出；这同时验证了预算旋钮真的在拦/放）。

花费：本次两条 job（静帧 per-token ≈ $0.067 + 视频 6s/768P ≈ $0.414），加线 1 的 15s/768P
（$1.035），总计 ≈ **$1.52**，在 $3 预算内。

### `--max-jobs` 默认值评估（结论：分模式，且可配置）

| 模式 | 默认 | 理由 |
|---|---|---|
| `run` / `job` / `chat --message`（无人值守、单发） | **1** | 提交出去的 job 撤不回来；一次性运行多花钱比拒绝更糟 |
| `chat` REPL / `serve`（桌面壳，用户在看着） | **3** | "给我三个镜头"这类需求本来就发生在会话里，而 GUI 没法传 flag |
| 全局覆盖 | `SKILYST_MAX_JOBS=N`（`~/.skilyst/env`） | 运维级旋钮；值非法（0/负数/非数字）直接报错，不做静默兜底 |

`skilyst config` 的 `limits.job_budget` 会显示"配置值 + 来源 + 两个模式默认"，README 有对照表。

---

## 证据清单（`evidence/m2-macos/`）

```
cli-preload.json            预载结果（5 skill）
cli-doctor.json             integrity 5/5 + 解析后的配置（密钥已脱敏）
cli-chat.stderr/.stdout     交互对话全过程（stderr=过程，stdout=结果 JSON）
cli-chat-trace.jsonl        8 次工具调用（含提交/轮询/校验）
cli-chat-ffprobe.json       产物独立复核（15.084s / h264 768x1344 / aac）
cli-chat-artifact-url.txt   产物 URL 原文
desktop-01…13-*.png         桌面壳 13 张真机截屏（初始/会话/transcript/设置/发送问答/打包 app）
tauri-dev.log               壳 + 运行时日志（含 /sessions 200、/message 200）
tauri-build.log             全量 build（.app 成功、.dmg 失败原文）
tauri-build-app.log         --bundles app 成功（exit 0）
packaged-app.log            未签名 .app 启动后自行拉起 runtime
embed-video-run.json/.stderr 透传验证 run（两条 job、image_roles=first_frame）
```

## 复现

```bash
./bin/skilyst preload skills/official && ./bin/skilyst doctor
printf '%s\n' '<request>' | ./bin/skilyst chat --skill skilyst/video-15s   # 付费
cd desktop && npm run tauri dev                                            # 真窗口
npm run tauri build -- --bundles app                                       # 打包冒烟
./bin/skilyst run skilyst/embed-video --max-jobs 2 --request '<request>'   # 透传，付费
PYTHONPATH=src python3 -m unittest discover -s tests                       # 165 tests
```
