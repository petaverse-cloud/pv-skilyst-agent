# Desktop MVP 重构——后端需求文档（Home / Projects / Workbench）

> 状态：v0.1——需求草案，待 Wesley 裁决后分发 backend
> 发起：Wesley 2026-09-30（desktop 布局重构方向裁决）。
> 关联：pv-skilyst-agent desktop 重构（本 profile 承载）；a3-canvas-workbench.md（A3 工作台形态，双端对齐既定）；pv-beehive-web `src/lib/api.ts`（现有消费侧契约）。
> 分发对象：backend（本文档主体）；platform 有一节独立诉求（§5，非阻塞）。

---

## 一、产品背景（已裁决方向）

Desktop 信息架构重构，最小化 MVP 模块集：

1. **Home = Gallery 进化**：类 Figma 首页定位——品牌叙事 + 作品展示（showcase）+ 生产力入口（新建/继续 project）
2. **Projects = 核心模块**：传统 project 域模型——用户建 project → 在 project 内 chat generate workflow、chat define nodes/params；chat history 属于 project 域，需保留并可回溯所有会话步骤
3. **Templates 退役**；**Canvas 不再是独立模块**（被 Chat Workbench 吸收为内嵌右栏，组件 @petaverse/skilyst-studio/canvas 双端共用）
4. Web console 同构反推：本方案确立的 Home/Projects 形态，web 侧后续跟进（"改造它，不是迁就它"，A3 既定原则）

**SSOT 原则（A3 既定）**：两端一致性靠"都渲染同一个服务端真源"。Project 与作品聚合是双端语义，因此放 beehive 后端，不做 desktop 私有实体。

## 二、现状契约盘点（2026-09-30 核实，消费侧代码为准）

已有能力（`pv-beehive-web/src/lib/api.ts` + openapi 真源 `pv-beehive-core/docs/openapi.yaml`）：

- `workflows`：CRUD + media-pool（list/get/add/remove/upload/promote）——**无 cover/封面字段，无来源标记**
- `assets`：CRUD + multipart upload；字段 id/name/asset_type/url/mime_type/file_size/tags/created_at/updated_at——**无 thumbnail 字段，无"作品集"聚合语义**
- `jobs`：create/get/result/cancel/executeNode/runAll——**无按作品维度的输出聚合视图**
- `templates`：CRUD（本方案下退役，见非目标）
- `billing/wallet`、`auth`、`admin/users`：与本需求无冲突

**缺口结论**：后端没有任何"project"容器实体；workflows/assets/jobs 是三个平铺列表，无法支撑 Home 作品流和 project 域模型。

## 三、BR-1（P0）：Project 实体

**需求**：用户级 project 容器，聚合 workflows（远端实体）与展示性元数据。

字段建议（命名遵循现有 uid/xxxUid 惯例）：

```
Project {
  project_uid: string
  name: string
  owner_uid: number
  workflow_uids: string[]        // 引用，非外键强约束（workflow 删除即从列表剔除）
  cover_asset_uid: string | null // 可选封面（P1 可先空）
  created_at / updated_at: string
}
```

端点（遵循 /api/v1 前缀 + 现有 REST 风格）：

```
GET    /api/v1/projects?limit&offset     # 本人列表，updated_at desc
POST   /api/v1/projects                  { name }
GET    /api/v1/projects/{project_uid}
PUT    /api/v1/projects/{project_uid}    { name?, cover_asset_uid?, workflow_uids? }
DELETE /api/v1/projects/{project_uid}    # 仅删容器，不级联删 workflows
POST   /api/v1/projects/{project_uid}/workflows/{workflow_uid}    # 挂载
DELETE /api/v1/projects/{project_uid}/workflows/{workflow_uid}    # 摘除
```

验收标准：

1. CRUD 全绿（含鉴权：仅 owner 可读写）
2. workflow 被删除后，projects 列表视图不再返回悬空引用（服务端过滤或标记）
3. 分页语义与 assets/jobs 现有 limit/offset 一致
4. openapi.yaml 同步更新（契约真源，从 handler 注解生成）

