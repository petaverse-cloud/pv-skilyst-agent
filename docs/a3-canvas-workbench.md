# A3 画板工作台——需求固化文档（Demand Record）

> 状态：v0.1 需求固化稿（Wesley 评审中——**文档优先，未获裁决不动工**）
> 发起：Wesley 2026-09-27。"文档优先，禁止直接动手，我们要固化需求并记录过程"——本文档就是那个固化。
> 关联：pv-beehive-core#585（M1 真源）；本仓 A1/A2（运行时/桌面壳）；web 仓 canvas 复用盘点。

---

## 一、需求陈述（Wesley 原话提炼，逐字保留关键判断）

> "目前的交互我不满意，我们需要的不是一个普通的对话 app，我需要执行可视化，主要工作区还是借鉴 web console studio，**画板仍然是最核心的用户界面**。而**用户输入是控制画板的入口**。"
> "用户可以通过输入在画板创建 script 节点，并通过输入与节点交互生成用户期望的脚本内容。用户也可以拖入图片或使用图片生成节点生成图片作为参考图，可以简单管理用户资产（mediapool）。用户可以通过输入编辑节点和节点之间的关联。**节点仍然自动判定输入输出匹配节点内的属性**。"
> 手动编辑："保留用户手动节点创建/连线/编辑功能，并非唯一，**留个用户介入编辑的口子**。"
> 会话模型："会话 + action——基于会话历史，但不是每条会话都会有执行，**但凡有执行就需要和上一条会话在一起**。"

### 产品定性

**Agent 可视化创作工作台**：画板是主工作区（workflow 的 UI），用户输入是控制画板的主要入口（agent 驱动画板），用户手动编辑是保留的介入通道（双写）。不是聊天 app 加了画板，是画板加了 agent。

### 与既有决策的关系

- **修正 #585"砍 WEB 编辑"的边界**：被砍的是"web console 作为生产编辑器"的定位；canvas 的渲染与（现恢复的）手动编辑代码在 Skilyst Agent 桌面壳内复活——同一代码资产，新宿主。
- **兑现 D6**：作品 = workflow + skills set，画板就是 workflow 的可视化——fork-onboarding 打开画板即见节点图，输入改造它。
- **beehive SSOT 不变**：画板状态=workflow config，agent/手动/引擎三方共写一个真源。

---

## 二、需求决策记录（已关闭的三项 + 过程）

| # | 决策点 | 结论 | 过程记录 |
|---|---|---|---|
| R1 | 写权归属 | **双写**：agent 工具写 + 用户手动写，同走 workflow SSOT | planner 初案"agent 唯一写者"（避免双写冲突）→ Wesley 修正："并非唯一，留个口子"→ 定案双写+同流审计 |
| R2 | script 节点 | **零新增**——core 已内置 `process:script`（LLM=deepseek） | planner 初案误判需新节点类型（a 进 core / b 壳内虚拟二选一）→ Wesley 指出"core 已经有了"→ 定案：纯工具调用（create_node+write_node_config），全程现成基建 |
| R3 | 会话模型 | **会话+action 混合单时间轴**：纯对话条目与执行条目同流；执行条目=输入+action 卡片（可多）；action 卡片双向锚定（展开参数/定位画板） | Wesley 定义"但凡有执行就需要和上一条会话在一起"→ planner 结构化为消息模型扩展 |

### R1 双写的三条规定（planner 提案，待确认）

1. 手动与 agent 共用同一条 workflow 更新通道（beehive PUT）——服务端仲裁（last-write-wins/revision）
2. 用户手动变更也推送进会话流（系统侧 action 条目"你手动连接了 A→B"）——agent 干的与用户干的同流可见，无双盲区
3. agent 操作前 re-read（用户可能刚手动改过——不信任缓存）

### R3 消息模型（技术契约草案）

```
messages.jsonl 条目类型：
  user        用户输入（文本/附件引用）
  assistant   agent 纯文本回复（markdown 渲染）
  action      执行条目（由 tool_result 中含画板变更的升级而来）
              { type, tool, params, result_ref, board_delta, origin: agent|user, cost?, duration? }
  note        系统条目（手动变更提示/错误/费用说明）
action 卡片 UI 行为：点开=调用详情；点卡片=画板定位高亮
```

---

## 三、功能需求清单（FR）

### FR-1 画板主工作区
- 主区占比 ~70%，React Flow（web canvas 代码复用：渲染子集+写路径 hooks 解封）
- 缩放/平移/框选/节点拖动（画板本能，手动）
- 节点卡：类型徽章/config 摘要/运行态 badge/产物预览（PreviewPlayer 三态复用）
- 连线动画：agent/手动新增连线时画板呈现过渡（操作的可视化反馈）

