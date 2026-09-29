# A3 画板工作台——需求固化文档（Demand Record）

> 状态：v0.3——需求固化完成（R1-R6+Q5-Q7 全关，**待裁决项清零**；下一步 backend 锁契约+排期）
> 发起：Wesley 2026-09-27。"文档优先，禁止直接动手，我们要固化需求并记录过程。"
> 关联：pv-beehive-core#585（M1 真源）；本仓 A1/A2（运行时/桌面壳）；pv-beehive-web（双端宿主之一）；issue #10（本仓，评审承载）。

---

## 一、需求陈述（Wesley 原话提炼，逐字保留关键判断）

> "目前的交互我不满意，我们需要的不是一个普通的对话 app，我需要执行可视化，主要工作区还是借鉴 web console studio，**画板仍然是最核心的用户界面**。而**用户输入是控制画板的入口**。"
> "用户可以通过输入在画板创建 script 节点，并通过输入与节点交互生成用户期望的脚本内容。用户也可以拖入图片或使用图片生成节点生成图片作为参考图，可以简单管理用户资产（mediapool）。用户可以通过输入编辑节点和节点之间的关联。**节点仍然自动判定输入输出匹配节点内的属性**。"
> 手动编辑："保留用户手动节点创建/连线/编辑功能，并非唯一，**留个用户介入编辑的口子**。"
> 会话模型："会话 + action——基于会话历史，但不是每条会话都会有执行，**但凡有执行就需要和上一条会话在一起**。"
> 双端定位（2026-09-27 补充澄清）："我们并不是要退役 webconsole，而是要**支持两端**。"

### 产品定性（v0.2 修订）

**Agent 可视化创作工作台，双宿主形态**：
- 画板是主工作区（workflow 的 UI），用户输入是控制画板的主要入口（agent 驱动），手动编辑是保留的介入通道
- **standalone App（桌面）与 web console studio 都是宿主，长期并存，功能一致**——同一工作台，两处可用
- 新形态规格由本需求文档定义；**web console 被反推兼容我们的方案**（改造它，不是迁就它）
- 不是聊天 app 加了画板，是画板加了 agent

### 与既有决策的关系（v0.2 修订）

- **"砍 WEB 编辑"的最终语义**：砍的是"web console 作为唯一编辑入口"的旧定位，**不是 web console 本身**。编辑能力以画板工作台形态回归且更强（agent 驱动），**同时在两端**。web 仓 canvas 不退役——升格为组件包单一真源，双端共装。
- **D6 兑现**：作品 = workflow + skills set；画板即 workflow 可视化；fork-onboarding 打开画板即见节点图。
- **beehive SSOT 放大**：两端一致性靠"都渲染同一个服务端真源"+服务端锁。

---

## 二、需求决策记录（R1-R6，全部已关——含过程）

| # | 决策点 | 结论（Wesley 裁决） | 过程记录 |
|---|---|---|---|
| R1 | 双写竞态 | **数据层/渲染层分离 + 锁协议**：画板分数据层（workflow 结构化文本描述=SSOT）与渲染层（React Flow 视图）；agent 触发修改→**先对数据层上锁**→执行工具序列→解锁。从根上消灭并发，不是仲裁谁赢 | planner 初案"agent 唯一写者"→ Wesley 修正"留口子"（双写）→ planner 提出"last-write-wins 仲裁+三条规定"→ Wesley 升格为**锁协议**（2026-09-27）→ planner 三条规定作废 |
| R2 | script 节点 | **零新增**——core 已内置 `process:script`（LLM=deepseek） | planner 初案误判需新类型 → Wesley 纠正 → 纯工具调用（create_node+write_node_config），全程现成基建 |
| R3 | 会话模型 | **会话+action 混合单时间轴**：纯对话与执行同流；执行=输入+action 卡片（可多）；卡片可展开+点选定位画板 | Wesley 定义"但凡有执行就要和上一条会话在一起" → planner 结构化为四类条目消息模型 |
| R4 | 画板操作能力形态 | **官方 canvas skill**：不是散装工具集——固化为官方 skill（预载分发+update skills 迭代），先满足基础功能需求持续迭代 | planner 初案"~10 个工具"→ Wesley 定向"一套官方 skill"（2026-09-27）——与 T4 官方 skills 体系同构 |
| R5 | 交付形态 | **完整交付，不要半成品**——内部可有里程碑，对外交付是完整态；**web console studio 与 standalone App 功能一致**（web 被反推兼容我们的方案） | planner 初案"M1 画板骨架先行"→ Wesley 否决半成品节奏 + 提出双端一致 |
| R6 | 组件形态 | **组件包**：canvas/会话流/mediapool 核心组件以单一真源发组件包，**双端共装**（App+web console 同装一份） | planner 建议整仓搬（基于"web 退役"误判）→ Wesley 澄清"支持两端"→ 组件包由 R5 直接确立 |

