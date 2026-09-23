# T4 — official skills bundle v0.1 (5 skills, manifest v0.2 sidecars)

| 项 | 值 |
| --- | --- |
| 任务 | M1 T4：官方 skills 预载包首版（pv-skilyst-agent `skills/official/`） |
| 依赖 | A1 runtime（安装器 / 加载器 / preflight）、manifest 规范 v0.2（pv-beehive-core#584 合并后） |
| 交付 | 5 个官方 skill（SKILL.md + manifest.json 双件）+ 预载包清单 + 打包器 + 验收测试 |
| 分支 | `platform/t4-official-skills` |

---

## 1. 交付清单

| 文件 | 说明 |
| --- | --- |
| `skills/official/{doctor,embed-video,lipsync-audio-refs,prompt-craft,video-15s}/` | 5 个官方 skill，每个含 `SKILL.md` + `manifest.json` + `LICENSE` + `references/i18n/glossary.{zh-CN,en}.json`（`doctor` 另有 `scripts/doctor.sh`） |
| `skills/official/index.json` | 预载包清单（authoritative）：skill_id / version / kind / content_digest / tree_digest |
| `skills/official/bundle.json` | 安装器入口视图（derived，二者必须一致） |
| `skills/official/README.md` | 包契约说明（双清单关系、打包/校验命令、preload 用法） |
| `tools/pack_official_bundle.py` | 打包器/校验器：唯一写入方（写 content_digest + 两个清单；`--check` 供 CI） |
| `tests/test_official_bundle.py` | T4 验收测试（16 项：v0.2 字段、node_id+binding、i18n 术语表、引用解析、预载供应链校验） |
| `src/skills/store.py` | 预载改为读 `index.json`（authoritative），双清单不一致即拒绝，逐条校验 content_digest |

---

## 2. 方法论来源（lanes 提炼，非搬运）

每条规则都标注了"为什么"（实测来源），正文不再复述。提炼来源：

| skill | 来源 | 关键实测教训 |
| --- | --- | --- |
| `embed-video` | lanes 钟馗 EP01 i2v 管线（cycle-2/3 报告）、trailer v2.4 帧链指令、core `seedance_mode.go` 校验器 | 首帧模式与参考图混传被上游拒绝（首次提交 $0 损耗）；帧角色存在时 N 张图 > 帧角色数 = 同一混传；i2v 帧人脸阈值 0.60/0.50（静态图才用 0.70，8 shots × 7 frames 实测） |
| `lipsync-audio-refs` | LIP-1 穿刺报告、cycle-4 口型批次（5/5 复现） | audio 模式禁 first_frame（混传 400）；audio_refs 单条 2-15s（1.836s 被拒，speed 0.6 重生成 2.34s 通过）；成片自带 AAC、音色重合成（F0 82.7→87.3Hz）；静默嘴动 std 0.5 vs 语音 5.0 |
| `prompt-craft` | `research/visual-language/` 语料（prompt-library / cinematic-language-guide / serialized-drama-guide / awesome-seedance-integration） | 结构骨架槽位顺序；分模型差异（H3 不保证 bracket 命令；seedance @引用+分时段；veo 冒号台词防字幕烧录 + 8 秒 12-15 词）；参考锁定五段语法（只传图不写锁定＝风格图）；3 秒 8 词判据（本线 15 词/3.3s 实测偏快） |
| `doctor` | qa 评审设计 + 本机实测输出 | 受限令牌 scope=jobs/assets；`/api/v1/billing/wallet` 被客户端门禁拒绝而服务端对受限 key 仍返回 200 —— 配额不可由 agent 读取，必须如实报告而非编造数字 |
| `video-15s` | A1 样板 + v0.2 规范 | 见下方 §4 修正项 |

---

## 3. 平台事实（本次实测采集，作为 node 依赖与参数约束的依据）

`GET /api/v1/nodes`（beehive-api.verse4.pet，受限 key，2026-09-23）：**21 个节点定义，`schema_version` 全为 27**。本包引用到的：

| node_id | node_type | duration | resolution | 备注 |
| --- | --- | --- | --- | --- |
| `generate:minimax-h3` | generate | 4-15 | 768P / 2K | 69000 µUSD/s，768P 维度比 1.0、2K 1.6；RPM 100 |
| `generate:byteplus-seedance-2.0` | generate | 4-15（text/image/frame）、4-30（带 `video_refs`）、-1 auto | 480p/720p/1080p/4k | 144100 µUSD/s；RPM 2 |
| `generate:nb2-image` / `nb2-lite-image` / `gpt-image-2` | generate | — | — | 图像节点，用于生成锁定首帧 |
| `generate:tts-minimax-hd` / `tts-minimax-turbo` / `tts-deepgram` | generate | — | — | TTS，42000 µUSD/piece（HD） |
| `generate:drawnow` | generate | — | — | seedance 同族通道 |

