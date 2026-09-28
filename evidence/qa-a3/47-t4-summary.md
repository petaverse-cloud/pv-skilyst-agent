# T4 会话流边界 — 结果（节存盘）

时间：2026-09-28 21:14~21:20 CST | serve pid 20336 port 8801 | 会话 20260928-211422-b0e11a

| # | 检查 | 判定 | 依据 |
|---|------|------|------|
| T4a | 纯对话输入不产生 action 条目 | PASS | roles=['user','assistant']，无 action/note（40/41 json） |
| T4b | action 条目 origin=agent | PASS | 4 条 action 全部 origin=agent（43 json） |
| T4c-1 | mutating action 带 board_delta | PASS | create_node → {added_node: generate-script-1}；read-only 工具（list/read_board/query_schema）合理地不带 delta——首轮 FAIL 是脚本断言过严，非产品缺陷 |
| T4c-2 | history() 过滤 action/note（chat API 兼容） | PASS | SessionStore.history() 实测 roles 全为 chat roles，polluted=0；同会话后续纯对话 turn 正常完成（ok=true, turns=1, tool_calls=[]），LLM 消费 history 未受新条目类型影响（46 json） |

备注：serve 无 GET /session/{id}/history 子路由（路径并入 session id → 400）。这是 API 面设计
（history 是 runner 内部通道，shell 拿 messages 全量），不构成缺陷；桌面 shell 用 GET /session/{id}。