### R1 锁协议设计（v0.3——Wesley 裁决 Q5 后定稿）

**核心语义：锁的生命周期由显式动作驱动，不由定时器驱动。**

| 细节 | 设计 | 说明 |
|---|---|---|
| 锁的位置 | **beehive 服务端**（workflow 写锁，core 能力） | 双端同权：App 上的 agent 持锁，web 上的用户同样不能并发写；锁在服务端才跨宿主成立 |
| 锁的粒度 | 先全 workflow 单锁，后续按需优化到节点级 | 单锁语义清晰；创作场景单用户单画板 |
| **上锁/解锁** | **显式动作对**：agent 触发修改时 `lock(workflow_id)` → 执行工具序列 → `unlock(workflow_id)`（finally 语义——异常路径也必须解锁） | Wesley 裁决（2026-09-27）："应该有上锁和解锁的动作，而不是设定一个 60s 固定时间，这样体验很差"——锁窗口=真实操作时长，无人工时限 |
| 手动写 | 手动编辑同样申请锁（同一动作对） | 双端一致；锁协议是唯一写路径的纪律 |
| 遇锁 UX | 行内提示"agent 正在操作画板，请稍候"（不排队不丢操作） | agent 正常序列秒级完成——提示窗口=真实锁窗口 |
| **崩溃兜底（替代超时）** | 锁绑定**持有者活跃度**而非定时器：获取锁时检测持有者是否存活（会话已终止/连接已断=持有者死）→ 死持有者的锁可安全接管+响亮日志 | 防 agent 崩溃死锁的恢复机制是"检测持有者死了没"，不是"到点就放"——正常路径零感知，异常路径自动愈合 |

### R3 消息模型（技术契约草案）

```
messages.jsonl 条目类型：
  user        用户输入（文本/附件引用）
  assistant   agent 纯文本回复（markdown）
  action      执行条目（tool_result 中含画板变更的）
              { type, tool, params, result_ref, board_delta, origin: agent|user, cost?, duration? }
  note        系统条目（锁提示/手动变更提示/错误/费用说明）
              { note_kind: lock|manual|error|info, origin: agent|user }
              origin 归属：agent = 运行时代表 agent 写入（锁拒绝提示等），
              user = 宿主事件桥转发的手动变更提示。契约无 system origin（#16 归一）。
action 卡片：点开=调用详情；点卡片=画板定位高亮
手动变更经事件桥进流（note 条目，origin=user）——双端审计无盲区
```

---

## 三、功能需求清单（FR，v0.2）

### FR-0 双端一致架构（新增，R5/R6 的落地）
- 核心组件组件包化：canvas（渲染+手动编辑）/会话流/mediapool/输入 Composer——单一真源发布，App 与 web console 同装
- **宿主适配层（host adapter）**：组件包抽象宿主差异接口——事件源（App 走 serve 事件桥/web 走自有通道）、凭据注入、导航/路由——组件不感知宿主
- 两端功能对齐验收：同一 workflow 在两端打开，画板/输入/mediapool/会话流行为一致

### FR-1 画板主工作区
- 主区占比 ~70%，React Flow；缩放/平移/框选/节点拖动（手动本能保留）
- 节点卡：类型徽章/config 摘要/运行态 badge/产物预览（PreviewPlayer 三态）
- agent/手动新增连线的过渡动画（操作可视化反馈）

### FR-2 数据层与锁（R1）
- 数据层：workflow 结构化文本描述规范（节点/连线/端口的规范化表达——agent 与人共读）
- 服务端写锁：beehive core 的 workflow 锁能力（待与 backend 对接契约——**R1 的 core 侧实现是新的依赖项**）
- agent 工具序列包裹在 lock/unlock 之间；手动写同样申请锁

