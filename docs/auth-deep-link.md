# Auth: Deep-Link Login Flow（Figma 式登录态传递）

> 状态：v0.1 设计稿（App/CLI 侧实现依据；backend 端点契约在 §五）
> 关联：pv-beehive-core #587 PR §五（一次性 token 交换端点设计稿——PKCE/单次消费/AgentScopes 上限）
> 需求：Wesley 2026-09-24 体验反馈第 2 条

## 一、目标

用户在 App/CLI 上点击登录 → 系统浏览器打开 web console（bee.verse4.pet）→ 用户用现有账号登录 → 登录态经回调传回 App/CLI → token 安全存储。**不自建认证，复用 beehive 账号体系**。完整生命周期：登录 / 登出 / 会话过期。

## 二、流程时序

### GUI（Tauri）

```
[未登录首屏] --点"登录"--> [唤起系统浏览器]
  https://bee.verse4.pet/login?device_code={DC}&client=skilyst-agent&redirect_uri=skilyst://callback
        │
        ▼ （用户在浏览器完成现有登录）
[web console 检测已登录 + device_code 会话] --授权确认--> [302 跳转]
  skilyst://callback?code={ONE_TIME_CODE}
        │
        ▼ （Tauri deep-link plugin 捕获）
[App 用 code + PKCE_verifier 调 POST /auth/token] --> {access_token, scopes, account}
        │
        ▼
[存 OS keychain] --> [登录态 UI：header 账号 + settings 登出]
```

### CLI

同上，回调改为 `http://127.0.0.1:{port}/callback`（loopback listener，`skilyst login` 启动时随机端口）——OAuth CLI 标准模式（RFC 8252）。

## 三、安全条款（对齐 #587 §五）

1. **PKCE**：App 侧生成 code_verifier + code_challenge（S256），device_code 发起时带 challenge，兑换时带 verifier
2. **一次性 code**：60s 有效、单次消费、服务端只存 hash
3. **scope 上限**：兑换产物 token 的 scope 上限 = AgentScopes 预设（jobs/assets/workflows——无 billing/admin）——D3（agent 永不碰钱）在登录链路同样成立
4. **明文不落盘**：token 只进 OS keychain（macOS Keychain / Windows Credential Manager / libsecret）——Tauri stronghold 或 keyring plugin
5. **登出**：清 keychain + 服务端 revoke（若有端点）→ 回未登录首屏
6. **过期**：401 时回登录引导（保留会话数据）

## 四、客户端状态机

```
UNAUTHENTICATED ──点击登录──> AWAITING_BROWSER ──code 到达──> EXCHANGING
     ▲                                                    │成功
     │登出/过期                                            ▼
     └──────────────────────────────────────────── AUTHENTICATED
```

- 开发者通道：`SKILYST_DEV_PROFILE` 指向含 BEEHIVE_PLATFORM_* 的 .env 时跳过登录（现有 ~/.skilyst/env 模式保留为 dev）
- Mock 模式：`SKILYST_MOCK_AUTH=1`——本地模拟 device-code 发起与兑换（无 backend 时可演示/测试全流程）

## 五、Backend 端点契约（待 backend 落地——本 PR 不改 beehive-core）

### POST /auth/device-code（发起）

```json
// request
{ "client": "skilyst-agent", "code_challenge": "{S256}", "redirect_uri": "skilyst://callback" }
// response 201
{ "device_code": "dc_xxx", "expires_in": 300, "interval": 5 }
```

### POST /auth/token（兑换）

```json
// request
{ "device_code": "dc_xxx", "code": "{one_time_code}", "code_verifier": "{verifier}" }
// response 200
{ "access_token": "...", "account": {...}, "scopes": ["jobs:read", ...], "expires_in": 86400 }
```

约束：code 单次消费 60s TTL 只存 hash / 兑换 scope 强制裁剪到 AgentScopes / 失败计数限流。

web console 侧需要：登录页识别 `?device_code=` 参数 → 登录成功后展示"授权 Skilyst Agent？"确认 → 302 到 redirect_uri+code（pv-beehive-web 的改动，单独 issue）。

## 六、CLI 命令

- `skilyst login`：loopback listener + 开浏览器 + 兑换 + keychain 存 token
- `skilyst logout`：清 token（+revoke 尝试）
- `skilyst whoami`：显示当前账号/scope/token 有效期

## 七、实现批次

1. App/CLI 侧全实现 + mock 模式（本 PR）
2. backend 端点（下一个 issue，r:backend，契约见 §五）
3. web console 授权确认页（pv-beehive-web，随 2）
4. 真实联调 + 签名 App 的 URL scheme 注册验证
