# M3b 物化可视化分镜 — agent 建图过程上画板（#52 M3b）

> 作者：skilyst-desktop-app（desktop 领域主理人）
> 状态：分镜稿（空档期产出；依赖：planner C1 裁决的收敛版验收——end-of-materialization 板态 + action 卡，逐步 live 是 M4+）
> 对齐：skills v0.3 §3（骨架+自由度+校验链）/ A3-canvas R3（action 混合时间轴）/ #52 M3b

## 0. 验收边界（C1 冻结后）

- **硬验收**：物化结束时画板呈现完整 workflow（节点+连线全量渲染），每步一个 ActionCard（可展开详情、点击定位）
- **非目标**：逐步实时上板（M4+）；锁窗口不为此扩大；SSE cadence 不变

## 1. 数据流现状（已合入 main 的地基）

```
/message (stream:true)
  ├─ SSE note  → 进度行（现有 onNote → notes）
  ├─ SSE delta → 模型文本（现有 onDelta）
  └─ action 行（agent 写 session store：{type, tool, params, board_delta, origin}）
                                  ↑ tools.py 的 actions() 回调已写盘
会话详情 GET /session/{id} → messages[] 含 action 行（TranscriptMessage.type === "action"）
                                        ↑ M2 的 ActionCard + locate() 已消费此契约
```

物化时 agent 走 canvas skill 工具序列（create_node / connect_ports / write_node_config）——每个工具一次 board_delta。**现状缺的是**：物化结束（run done）后画板没有自动 refresh 到最新 workflow 快照；画板只在打开时拉一次。

## 2. 分镜（收敛版，6 镜）

| 镜 | 触发 | 行为 | 组件落点 | 依赖 |
|---|---|---|---|---|
| S-1 | 用户在 picker 选 skill（M3a 已交付）+ 发首条消息 | 输入条上方显示所选 skill 徽标；发送后 History Drawer 自动开（M2 行为） | Composer（现成） | — |
| S-2 | agent 执行 canvas 工具（SSE note: canvas: create_node …） | note 行进转录流（现有）；ActionCard 骨架先落（title=params.workflow_id / tool 名） | ConversationView + ActionCard（现成） | — |
| S-3 | run done（sendMessage resolve） | **画板刷新**：拉 GET /session/{id} 或直接 workflow GET，重建节点图 | CanvasView 加 refreshBoard()——run 结束后调 getWorkflow(activeWorkflowId) 重渲染 | 本稿唯一新写点 |
| S-4 | 物化校验失败（v0.3 §3 校验链拒绝） | ActionCard 里渲染结构化错误（note_kind=error 现有通道）；画板不刷新到违规态 | ActionCard 现有 error 渲染 | runtime 校验（platform #55 已合并 parse/validate！确认中） |
| S-5 | 用户点 ActionCard | locate() 定位画板节点（M2 已交付，params 契约已修 #41 round2） | locate（现成） | — |
| S-6 | 物化产物执行（submit_node_job） | quote-first 确认卡（S4 现有）→ 产物落 mediapool | 现有 | core#686 锁语义（写面前置） |

## 3. 唯一实现增量：run 结束后的画板刷新

```ts
// WorkbenchPage.send() 的 finally 之后：
//   if (activeWorkflowId) → canvasRef.current?.refreshBoard()
// CanvasView 暴露 refreshBoard()（ref handle 已存在 focusNode 同款模式）：
//   重新 getWorkflow(id) → setBoard(...)，保持 viewport 不重置（React Flow
//   以 node id 保留已有布局，新增节点按 fitView 增量进入——不整板 fitView 抖动）
```

预估 ~60 行 + 2 测试（run 结束触发刷新 / 无 activeWorkflow 时静默跳过）。

## 4. 明确不做（本镜范围外）

- 逐步 live（每 board_delta 即时上板）——M4+，需 SSE cadence + core#686
- 骨架只读标注渲染（pinned/free-zone 角标）——M3c，依赖 v0.3 workflow_skeleton 进 skill manifest（platform #65 PR 在途，确认后排）
- 锁 UX（"agent 正在操作画板"）——A3-S2，core#686 前是惰性的

## 5. 风险与开放问题

1. **refreshBoard 的数据源选择**：GET /session（转录含 board_delta 重放）vs GET /workflow（服务端 SSOT 直接拉）。倾向后者（SSOT 单源、无重放解析）；若 core#686 修好后 workflow GET 返回带锁信息，一并渲染锁态
2. **viewport 保持**：React Flow 增量节点默认不重置视图——需验证 skill 物化的大图（30+ 节点）首次 fitView 时机，放 S-3 验收
3. **M3b 的 E2E**（#52 验收）：选 skill → 物化 → 板上节点图 → action 卡可定位。dry-run 可接受（C4）。需要 core P1 冻结前先用官方 canvas skill（skilyst/canvas-ops 已在预载包）做一轮真实物化——不依赖 registry
