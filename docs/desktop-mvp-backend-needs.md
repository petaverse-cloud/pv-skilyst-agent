# Desktop MVP 重构——后端需求文档（Home / Workbench，v0.2）

> 状态：v0.2——2026-10-08 Wesley 裁决修订：**放弃 project 叙事，沿用 workflow**（变更记录见附二）
> 发起：Wesley 2026-09-30（desktop 布局重构方向裁决）。
> 关联：pv-skilyst-agent desktop 重构（#31 承载实现）；a3-canvas-workbench.md（A3 工作台形态，双端对齐既定）；pv-beehive-web `src/lib/api.ts`（现有消费侧契约）。
> 分发对象：backend（本文档主体）；platform 有一节独立诉求（§4，非阻塞）。

---

## 一、产品背景（v0.2 修订）

Desktop 信息架构重构，最小化 MVP 模块集：

1. **Home = Gallery 进化**：类 Figma 首页定位——品牌叙事 + 作品展示（showcase）+ 生产力入口（新建/继续创作）
2. **创作域 = workflow**：不引入 project 概念。用户建 workflow → 在其会话中 chat generate nodes、define params；chat history 归属 workflow 域（本地 runtime session，desktop 端记录归属），保留并可回溯所有会话步骤
3. **Templates 退役**；**Canvas 不再是独立模块**（被 Chat Workbench 吸收为内嵌右栏，组件 @petaverse/skilyst-studio/canvas 双端共用）
4. Web console 同构反推（A3 既定原则："改造它，不是迁就它"）

**SSOT 原则（A3 既定）**：两端一致性靠"都渲染同一个服务端真源"。workflow 是 beehive 服务端既有实体，创作域直接复用，不新建容器。

**语义澄清（2026-10-08 Wesley）**：media_pool 是 workflow 内节点生成内容的聚合（workflow 私有素材库，BEE-142），**不是**作品封面来源。封面走 workflow 级属性（BR-A）。

## 二、现状契约盘点（2026-09-30 核实，消费侧代码为准）

已有能力（`pv-beehive-web/src/lib/api.ts` + openapi 真源 `pv-beehive-core/docs/openapi.yaml`）：

- `workflows`：CRUD + media-pool（list/get/add/remove/upload/promote，含 origin 溯源）+ 锁协议（A3 S2，lock/unlock/心跳）
- `assets`：CRUD + multipart upload——用户级资产库
- `jobs`：create/get/result/cancel/executeNode/runAll
- `templates`：CRUD（本方案下退役）
- `billing/wallet`、`auth`、`admin/users`：与本需求无冲突

**缺口结论**：workflow 无级别属性设置（元数据容器），无 cover 字段，无创建来源标记。除此之外创作域所需契约已齐。

## 三、BR-A（P0，backend）：workflow 级属性设置（含 cover）

**需求**：workflow 增加 workflow 级属性设置能力，作为双端共享的元数据容器。第一期字段：

```
WorkflowSettings {
  cover_url: string | null        // 显式封面（用户或 agent 设置）
  // 预留扩展：后续 workflow 级偏好等
}
```

实现路径建议（backend 定夺）：

- workflow 实体增加可选 `cover_url`，或 `settings` 子对象容纳（倾向后者，避免逐字段加列）——URL 指向 asset/media_pool 条目或外链
- 写路径走现有 `PUT /api/v1/workflows/{id}`（咨询式锁 MVP 语义不变）
- **canvas skill 的写通道可顺带设置 cover**：agent 在创作闭环里把代表产物设为封面（runtime 侧为 canvas tools 加可选参数，涉 src/ 归 platform，见 §4）

**Home 封面推导规则（desktop/web 前端约定，不进后端）**：
- 有显式 cover_url → 用之
- 无 → 由 workflow name 自动生成简化封面（本地生成，纯前端，零后端依赖）

验收标准：

1. workflow 创建默认 cover 为空，不破坏现有读写
2. cover 可经 PUT 设置/清除，双端 GET 均可见
3. 咨询式锁语义回归不破坏（cover-only 更新不触发锁拒绝——现状 PUT 本就不校验，钉住测试）
4. openapi.yaml 同步更新

## 四、BR-B（P1，platform 主责、backend 契约收口）：workflow 溯源标记

Agent 会话创建的 workflow 与用户手动创建的，双端应可区分（Home 卡片角标"agent created"、自动封面策略依据）。

诉求：workflow 增加可选元数据 `created_via`（"web" | "desktop" | "agent"）+ `source` 自由文本。canvas skill 的 create_workflow 透传（runtime `src/canvas.py:376` 现有 name/description 参数，加一个可选参数，platform 小改动 + backend 契约收口）。

不阻塞 MVP：desktop 可在本地记录归属作降级路径。

## 五、非目标（明确不做，防止范围蔓延）

1. **Project 实体**：已裁决放弃（v0.2）。workflow 即创作域单元；若未来出现"一个创作聚合多 workflow"的真实场景，再议容器
2. **Public gallery / 分享页**：Home 第一期只做"我的作品"，社区/营销 showcase 是第二期
3. **Templates 数据迁移**：退役即可，存量数据不动不迁
4. **Session/对话数据上后端**：会话与 transcript 属本地 runtime（pv-skilyst-agent src/session），SSOT 在本机磁盘
5. **Billing/钱包变更**：现有 D3 展示性余额链路不动

## 六、待裁决问题（backend 评审时回应）

1. cover_url 独立字段 vs settings 子对象——倾向后者（扩展性），backend 定
2. cover_url 校验策略（仅允许本服务 asset/media_pool URL，还是放开外链）
3. BR-B created_via 枚举是否预留 "api"（第三方 API key 调用方）

---

## 附一：desktop 侧交付依赖关系（v0.2）

| Desktop 里程碑 | 依赖 |
|---|---|
| M1 布局重构（Home/Workbench/Settings 导航 + workbench 内嵌 canvas） | 无后端依赖，先行 |
| M2 Workbench 域（workflow 列表入口 + history 归属记录） | 无后端依赖（workflow CRUD 现成） |
| M3 Home 封面显式化 | BR-A |
| M4 归属角标/agent 自动封面 | BR-B |

M1/M2 不等后端；BR-A 是 M3 的前置。desktop 侧对 openapi 变更的消费走 `@petaverse/skilyst-studio` 包 + 本地 api.ts 适配层。

## 附二：v0.1 → v0.2 变更记录（2026-10-08 Wesley 裁决）

1. **撤回 BR-1（Project 实体）**：v0.1 的 project 容器与 workflow 实质一对一，重复造壳。裁决"放弃 project 叙事，沿用 workflow"——project 概念从 UI 叙事到数据模型全面移除
2. **BR-2 重写为 BR-A（workflow 级属性设置）**：原 /home/feed 端点撤回；Home 作品流由 workflows.list 现有契约 + 封面推导规则（前端）支撑。media_pool 语义澄清：workflow 内节点生成内容聚合，非封面来源；封面默认由 workflow name 前端自动生成，显式 cover 为 workflow 级属性（BR-A）
3. **BR-3 改号 BR-B**：内容不变（created_via 溯源），补充 runtime 侧落点（src/canvas.py:376）
