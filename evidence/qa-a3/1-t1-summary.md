# T1 三条 E2E 创作故事 — 结果（节存盘）

时间：2026-09-28 21:17~22:00 CST | serve pid 20336 port 8801 | dev API + e2e-platform 真实付费

## T1a 视频全链（会话 20260928-211707-46b134 / workflow-1790601427457590393）
15/16 PASS。故事完整走通：script 节点→分镜图片节点→$0.96 参考图（确认门）→
material 首帧接线→$1.035 minimax 15s 视频（确认门）→MP4+PNG 双产物落池 CDN。
- 每步 action 卡片附着在对应输入下（seq 单调，30 actions / 4 turns）✓
- 画板 4 节点（script/gpt-image-2/material/minimax-h3）✓
- 钱包差 1,995,000 µUSD = 960,000 + 1,035,000（两次 quote 之和，无隐藏扣费）✓
- **1 FAIL（真实缺陷）**：blueprint 上 minimax 无 material_deps——agent 建 material 节点用了
  `config.entry_id` 而非 SKILL.md 规定的 `pool_entry_id`，runtime `connect_ports` 的
  `is_material` 判定 False，`input_port="first_frame"` 被静默丢弃降级为普通 dep 边。
  本次产物碰巧正确（模型自行把 images[]/image_roles[] 手写进提交 config），但画板 SSOT
  无端口语义、read_board 不显示 material 边、行为不可依赖。→ 开 issue（by:qa, r:backend or platform）

## T1b 端口互斥负路径（会话 20260928-212553 / workflow-1790602514078143757）
PASS（核心）。两轮实测：
- 第一轮（空池）：agent 拒绝执行，给出两个理由（池空 + 端口互斥），引用 schema 原文
  （"First/last-frame mode cannot mix with reference mode (upstream rejects the mix)"），
  给出替代方案。无裸 400。
- 第二轮（池有图）：agent 重查 schema 后明确拒绝混接，解释失败会落在提交时而非接线时，
  提供单端口二选一+两段式正确做法。板终态无任何 material_deps（未写坏边）。
- query_schema 先行 ✓（setup 轮+mutex 轮均有 canvas_query_schema minimax-h3）
- 注：setup 轮 agent 先试 process:script 被结构化拒绝后自修正为 generate:script（SKILL.md
  正名规则生效）✓

## T1c 锁竞争+崩溃接管（workflow-1790602746185338407 共享板 + workflow-1790603067997635889 私有板）
全部 PASS。
Phase A（竞争，私有板，manual-client JWT 持锁 vs serve AK/SK agent）：
- A1 手动客户端持锁 200 ✓
- A2 agent 写入被拒：note_kind=lock "画板正被占用: held by manual-client/qa-manual-1 since …"，
  agent 如实告知用户且不重试（SKILL.md 锁纪律生效），板零变更 ✓
- A3 锁下读不受影响（canvas_read_board 正常）✓
- A4 解锁后重试成功（generate-script-1 落板）✓
Phase B（崩溃接管，SIGKILL serve runtime 2）：
- B2 刚死持有者：立即申请者 409（未到 90s，正确不放）✓
- B3 死持有者锁被下一申请者接管：kill→grant 92s（90s 存活检测窗口内）✓
首版 Phase A 在共享板上 FAIL（agent PUT 404）——根因是 #515 WRITE gate（admin 建的
共享板对普通用户只读），属设计行为非锁缺陷，已在私有板复测通过。证据 1c-08~1c-10。