`image_roles` 枚举三值：`first_frame | last_frame | reference_image`（minimax-h3 与 seedance 一致）。
混传禁令的机器实现：`internal/api/validation/seedance_mode.go`。

---

## 4. 本次审计发现并修正的既有缺陷

| # | 缺陷 | 证据 | 修正 |
| --- | --- | --- | --- |
| 1 | `video-15s` SKILL.md 让 agent 调用 `beehive_submit_video` —— **运行时不存在该工具**（实际为 `beehive_submit_job`，见 `src/agent/tools.py`） | `grep` 工具注册表 | 改名为 manifest `binding.tool` 指向的真实工具；v0.2 `binding` 字段固化"工具↔node"契约，skill 正文不再硬编码工具名 |
| 2 | `video-15s` manifest 声明了 `license.file = LICENSE` 与 `i18n.glossary.*` 两个路径，**包内均不存在** | `tests/test_baseline.py` 旧断言甚至把 glossary 缺失当作预期 | 补齐 LICENSE（MIT）与双语术语表；新增测试断言"每个声明的引用必须解析" |
| 3 | A1 测试把"包内只有 SKILL.md + manifest.json"写死为预期 | 加文件后 3 项测试失败 | 改为按 skill_id 查找、按包内实际清单断言（不再与版本/文件数耦合） |
| 4 | 预载只读 `bundle.json`，清单里的 digest 无任何执行方（死元数据） | `store.preload_official_bundle` 原文 | 预载改读 `index.json` 并逐条校验 content_digest；双清单不一致直接拒绝 |

---

## 5. 验收证据（全部实跑）

```sh
# 1. 预载（真实安装路径，写 ~/.skilyst/store）
./bin/skilyst preload skills/official
# -> preloaded: skilyst/doctor 1.0.0, skilyst/embed-video 1.0.0,
#    skilyst/lipsync-audio-refs 1.0.0, skilyst/prompt-craft 1.0.0, skilyst/video-15s 1.1.0

# 2. 运行前校验（活集群节点注册表）
./bin/skilyst doctor skilyst/embed-video        # registry_available=true runnable=true
./bin/skilyst doctor skilyst/lipsync-audio-refs # registry_available=true runnable=true

# 3. 环境自检脚本（doctor skill 的可执行入口）
sh skills/official/doctor/scripts/doctor.sh skilyst/embed-video
# -> config=0 doctor=0 preflight=0 authz_probe=0，exit 0

# 4. 打包器自检 + 全量测试
python3 tools/pack_official_bundle.py --check   # OK: 5 skills, digests and both manifests agree
PYTHONPATH=src python3 -m unittest discover -s tests
# -> Ran 103 tests ... OK（基线 87 + T4 新增 16）
```

---

## 6. 已记录缺口（不掩盖，本 PR 不改运行时）

1. **多模态调用链未打通**：`beehive_submit_job` 目前只透传 `prompt` / `duration` /
   `resolution` / `ratio`。`embed-video` 与 `lipsync-audio-refs` 的绑定声明了
   `images` / `image_roles` / `audio_refs`，因此两 skill 的**端到端运行**需直接走
   Beehive jobs API，而非 agent 工具。两个 skill 正文已如实写明（"recorded gap, not
   a silent degradation"）。打通后即可用 `--dry-run` 验证调用链。
2. **平台签名是占位**：`supply_chain.signatures[].sig = platform-signature-pending`，
   只有 `digest`（= content_digest）是真实的。密钥仪式落地前不接受假签名。
3. **配额不可读**：见 §2 doctor 行；`/api/v1/billing/wallet` 的客户端门禁与服务端行为
   不一致属后端 scope 中间件缺口（M1 风险表 T3 项）。
4. **`node_definition_version` 语义待收敛**：活注册表暴露的是整数 `schema_version`（当前 27），
   而 v0.2 规范要求 semver range，故本包统一写 `>=1.0.0` + `compatibility.node_definitions.min`。
   规范与 core 字段对齐后需回填精确区间。

---

## 7. 纪律遵守

- skill 内容为**方法论提炼**，每条规则写明"为什么"及实测来源；深度材料引用
  `research/visual-language/` 路径而非全量复制。
- **凭据零入库**：包内不含任何密钥；`doctor` 只打印 `skilyst config` 的脱敏输出，
  脚本自身不读凭据文件。
- 改完未自行合并 PR，等待 evaluator 评审。