**Desktop 消费方式**：新建 project → POST create；agent 在会话中创建 workflow（canvas tools，服务端已有）→ desktop 拿到 workflow_id 后调用挂载端点。chat history 归属（session 列表）为 runtime 本地域，desktop 端按 project 本地记录，**不进后端**（见 §5）。

## 四、BR-2（P0）：Home 作品流（works feed）

**需求**：Home 页的"作品展示"数据源。聚合用户全部可见产出，单端点、可分页。

```
GET /api/v1/home/feed?limit&offset&kind
```

返回条目（统一卡片模型）：

```
WorkCard {
  kind: "workflow" | "asset" | "job_output"
  ref_uid: string
  title: string
  cover_url: string | null     # 关键缺口：现无任何封面来源
  updated_at: string
  project_uid: string | null
}
```

排序：updated_at desc。kind 过滤可选（第一期可只做全量混排）。

**关键子需求——封面**：现状 workflows 无 cover 字段、assets 无 thumbnail。最低成本路径（backend 定夺）：

- 方案 A：workflow 增加可选 `cover_asset_uid`，agent/用户把 mediapool promote 出的 asset 设为封面
- 方案 B：feed 端点服务端推导（workflow 取其 media-pool 首图，asset 取原图 url）

MVP 可接受 cover_url 为 null（前端占位图），但字段必须在契约里，避免二次破坏性变更。

验收标准：

1. 空数据返回空列表不报错
2. 三类 kind 至少 workflow/asset 可返回真实条目
3. 分页正确（offset 翻页无重复）
4. openapi 同步

## 五、BR-3（P1，platform 分发）：workflow 溯源标记

Agent 会话创建的 workflow 与用户手动创建的，双端应可区分归属（Home 卡片角标"agent created"、project 自动挂载依据）。

诉求：workflow 实体增加可选元数据 `created_via`（"web" | "desktop" | "agent"）+ `source` 自由文本。canvas tools 的 create_workflow 透传即可（runtime 侧一行参数，**涉及 src/，归 platform**，backend 只需契约收口）。

不阻塞 MVP：desktop 可在挂载 project 时本地记录来源，作为降级路径。

## 六、非目标（明确不做，防止范围蔓延）

1. **Public gallery / 分享页**：Home 第一期只做"我的作品"，社区/营销 showcase 是第二期
2. **Templates 数据迁移**：退役即可，存量 templates 数据不动不迁
3. **Session/对话数据上后端**：会话与 transcript 属本地 runtime（pv-skilyst-agent src/session），SSOT 在本机磁盘；后端不存对话内容（隐私 + 域边界）
4. **Billing/钱包变更**：现有 D3 展示性余额链路不动

## 七、待裁决问题（backend 评审时回应）

1. BR-1 挂载端点 vs 直接 PUT workflow_uids 数组——倾向独立挂载/摘除端点（并发友好），backend 定
2. BR-2 封面方案 A/B 取舍与排期
3. project 数量上限、命名约束（是否复用 assets 的校验惯例）
4. BR-3 created_via 枚举是否需要预留 "api"（第三方 API key 调用方）

---

## 附：desktop 侧交付依赖关系

| Desktop 里程碑 | 依赖 |
|---|---|
| M1 布局重构（Home/Projects/Settings 导航 + workbench 内嵌 canvas） | 无后端依赖，纯前端，先行 |
| M2 Projects 域（project CRUD + history 归属） | BR-1 |
| M3 Home 作品流 | BR-2（封面可为 null） |
| M4 归属角标/自动挂载 | BR-3 |

M1 不等后端；BR-1/BR-2 是 M2/M3 的硬前置。desktop 侧对 openapi 变更的消费将走 `@petaverse/skilyst-studio` 包 + 本地 api.ts 适配层。