### FR-2 输入驱动的节点操作（agent 工具集）
| 工具 | 语义 | 对应场景 |
|---|---|---|
| create_node(type, title?) | 建节点（含 process:script） | "创建一个钟馗夜巡 15 秒的脚本" |
| write_node_config(node_id, config) | 写节点内容（脚本生成结果填入 script 节点） | 与上条连续："分镜写细一点" |
| connect_ports(from, to, port?) | 连线（port 省略时 agent 自动判定） | "参考图1 连 minimax-h3 作首帧" |
| query_schema(node_id) | 读 input_schema（端口/枚举/互斥规则） | 端口判定的依据——不靠模型记忆 |
| generate_image(prompt, ref?) | 图片生成 job（产物入 mediapool+节点） | "生成一张钟馗三视图" |
| submit_node_job(node_id) / run_workflow() | 执行 | "跑这个节点"/"全部生成" |
| read_node_output(node_id) | 读上游产物（作下游素材） | "把脚本节点的输出接给 TTS" |
| list_media() / add_media(source) | mediapool 管理 | "把这张图加入素材池" |

- 端口匹配规则：**agent 必须先 query_schema 再 connect**（互斥规则如 minimax-h3 first_frame×reference_image 在 schema 里——lanes 实测 400 教训的制度化）；校验失败收结构化错误并自修正
- quote-first 不变：付费 job 前预估成本确认

### FR-3 手动编辑通道（保留口子）
- 手动建节点/连线/删改（web canvas 写路径复用）
- 手动变更同步推送：会话流系统条目 + agent 可见（R3 规定 2）

### FR-4 mediapool
- 侧栏面板（web MediaPoolPanel 复用）；拖入图片入池；图片节点产物入池
- 池资产被引用：输入提及（"用池里第 3 张"）或直接拖到节点端口（手动通道）

### FR-5 会话流（R3 混合模型）
- 单时间轴：user/assistant/action/note 四类条目同流
- action 卡片：可展开（参数/校验过程/耗时/花费）+点选定位画板
- 输入框常驻底部（Composer 复用+markdown 渲染沿用）

### FR-6 workflow SSOT 同步
- 画板=workflow 渲染；变更经 serve 事件桥推送（SSE）全端刷新
- 本地 transient 层（连线预览等）确认后落 workflow

---

## 四、复用资产盘点（已核）

| 资产 | 来源 | 规模 | 状态 |
|---|---|---|---|
| canvas 渲染+节点卡+hatch 预览 | pv-beehive-web components/canvas | ~6265 行 | 封存→**解封复用** |
| canvas 手动写路径 hooks | 同上 | ~1188 行 | 封存→**解封复用**（R1） |
| PreviewPlayer 三态预览 | 同上 | 组件级 | 直接复用 |
| MediaPoolPanel | 同上 | 组件级 | 直接复用 |
| workflow API+media-pool 端点+script 节点+input_schema | beehive core | 既有 | **零改动**（T3 scope 已覆盖） |
| agent runtime（loop/工具注册/会话） | 本仓 A1 | 既有 | 扩 canvas 工具集 |
| serve SSE 事件桥 | 本仓 A2 | 既有 | 扩 workflow 变更事件 |
| Composer+markdown | 本仓 feedback round1 | 既有 | 沿用 |

**新写量收敛为**：canvas 工具协议实现（FR-2 表 ~10 工具）+ 事件桥扩展 + 会话流 action 渲染 + web canvas 组件移植适配（脱 AuthGate 接 serve 鉴权）。

---

## 五、里程碑（草案，待裁决后排期）

| 里程碑 | 范围 | 验收标准 |
|---|---|---|
| A3-M1 画板骨架 | canvas 移植进壳+workflow 渲染+手动编辑解封 | 打开 App 即画板；手动建/连节点经 serve 落 workflow |
| A3-M2 agent 上板 | canvas 工具集+端口自动判定+action 卡片 | 输入"创建脚本→分镜→图生视频"全链画板可视化；手动/agent 变更同流审计 |
| A3-M3 创作闭环 | mediapool+产物预览+quote-first+四类条目全流 | 从零到 15s 视频全程不碰鼠标建节点（手动仅介入） |

---

## 六、待裁决清单（当前未决）

| # | 待决项 | planner 建议 |
|---|---|---|
| Q1 | R1 三条规定（同通道/同流审计/re-read）确认 | 如文 |
| Q2 | FR-2 工具集范围（10 个够不够？增删？） | 如表 |
| Q3 | 里程碑切分与排期 | A3-M1 先行（画板骨架——最大复用件先落地） |
| Q4 | web canvas 移植方式：整仓搬（fork web 组件进 skilyst-agent）还是引用（web 仓发组件包）——涉及两仓依赖方向 | 整仓搬（避免跨仓依赖耦合；web 仓的 canvas 后续退役到只读归档） |

**裁决前不动工。**（Wesley 纪律 2026-09-27）

---

## 变更记录

- v0.1（2026-09-27）：初稿——需求陈述/三项已关决策记录/FR 清单/复用盘点/里程碑草案/四项待裁决。
