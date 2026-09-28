# A3 S4 双端功能对齐验收清单（FR-0）

同一 workflow 在桌面 App 与 web console 两个宿主打开，逐项行为对比。
两宿主共享同一组件包（skilyst-studio）与同一 core API，差异只允许出现在宿主适配层
（host adapter）与会话通道（桌面: 内置 runtime；web: skilyst serve 直连）。

## 验收表

| # | 能力 | 桌面 App（desktop/） | web console（/studio/agent） | 判定 |
|---|------|----------------------|------------------------------|------|
| 1 | 画板渲染 | CanvasView → WorkflowCanvas designer | /studio/agent → WorkflowCanvas designer | 同一包同一组件 |
| 2 | 节点产物预览（FR-1） | liveBoard 显式开 | liveBoard 显式开 | 图片直显/视频缩略+点击播放（PreviewPlayer 同源） |
| 3 | job badge running→completed | liveBoard 轮询合成 + WS | 同左 | 10s 单飞轮询，同一合成逻辑 |
| 4 | mediapool 面板（FR-5） | MediaPoolPanel（引用徽标+重命名+删除） | 同左 | 同一组件；删除被引用资产 409 拒绝透传 |
| 5 | 手动编辑 | designer 全编辑（S4 起，拖拽/连线/属性面板） | designer 全编辑 | 同一包同一编辑器（FR-4） |
| 6 | 会话流+action 卡片（FR-6） | ConversationView+ActionCard（Mantine 壳） | ConversationStream+ActionCard（包内，tailwind） | 摘要/展开/定位三行为一致；包内 actionSummary 单一真源 |
| 7 | action 卡片定位画板 | result_ref→focusNode（视口居中+高亮 ring） | 同左（locate 回调） | 同一 focusNode API |
| 8 | quote 确认卡（FR-3） | QuoteConfirmCard（包内）+ 余额自取（beehive JWT） | QuoteConfirmCard（包内） | 同一组件；POST /confirm 同一端点 |
| 9 | 付费门（D3） | confirm_paid: true → SSE confirm_request → 卡片应答 | 同左 | runtime 不碰钱包；拒绝=零提交零扣费 |
| 10 | 输入引用解析 | canvas_list_media index（"第 3 张"）→ material 接线 | 同左（agent 侧能力，双端共享） | SKILL.md 固化流程 |
| 11 | SSOT 同步（FR-7） | liveBoard 轮询跟随 agent 写入 | liveBoard 轮询 + 自身 WS | 画板=workflow 渲染，写经 core 锁协议 |

## 通道差异（允许的差异，均在适配层）

- 桌面：runtime 由 desktop shell 内嵌启动（inShell），vite 同源代理 /api。
- web：`skilyst serve --cors-origin` 由用户在 /studio/agent 页连接（token 存 localStorage，
  零入库）；API 走 web host adapter 的 apiBaseUrl+JWT。

## E2E 存证

- evidence/a3-s4/01..05：对话驱动创作故事（脚本→分镜→参考图→连线→付费 15s 视频→落池）。
- evidence/a3-s4/app-*.png：桌面 App 侧画板+会话流。
- evidence/a3-s4/web-*.png：web console 侧同一 workflow 工作台。
