# Figma Desktop 逆向分析——借鉴要点（2026-10-08）

> 对象：/Applications/Figma.app v126.9.11（Electron，本地静态分析 asar + native binaries）
> 方法：容错 asar 解包（Apple ElectronAsarIntegrity 签名头负 size 条目，官方工具拒绝）+ 字符串/符号反查
> 目的：为 skilyst desktop MVP 重构（#31）提炼可借鉴的架构与实现技巧
> 性质：内部研究，不分发（含反编译产物，仅本地 scratch）

## 一、Figma Desktop 整体架构（实测结论）

**三进程四层架构：**

```
Electron main (main.js 3.6MB, 单 bundle)
 ├─ shell 窗口层：shell.html + desktop_shell.js (React) —— 宿主 chrome（tab 栏/标题栏/拖拽）
 ├─ TabView 层：每 tab 一个 WebContentsView，web app（figma.com 全功能编辑器）远程加载
 │    └─ 每种 view 独立 preload：web_app / shell_app / popout / overlay / detached / tray（6 种 binding_renderer）
 ├─ utility process：bindings_worker.js（native 调用隔离）
 └─ 旁路进程：FigmaAgent.app（独立 .app，开机代理，HTTP 127.0.0.1:44950）
```

关键事实：

1. **编辑器本体不在客户端 bundle 里**。desktop 壳只负责窗口/菜单/tab/系统集成，真实编辑器 = 远程 web app 装进 WebContentsView。asar 仅 9.3MB，其中 node_modules 只有 chokidar 一个运行时依赖（package.json 原话："Consider carefully adding new dependencies. NPM modules often contain a lot of transitive dependencies which increases code size and decreases load times"）
2. **主进程深度参与业务**：livegraph 实时同步客户端跑在 main process（371 处引用，batch_mutations/rebase_optimistic_mutations 协议），tab 状态、mutations 批处理都在 main——不是"主进程只管窗口"的浅壳
3. **native 层双件**：bindings.node（C++/win 侧 GPU 统计）+ desktop_rust.node（Rust/neon-1.1.1，cxx-1 桥，FontVariationAxis 等结构）——字体库、talon（captions 语音 SDK）流式接口
4. **字体服务三路**：utility process 优先（崩了 fallback 直调 native，再 fallback 空对象）——native 调用全部有降级链，启动永不因 native 崩死

## 二、模块清单（asar 内实体）

