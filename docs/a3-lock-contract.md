# A3-S2 依赖：Workflow 写锁契约草案 v0.4

> 状态：v0.4 草案（planner 起草，r:backend 评审实现——对齐后进 beehive-core）
> 上游需求：docs/a3-canvas-workbench.md §二 R1（Wesley 裁决：显式上锁/解锁动作对；崩溃兜底=持有者存活检测，**否决固定超时**）
> 关联：skilyst-agent issue #10；beehive-core #585

---

## 一、语义总纲

**锁的生命周期由显式动作驱动；定时器只出现在崩溃兜底的持有者存活检测里，且只在"新申请者到达"时评估——正常路径永远感知不到任何时限。**

| 路径 | 行为 |
|---|---|
| 正常路径 | `lock` → 持有者执行写序列 → `unlock`。无任何定时器参与 |
| 竞争路径 | 锁被活持有者持有 → 新申请者收 409（附持有者信息）→ 前端提示"请稍候" |
| 崩溃路径 | 持有者死（心跳停）→ **下一个申请者到达时**检测到死持有 → 服务端自动释放+授予+响亮日志——不自动扫、不到点放 |

## 二、API 契约

### POST /api/v1/workflows/{id}/lock（获取锁）

请求：
```json
{ "holder": { "kind": "agent-session", "id": "<session_id>" } }
```
- `holder.kind`：`agent-session`（serve 运行时的 agent 会话）| `user-session`（JWT 控制台会话）| `manual-client`（画板手动编辑客户端）
- `holder.id`：持有者标识（agent session id / JWT user_uid+session / client 生成的稳定 id）

响应：
- `200`：`{ "lock": { "holder": {...}, "acquired_at": "<iso>", "heartbeat_dead_after_s": 90 } }`
- `409`：`{ "held_by": { "holder": {...}, "acquired_at": "<iso>" } }`——锁被**活**持有者持有
- 409 前的服务端检查：当前持有者 `last_seen` 距今超过 `heartbeat_dead_after_s`（默认 90s，可配）→ 判死 → 释放旧锁+授予新申请者+`log.Printf("[workflow-lock] dead holder %v taken over by %v")`

### POST /api/v1/workflows/{id}/unlock（释放锁）

请求：`{ "holder_id": "<申请时的 holder.id>" }`
- 只有持有者本人能解锁（403 否则）
- **幂等**：解锁已解锁的 workflow = 200 no-op（崩溃恢复后重放安全）

### 心跳（隐式）

- 持有期间，该 holder 对该 workflow 的**任何已认证 API 调用**刷新 `last_seen`（GET workflow/PUT workflow/提交 job/轮询 job 均算）
- agent 的 canvas 工具序列天然高频调用（query_schema/PUT）——无需专门心跳端点
- 手动编辑客户端：编辑期间有交互即刷新；纯浏览不持锁（手动编辑在"落笔"时短暂 lock→write→unlock，或按交互序列持有——前端实现细节，契约不约束）

## 三、实现要点（backend）

1. **存储**：进程内 `map[workflowID]*lockEntry` + mutex（与 device-code store 同模式）——单实例 dev 语义；多副本时升 Redis，接口留 seam
2. **锁与写路径的关系（关键设计）**：
   - **MVP：锁是咨询式（advisory）**——PUT workflow 不强制校验锁（存量 lanes/脚本/Agent 旧路径不破坏）
   - **canvas-ops skill 与手动编辑客户端**是锁纪律的第一批执行者（agent 工具序列 lock→…→unlock；手动写同申请）
   - 强制式（PUT 校验锁持有者）作为后续选项——待双端都上锁后评估（避免一步到位破坏存量调用方）
3. **T3 scope**：两端点声明 `workflows:write`（进 routeScopes 表——TestEveryRouteDeclaresAScope 会强制）
4. **测试**（真实路由链）：获取/释放/幂等/409 活持有者/死持有者接管（构造 stale last_seen）/非持有者解锁 403/agent-session 与 user-session 双 kind

## 四、与 A3 文档的对接

- 本契约落地后，A3 FR-2 的"服务端写锁"依赖解除
- canvas-ops skill 的 lock/unlock 工具按本契约调用（agent 侧实现归 skilyst-agent S3）

## 变更记录

- v0.4（2026-09-27）：planner 起草——三路径语义/三端点契约/隐式心跳/咨询式 MVP+强制式后续/测试清单。待 backend 评审。