### FR-3 官方 canvas skill（R4）
- `skilyst/canvas-ops`（首版范围）：create_node / write_node_config / connect_ports / query_schema / read_node_output / submit_node_job / run_workflow / generate_image / list_media / add_media
- 端口匹配规则：connect 前必须 query_schema（input_schema 的互斥/枚举是判定依据——lanes 实测 400 教训的制度化）
- 删除/撤销/参数微调/批量：首版取舍待 skill 迭代定（Wesley 定向"先满足基础功能需求"）
- quote-first 不变：付费 job 前预估确认

### FR-4 手动编辑通道
- 手动建/连/删（canvas 写路径复用）经锁协议写数据层
- 手动变更同步推送：会话流 note 条目 + agent 可见

### FR-5 mediapool
- 侧栏面板；拖图入池/生成入池/输入引用（"用池里第 3 张"）/拖到端口（手动）

### FR-6 会话流（R3 混合模型）
- 单时间轴四类条目；action 卡片展开+定位画板；Composer 常驻+markdown 沿用

### FR-7 workflow SSOT 同步
- 画板=workflow 渲染；变更经事件桥全端（双端）刷新；本地 transient（连线预览）确认后落数据层

---

## 四、复用与依赖（v0.2 修订）

| 资产 | 来源 | 动作 |
|---|---|---|
| canvas 渲染+写路径 | pv-beehive-web（6265+1188 行） | **组件包化**（R6）：抽包+宿主适配层，双端共装 |
| PreviewPlayer/MediaPoolPanel/Composer | web 仓+本仓 | 入组件包 |
| workflow API/script 节点/input_schema | beehive core | 零改动 |
| **workflow 写锁** | beehive core | **新增能力**（R1）——backend 侧契约待定义（v0.3 与 backend 对齐） |
| agent runtime/serve SSE | 本仓 A1/A2 | 扩 canvas 工具+事件桥 |

**新写收敛**：组件包工程（抽包+宿主适配）/ canvas 官方 skill / 数据层描述规范 / core 写锁 / 会话流 action 渲染。

---

## 五、里程碑（v0.2：内部节奏，对外完整交付——R5）

| 内部阶段 | 内容 | 说明 |
|---|---|---|
| S1 组件包基建 | canvas 抽包+宿主适配层+两端各接入跑通（只读渲染） | 包工程先立——后续所有组件进同一轨道 |
| S2 数据层+锁 | 描述规范+core 写锁（backend 契约）+agent lock/unlock 包裹+手动遇锁 UX | 一致性根基 |
| S3 官方 canvas skill | 工具实现+端口自动判定+action 卡片+会话混合流 | 魔法时刻 |
| S4 创作闭环 | mediapool+产物预览+quote+双端功能对齐验收 | **对外完整交付点**（FR 全绿才交付） |

依赖排序：S1∥S2 并行（互不阻塞）→ S3 → S4。

---

## 六、待裁决清单（v0.2 更新）

**已关闭**：Q1-Q4（→R1-R6，见 §二）。

**全部关闭**（2026-09-27 Wesley 裁决）：
| # | 结果 |
|---|---|
| Q5 | **修正采纳**：锁=显式上锁/解锁动作对（无固定超时）；崩溃兜底=持有者存活检测（非定时器）——见 §二 R1 v0.3 表 |
| Q6 | 按建议执行：backend 锁契约 v0.3 草案随 backend 对齐流程定 |
| Q7 | 按建议执行：组件包在 pv-beehive-web 仓内抽包（代码不动仓，发布 subpath） |

**需求固化完成——待裁决项清零。下一步：backend 锁契约草案（v0.3）+ S1∥S2 排期（经 Wesley 确认后开工）。**

---

## 变更记录

- v0.1（2026-09-27）：初稿——需求陈述/三决策/FR 清单/复用盘点/里程碑草案/四待裁决。
- v0.2（2026-09-27）：Wesley 四项裁决落固——R1 锁协议（替代双写仲裁）/R4 官方 canvas skill/R5 完整交付+双端一致/R6 组件包；**"砍 WEB 编辑"边界二次修正：web console 不退役，支持两端**；新增 FR-0 双端架构与 FR-2 锁；复用表加 core 写锁依赖；里程碑改内部节奏+完整交付点；新待决 Q5-Q7（含默认值）。
- v0.3（2026-09-27）：Wesley 裁决 Q5-Q7——**Q5 修正：锁为显式上锁/解锁动作对（否决 60s 固定超时——"体验很差"），崩溃兜底改为持有者存活检测**；Q6/Q7 按建议执行。待裁决项清零，需求固化完成。