| 模块 | 文件 | 作用 |
|---|---|---|
| 主进程 | main.js | 全部窗口/tab/菜单/livegraph/agent/updater |
| 壳 UI | shell.html + desktop_shell.js + desktop_shell.css | React 写的 tab 栏/标题栏（css-modules 混淆类名）|
| view binding | web_app_binding_renderer.js 等 6 个 | 每种 webContents 一个独立 preload，最小暴露面 |
| loading | loading_screen.html + mp4 | 启动动画，主题感知，出错换错误态 |
| 懒加载外置 | lazy/external/*.js | codegen_mcp_server / fcm_manager / mcp_client_manager / proxy_agent（4 个重型模块 require 延迟）|
| i18n | i18n/*.json（8 语言）| **主进程原生菜单本地化**，不依赖 web app |
| 代理进程 | FigmaAgent.app | 开机驻留，主 app 关了也能处理 deep-link/open-in-app（desktop_state.json 记录 appOrigin/authedUserIDs）|
| native | bindings.node / desktop_rust.node | 字体枚举/预览/GPU 统计/talon 流 |

## 三、值得借鉴的实现技巧（按对 skilyst 的适用度排序）

### A. 直接可用（M1 就该抄）

1. **每个 webContents 场景一个窄 preload**（6 个 binding_renderer）。我们只有一个 webview，但同理：shell 与 web 前端之间的桥只暴露最小 API（现状 Tauri invoke 白名单已是此思想，制度化它）
2. **sandbox: true + nodeIntegration: false + contextIsolation: true + will-navigate/will-redirect 全 deny**——web 内容永远不许自行导航，跳转一律经 shell 判定后开系统浏览器。与我们的安全边界完全同构，Figma 证明了这条路不牺牲体验
3. **主进程原生菜单的 i18n**：菜单文案打包进 app 而非从 web 拉取——我们的 Rust 侧菜单（将来做原生菜单栏时）直接采纳
4. **自诊断菜单（DEBUG only）**：Crash Renderer / Crash Main / Throw Uncaught / Reject Promise——给 dev build 一个"混沌菜单"，验收时手工触发故障路径。正好补我们"响亮失败"的验证手段
5. **loading screen 有主题/错误态/超时兜底**：启动动画超时计 level warning 并继续——我们启动 runtime 45s READY_TIMEOUT 可借鉴"超时不白屏，出错误态+重试"

### B. 中期采纳

6. **FigmaAgent 旁路进程模式**：deep-link 归属在独立驻留进程，主 app 未启动也能接管（desktop_state.json 做交接）——这正好回答我们 #25/#31 的冷启动竞态（deep-link 在 app 未运行时的 get_current 路径未经验证）：Tauri 单实例 + second-instance 转发，或排期一个 skilyst-agent 常驻 helper
7. **utility process 隔离 native 调用 + 三级降级**（utility→直调→stub）——我们 keychain/deep-link 若扩展重 native 面，照此隔离
8. **重型模块 lazy/external 化**：4 个重型 JS 全部运行时按需 require——我们 vite bundle 已自动 code-split，但 Rust 侧插件同理懒注册
9. **livegraph 在 main 进程做 mutations batch/rebase**：多 tab 共享一份订阅状态，tab 只是 view。若 skilyst 未来多窗口（workbench+预览窗），数据订阅收敛在 Rust 侧而非每个 webview 各自 HTTP 轮询——我们现在 5s 轮询在 React，多窗口后会重复请求
10. **desktop-file:// 自定义协议 + registerSchemesAsPrivileged + X-Token 头**：本地文件经自定义 scheme 授予 web 层，带 token 校验——比把文件路径直接塞给 webview 安全。Tauri 侧有 register_uri_scheme_protocol 对应能力，未来 mediapool 本地预览可用

### C. 工程纪律（最值得学的）

11. **运行时依赖只有一个 chokidar，且 package.json 里写明加依赖要三思**——9.3MB 总包 vs 我们 desktop/ node_modules 之臃肿，方向值得敬礼
12. **CSP 在 shell.html 显式收紧**（default-src 'none' 白名单制），即便本地文件也要 CSP
13. **App URL 子菜单**（dev build 可切生产/自定义后端）——给我们 dev build 一个"runtime endpoint 切换"菜单，联调 backend 环境时免改代码
14. **socket 连通性自检**（websocketstest.com + /waf-validation 页面）——启动后主动验证网络通路并给可诊断的错误。我们 runtime 健康流可加同类探针页

## 四、不建议学的（差异点）

- Electron（Chromium 全家桶）vs 我们 Tauri（WKWebView）——Figma 的 WebContentsView 多 tab 架构无直接对应，Tauri 1.x 多 webview 支持有限；tab 场景我们暂不需要
- 远程加载编辑器本体：Figma 的核心资产在浏览器端已达 10 年成熟度，我们的核心交互（chat+canvas）还在快速迭代期，先保持本地 vite bundle，等稳定后再考虑"壳+远程 UI"的热更模式

## 五、落到 skilyst 的行动项（建议进 #31 或新 issue）

| 行动 | 来源 | 优先级 |
|---|---|---|
| dev build 自诊断菜单（crash/fault 注入） | A4 | P2，随 #31 |
| 启动 loading 屏错误态+重试，超时不白屏 | A5 | P1，随 #31（现状是 Loader 文案，可接受，错误态补齐）|
| Rust 菜单 i18n 打包进 bin | A3 | P3 |
| 自定义协议 + token 头供本地文件 | B9 | P3，mediapool 本地预览时 |
| App URL/env 切换菜单（dev） | C13 | P2，backend 联调时需要 |
| 多窗口数据订阅收敛 Rust 侧 | B9 | 观察，暂不动 |
