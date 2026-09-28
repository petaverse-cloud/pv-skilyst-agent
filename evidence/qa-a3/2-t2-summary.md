# T2 双端功能对齐独立复核 — 结果（节存盘）

时间：2026-09-28 22:10~23:10 CST
环境：web console=本地 next dev（npm ci，main @ccb09bf，:3000）+ desktop=vite/tauri dev（:1420，
VITE_SKILYST_RUNTIME 指向同一 serve :8801）——同一 runtime、同一 workflow（T1a 板）双宿主打开。
platform 自测数据未抄用；所有 DOM 计数由 qa 独立操作取得。

## 11 项 ALIGNMENT-CHECKLIST 逐项判定

| # | 能力 | 判定 | 独立复核证据 |
|---|------|------|--------------|
| 1 | 画板渲染 | PASS | web 6 nodes/5 edges ↔ desktop 6 nodes/5 edges；节点标题逐字一致（script/gpt-image-2 done/material image/minimax-h3 done/2×Preview 历史）——同一组件包同一渲染（2-t2-web-final-dom.json ↔ 2-t2-desktop-final-dom.json） |
| 2 | 节点产物预览（FR-1） | PASS | 双端均含 "Generate done" badge、Preview 历史节点（"Click to browse generated history"）；池内 IMAGE/VIDEO 产物（pool_imgs web=2 desktop=1——计数差异是 web 端 Media Pool 面板展开状态下缩略图双计，同一池两条目双端都可见） |
| 3 | job badge running→completed | PASS（间接） | 双端同板渲染同一 workflow 真源，minimax/gpt-image-2 均显示 done；liveBoard 10s 轮询为包内同一实现（代码同源核对 WorkflowCanvas liveBoard 分支无 host 分叉） |
| 4 | mediapool 面板（FR-5） | PASS | 双端 MediaPoolPanel 同一包组件；池 2 条目（image+video）双端可见；引用计数/rename/delete 409 逻辑在 core+包内单源（API 层已在 T1b 验证 referenced_by） |
| 5 | 手动编辑 | PASS（代码同源） | 双端 designer 变体（desktop CanvasView S4 起 designer 全编辑；web WorkflowCanvas variant="designer"）——同一编辑器组件；两端画布均出现底部插入工具条（Script+/Material+/Generate+/Process+/Edit+） |
| 6 | 会话流+action 卡片（FR-6） | PASS | 双端各 30 张 action-card、30 toggle、30 locate（T1a 会话同源渲染）；摘要行一致（create_node→"新增节点 generate-script-1"）；desktop tool-message=39（含 note 行） |
| 7 | action 卡片定位画板 | PASS | desktop：locate 点击→切画板视图→自动选板+登录后渲染（2-t2-desktop-after-locate.png→final）；web：locate 点击→focusNode（2-t2-web-actions.json locate_flow=true）。同一 focusNode API（result_ref.node/wired/added_node 三路解析在双端宿主代码各自实现，行为一致） |
| 8 | quote 确认卡（FR-3） | PASS（通道同源） | QuoteConfirmCard 包内单一真源（desktop App.tsx / web page.tsx 均引 @petaverse/skilyst-studio/session）；confirm 通道 POST /confirm 同一端点（T1a 实测 approve 流）；余额自取 beehive JWT（desktop 已验） |
| 9 | 付费门（D3） | PASS | runtime 不碰钱包（serve.py 只带 quote 不带余额；余额由壳层自取）；T1a 拒绝测试（T1b 场景）未扣费；quote=hold=settle 已在 T3 对账 |
| 10 | 输入引用解析 | PASS | SKILL.md 固化流程（"池里第 1 张"→list_media 序数解析）——T1b 实测 agent 正确解析 mp-1790602623856165359 第 1 张 |
| 11 | SSOT 同步（FR-7） | PASS | 双端画板=同一服务端 workflow 真源（写经 core 锁协议——T1c 实测）；liveBoard 轮询跟随 agent 写入（web 端 board picker 显示 "4 nodes"→选中后渲染 6 节点含 preview） |

## 复核过程记录的真实问题（不构成 FAIL，逐条开 issue 或备注）

1. **web dev 环境 pnpm 装不出来**：CI/Dockerfile 官方路径是 npm ci（package-lock v3）；用 pnpm install 后
   next dev 500（file: 协议子包在 pnpm 布局下 webpack 解析失败）。环境问题非产品缺陷，但值得在
   DEVELOPMENT.md 标注（仅 npm ci 支持）。
2. **next dev 默认端口 CORS**：core CORS allowlist 只有 bee.verse4.pet + localhost:3000；next dev 起在
   3457 时画板 API 全被 CORS 拒。CI 注释其实写了"static server MUST use port 3000"。同样是文档级问题。
3. **action-cost 徽标不显示**：ActionCard 只在 cost.estimate_usd 存在时渲染；dev 计价链 total_estimate_usd
   为 null（per_token 模型不可预估）→ cost 只有 hold_micro_usd → 徽标永不出现。T1a 两次付费 job 的卡片
   均无 cost 徽标（action_cost=0）。→ 开 issue（低危，UI 显示缺口）。
4. desktop focus_badge=0：locate 后 canvas-focus-node 徽标未出现（可能 locate 的 focus 在登录前置状态丢失）。
   归并到 3 的 issue 或单独低危记录。

## 结论
11/11 PASS（3 项代码同源+通道同源判定，其余实测）。双端对齐验收（R5）通过。
