# QA 测试报告 — issue #13：A3 画板工作台全量回归 + 双端对齐验收

执行：qa（独立复核，未抄用 platform 自测数据） | 2026-09-28 21:10~23:15 CST
环境：dev API beehive-api.verse4.pet（真实集群）；e2e-platform（uid 213508887911264，钱包起点
$9,992.26 → 终点 $9,990.26，测试净花费 $2.00 均为 issue 指令内付费 job）；serve runtime :8801
（--live，AK/SK 通道）；web console 本地 next dev（main @ccb09bf）+ desktop tauri dev（main @eed67c8）。
证据目录：evidence/qa-a3/（本目录，逐项 json/png/md）。

## PASS/FAIL 矩阵

### 1. 三条 E2E 创作故事（真实环境真实付费）
| # | 检查 | 判定 |
|---|------|------|
| T1a-1 | 视频全链走通（script→分镜→$0.96 参考图确认门→首帧接线→$1.035 minimax 15s 确认门→MP4 落池 CDN） | PASS |
| T1a-2 | 每步 action 卡片附着对应输入下（seq 单调，30 actions/4 turns） | PASS |
| T1a-3 | 画板节点/连线与操作一致 | PASS |
| T1a-4 | 产物可预览（image+video 双产物落池，双端 Preview 历史+done badge） | PASS |
| T1a-5 | 首帧接线在画板 SSOT 上有端口语义（material_deps+input_port） | **FAIL**（bug①，见下） |
| T1b-1 | query_schema 先行（铁律） | PASS |
| T1b-2 | 端口互斥→结构化拒绝+agent 自修正（无裸 400 抛给用户） | PASS |
| T1c-1 | agent 持锁期手动写→409+note"画板正被占用"+holder 详情+不重试 | PASS |
| T1c-2 | 锁下读不受影响 | PASS |
| T1c-3 | 释放后重试成功 | PASS |
| T1c-4 | kill serve 后死锁 90s 存活检测自动接管（实测 kill→grant 92s） | PASS |

### 2. 双端功能对齐（ALIGNMENT-CHECKLIST 11 项独立复核）
11/11 PASS。web 6 nodes/5 edges ↔ desktop 6 nodes/5 edges，节点标题逐字一致；30 张 action 卡片
双端同数；locate/quote 卡/mediapool/livedBoard 同包同源。详见 2-t2-summary.md。
衍生低危发现：action-cost 徽标因 total_estimate_usd=null 永不显示（bug③）。

### 3. 计费诚实性
| 检查 | 判定 |
|------|------|
| quote=reserve=settle 三段一致（gpt-image-2 $0.96 / minimax-h3 $1.035） | PASS |
| action 卡片 cost 与 ledger 对账 | PASS |
| 钱包差=settle 总和（1,995,000 µUSD，无隐藏扣费） | PASS |
| per_second 计价正确（69000×15s=1,035,000） | PASS |

### 4. 会话流边界
| 检查 | 判定 |
|------|------|
| 纯对话输入不产生 action 条目 | PASS |
| action 条目 origin=agent + board_delta（mutating） | PASS |
| 锁提示 note（note_kind=lock）出现且带 holder 详情 | PASS（T1c 实测） |
| note origin 标记：锁提示 origin=system（R3 文档写 agent\|user——语义偏差，见 bug②备注） | 备注 |
| chat API 兼容：history() 过滤 action/note，同会话后续纯对话正常 | PASS |

## 发现的缺陷

**bug①（medium，r:platform）agent 建 material 节点用 config.entry_id 而非 pool_entry_id →
connect_ports 静默降级，input_port 丢失**
- 现象：T1a 板上 minimax 节点 material_deps=null；agent 传 input_port=first_frame 的连线被
  connect_ports 的 is_material 判定（检查 config.pool_entry_id）漏过，降级为普通 depends_on 边。
  本次产物碰巧正确是因为模型自行把 images[]/image_roles[] 手写进了提交 config（读板→自补），
  但画板 SSOT 无端口语义、read_board 不显示 material 边、行为不可依赖。
- 根因评估：runtime（canvas.py connect_ports）与 SKILL.md 规范（pool_entry_id）字段名不匹配场景下
  无防护——is_material 为 False 时不拒绝 input_port 参数，静默丢弃。代码 bug（缺校验）+ 模型未按
  SKILL.md 写字段（无兜底）。
- 复现：POST /message "把池里第 1 张接到 minimax 首帧"，agent 用 canvas_create_node(material,
  config={entry_id:...}) 建节点后 connect_ports(input_port=first_frame)；读板看
  minimax.config.material_deps=null。
- 证据：17-t1a-final-board.json（material-image-3 config.keys=[entry_id,...]、minimax 无 material_deps）、
  15-t1a-turn3-video-sse.json（connect 调用记录）、job input（images[] 实际来自模型手写 config）。

**bug②（low，r:platform）note 条目 origin 与 R3 契约偏差**
- R3 契约：note origin ∈ {agent, user}（手动变更/锁提示区分来源）。实现：runner.record_action 里
  锁提示 note 固定 origin="system"（src/agent/runner.py:123），user 来源的手动变更 note 通道未见
  （桌面手动编辑是否写 note 待 platform 确认）。语义偏差非功能故障——T4/T1c 行为本身正确。

**bug③（low，r:platform）action 卡片 cost 徽标永不显示（estimate_usd=null 路径）**
- ActionCard 仅在 cost.estimate_usd 存在时渲染徽标；dev 计价链 per_token 模型 total_estimate_usd=null
  → cost 只带 hold_micro_usd → 付费 action 卡无任何金额显示（T1a 双端 action_cost=0）。用户在卡片上
  看不到 $0.96/$1.035。证据：2-t2-web-actions.json、2-t2-desktop-final-dom.json。

**环境备注（不计缺陷）**：web 仓本地 dev 仅 npm ci 可用（pnpm 布局破坏 file: 子包解析）；next dev
必须用 :3000（core CORS allowlist）。建议 DEVELOPMENT.md 标注。

## 总判定
四块 22 项主检查 21 PASS / 1 FAIL（bug①）。A3 S4 交付整体质量良好：三条故事、计费诚实性、双端
对齐、会话边界全部达到验收基准；唯一实质缺陷是 material 接线的字段名静默降级（bug①），不阻塞
"能创作出正确产物"，但破坏画板 SSOT 的端口语义，应在锁转强制前修复。

## 纪律符合性
- DB 仅 SELECT（kubectl run postgres:16-alpine 临时 pod，用完即删）；无任何写操作
- 凭据零入库（~/.skilyst/env 运行时读取；证据文件中 token 已处理）
- 真实数据真实 API 真实付费（净 $2.00，issue 明示放行）
