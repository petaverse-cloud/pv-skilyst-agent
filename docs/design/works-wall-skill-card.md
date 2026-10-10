# #54 卡片设计稿 — Home 作品墙 skill 维度（desktop 侧）

> 作者：skilyst-desktop-app（角色署名：团队共用 Wesley 账号，本稿出自 desktop 领域主理人）
> 状态：设计稿（空档期产出；解封后照稿实现——planner 建议采纳）
> 对齐：product-system-goals-v1 §3.1 目标 4 / skills v0.3 §1-§2（作品墙条目主体 = skill，workflow 是物化产物带 content-hash 快照）/ web#356（双宿主同轴）
> 铁律：English-only 对外文案；响亮失败；消费侧不先于 API 冻结（审计规则 4——本稿为 UI 结构设计，数据字段以 core P1 冻结为准）

## 1. 数据契约假设（待 core P1 冻结核对，字段名可能变）

作品墙列表（web console 与 desktop 共用同一 core API）预期形状：

```
GET /api/v1/skills …/published（或 workflows 列表扩展 materialization 关联）
entry {
  skill_id, display_name, version, author { name, verified }
  attribution_chain [ ... ]        # 署名链（fork 树数据源，最多 3 级参与分成）
  reference_workflow {             # v0.3 §1: 随 skill 携带的参考快照
    id, content_hash,
    cover_url,                     # 成品缩略（现有 nameCover 兜底继续工作）
    artifacts { count, kinds }     # 成品媒体概览
  }
  fork_depth, usage_count
  pricing { price_usd, free }       # $0 = 免费
  my_relation: owner | forked | none   # web 三 tab 同源字段
}
```

**设计不变量**（无论 API 最终字段名如何）：每张卡必须有 ① skill 标识（名称/作者/版本）② 一个视觉主体（cover 或生成兜底）③ 血缘暗示（fork 链）。字段映射在 beehiveClient.ts 单点收敛，API 变了只改一层。

## 2. 卡片视觉规格（延续现有 WorkflowCard 骨架，Mantine 深色）

```
┌────────────────────────────────┐
│ [视觉主体区 130px]              │  cover_url 优先；无 cover 用
│   cover 或 nameCover 兜底      │  nameCover(name) 现有渐变兜底
│   右上角: 价格角标              │  free → Badge "Free" (绿色 light)
├────────────────────────────────┤  付费 → "$12" (金色 light)
│  Skill 名称 (Text fw 600)      │
│  by <author> ✓ · v1.2          │  verified 作者带 ✓（v0.2 author.verified 字段）
│  ────────────────────────────  │
│  底行 (Group justify space-between):
│    左: fork 链深度角标          │  "2 upstreams" (dimmed xs) —
│    右: <N> runs · <M> outputs   │  usage_count + artifacts count
└────────────────────────────────┘
hover: translateY(-2px) + 阴影（现有交互保留）
click: 导航 workbench 绑定 reference_workflow（现行为）+ 后续 M3 物化入口
```

- **"Made with" 明示**：卡内副行固定 `made with <skill>` 替换现在的 `agent-created` 计数逻辑（created_via 语义升级为 skill 署名；老的 created_via 字段继续渲染为 dimmed 徽标以兼容未迁移数据）
- **fork 树不做卡内展开**（web#356 的树可视化在 console 详情页）；desktop 卡只显示深度角标——双宿主分工：desktop = 消费/入口，console = 管理/浏览深度

## 3. tab 结构（对应 web 三视图，desktop 简化为过滤）

Home 的 "Your works" 区加一组 Segmented Control：`Discover | My skills | Forked`（数据未冻结前仅渲染 Discover=全部）。web 的 "Published" tab 在 desktop 合并入 My skills（创作者经济的管理动作归 console，desktop 不做发布流——R6 分工）。

## 4. 降级路径（响亮失败）

- API 冻结前：现状渲染（workflow 主体）+ 顶部 dimmed 提示 "skill dimension lands with the skills registry"（不可点，不是错误态）
- 字段缺失（skill 无 reference_workflow）：视觉主体用 nameCover 兜底，无 runs/outputs 行——不是错误
- API 403/404：沿用 G4-15 红横幅模式（现状）

## 5. 实现工料预估（解封后）

beehiveClient.ts +1 函数（列表换源）；HomePage WorkflowCard 改造 ~80 行；tab ~40 行；测试 +3（字段映射/兜底/tab 渲染）。**零 runtime 改动**——数据面全在已合的 /beehive/* 或 /api/v1/* 通道上。
