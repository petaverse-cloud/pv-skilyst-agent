# T3 计费诚实性 — 结果（节存盘）

时间：2026-09-28 22:05 CST | DB 只读 SELECT（kubectl run postgres:16-alpine 临时 pod 模式）

## T1a 两个付费 job 的 quote=hold=settle 三段核对

| job | 节点 | quote(confirm_request SSE) | reserve(hold) | settle(实际) | 判定 |
|-----|------|---------------------------|---------------|--------------|------|
| job-1790601545790-… | gpt-image-2 参考图 | total_hold=960000 µUSD ($0.96)（16-t1a-confirms.json） | 960000 | 960000 | PASS 三段一致 |
| job-1790601716186-… | minimax-h3 15s 视频 | total_hold=1035000 µUSD ($1.035) | 1035000 | 1035000 | PASS 三段一致 |

## action 卡片 cost vs ledger
- 参考图 action（seq 36）：cost.hold_micro_usd=960000 ↔ ledger reserve 960000 ✓
- 视频 action（seq 84）：cost.hold_micro_usd=1035000 ↔ ledger reserve 1035000 ✓

## 钱包总账
- 起点 9,992,259,486 µUSD（10-t1a-wallet-start.json，$9,992.26）
- 终点 9,990,264,486 µUSD（18-t1a-wallet-end.json）
- 差 1,995,000 = 960,000 + 1,035,000（两 settle 之和），无隐藏扣费 ✓
- per_second 计价核实：minimax 69000 µUSD/s × 15s = 1,035,000 ✓

## 判定：T3 全 PASS（2/2 job 三段一致 + 卡片对账 + 总账闭合）

原始 SELECT 输出见 3-t3-ledger-select.txt。
