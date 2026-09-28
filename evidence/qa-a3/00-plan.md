# QA A3 全量回归 + 双端对齐验收 — 执行计划（issue #13）

主理人：qa（独立复核 platform 的 S4 自测证据，不抄其数据）
启动条件：S4 已合并 main（agent eed67c8，web #334）——已确认 2026-09-28
环境：dev API beehive-api.verse4.pet；账号 e2e-platform（uid 213508887911264，钱包余额
9992259486 µUSD = $9,992.26，测试开始时实测）；凭据走 ~/.skilyst/env（零入库）。

## 测试矩阵（四块）

### T1 三条 E2E 创作故事（真实环境，dev API + 真实付费）
- T1a 视频全链：script 节点→分镜→付费参考图（确认门）→material 首帧接线→minimax-h3
  15s 付费视频（确认门）→产物落池预览。断言：每步 action 卡片附着对应输入下
  （seq 单调+user 输入之间）；画板节点/连线与操作一致；产物 kind=video 落池。
- T1b 端口互斥负路径：诱导 agent 同时接 first_frame+reference_image 到 minimax-h3。
  断言：query_schema 先行；结构化错误（非裸 400 抛给用户）；agent 自修正（改接线并完成）。
- T1c 锁竞争+崩溃接管：agent 持锁期间另一凭证（qa-authz-probe 或手动 manual-client 锁）
  写同 workflow→409 LockHeldError→"画板正被占用"提示；kill serve 后新申请者 90s 存活
  检测自动接管（新 lock 200 + 服务端 takeover 日志证据=响应行为）。

### T2 双端对齐独立复核（ALIGNMENT-CHECKLIST.md 11 项）
同一 workflow：desktop（pnpm tauri dev 或 serve 模式）+ web console（bee.verse4.pet
/studio/agent 或本地 next dev）。独立操作（自己建板/自己编辑），逐项记录行为对比。
判定：不一致=FAIL。

### T3 计费诚实性（只读 SELECT 核对）
- T1 付费 job 的 quote（confirm_request SSE 载荷）=hold（ledger 冻结）=settle（实际扣费）
  三段数字一致。
- action 卡片 cost（µUSD）与 wallet ledger SELECT 对得上。
- 钱包余额前后差 = settle 总和（无隐藏扣费）。

### T4 会话流边界
- 纯对话输入（无 canvas 工具调用）不产生 action 条目。
- note 条目 origin 标记正确：锁提示 note origin、手动变更 origin=user（如可注入）、
  paid confirm 超时/拒绝 note。
- chat API（GET /session/{id} 的 history 通道）不受 action/note 条目影响——history()
  过滤 chat_roles，验证旧会话回放兼容。

## 纪律
- FAIL 必须给根因评估（代码 bug/脚本 bug/数据缺失/预期行为）+复现步骤+证据。
- DB 只 SELECT（billing ledger 查询走 kubectl run postgres:16-alpine 临时 pod 既有模式）。
- 发现 bug 开 issue（by:qa，r: 对应 owner）关联 #13。

## 产物
evidence/qa-a3/ 每节存盘：
- 00-plan.md（本文件）
- 10-15：T1 三故事证据（SSE json、board json、quote json、锁 409/接管响应）
- 20-2x：T2 双端对比记录（DOM/截屏/DOM 计数）
- 30-3x：T3 计费三段数字+ledger SELECT 输出
- 40-4x：T4 会话流条目序列
- 99-report.md：最终 PASS/FAIL 矩阵（回挂 #13）
