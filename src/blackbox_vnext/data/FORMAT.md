# Blackbox vNext — Reference Implementation FORMAT.md (Slice 0)

> 权威 wire/profile/canonicalization 冻结（formal Spec v0.3.2 + Design v0.1.4）。
> 本 FORMAT 是 Slice 0 起所有实现的 ground truth；任何实现 MUST 从本 FORMAT 离线可恢复。

## F.1 wire token / lexical profile

| Token | Profile |
|---|---|
| `event_id` | `evt-` + 32 lowercase hex characters（128-bit no-coordination identity）；reference 实现由每个 writer 本地随机生成 |
| `content_hash` | `sha256:` + 64 lowercase hex characters |
| 字符串编码 | UTF-8，无 BOM |
| 整数 | signed safe-integer range `[-9007199254740991, 9007199254740991]`（IEEE-754 double 精确）；越界整数 → typed-extension 字符串表示 |
| 标量类型（参考 profile 拒绝集） | `true` / `false` / float / NaN / Infinity → ingest fail-closed |
| `null` | 仅在 `extensions` 内允许使用；core event schema MUST NOT 顶层/嵌套含 `null` 字段 |

## F.2 canonical JSON（RFC 8785 JCS-compatible 严格子集）

对完整 event 对象（`content_hash` key 除外）做**递归 canonicalization**：

1. **object property sorting**：按原始 property-name 的 **raw UTF-16 code-unit 字典序**（RFC 8785 规则，**非** code-point 排序）；每个嵌套层级都做。
2. **whitespace**：零额外空白；`:` 后无空格、`,` 后无空格；无缩进。
3. **string escaping**：JSON 最小转义 + 控制字符转义；其余 Unicode 直接 UTF-8。
4. **array semantics**：元素顺序保留（有序），参与 hash。
5. **numeric**：仅整数 + 浮点 reject；safe-integer 范围外 reject 或 typed-extension 字符串。

## F.3 input domain pre-schema 拒绝规则（frozen by Design v0.1.4 G1）

canonicalization 前 MUST 拒绝：

| 情况 | 拒绝 |
|---|---|
| duplicate object property names（同 object 内同 key 出现 ≥2 次） | reject |
| lone surrogate（`\uDEAD` 等未配对） / invalid Unicode | reject |
| boolean / float / NaN / Infinity | reject |
| 越界整数（> 9007199254740991 或 < -9007199254740991）作为 JSON integer | reject；可显式 typed-extension 字符串 |

> JSON Schema 本身**不能**完全检测 lone surrogate / duplicate-key —— 因此这两条作为 **pre-schema ingest constraints** 在 FORMAT 中固定。raw invalid fixtures（`fixtures/invalid/`）exercise 这些拒绝路径。

## F.4 hash input + stored bytes

- **hash input bytes** = canonical 序列化对象（**省略 `content_hash` key 后**）的 UTF-8 bytes；**不含** trailing LF。
- **stored event bytes** = canonical 序列化对象（含 `content_hash`）的 UTF-8 bytes + **恰好一个** trailing LF (`\n`)。
- 算法：**SHA-256 over exact canonical hash input bytes**；输出 = `sha256:<64-hex>`。

## F.5 unknown top-level field

仅 `extensions` namespace 允许扩展（参与 hash）。顶层 core schema 之外、未放入 `extensions` 的字段 → ingest **fail-closed**（events/ 不写）。

## F.6 标记（types 兼容集 + 拒绝）

每个 canonical event 的 `type / subtype / receipt_class` 组合**必须**在 `.blackbox/schema/subtypes.json` 的 `allowed_type` × `allowed_receipt_class` 矩阵中注册。否则：

- 未注册 subtype → **UNCLASSIFIED** / no-effect（不可参与 fold）
- 已注册 subtype + 非法 type/receipt_class 组合 → **invalid receipt**（不是 UNCLASSIFIED）

## F.7 prior_refs 结构

```json
"prior_refs": {
  "parent":    ["evt-..."],   // exclusive state parent(s)；参与 fork/conflict/fold
  "supports":  ["evt-...", "evt-..."]  // supporting/evidence refs；不创建 lifecycle fork
}
```

- 普通 state_transition：`parent` cardinality = `exactly_one`；若 `genesis_allowed=true` 可 zero。
- RESOLUTION：`parent_cardinality = one_or_more`（命名所有 conflicting heads）。
- `support` / `non_state`：永为 `supports` 引用；永不为 parent；永不创建 head。

## F.8 append-only UNCLASSIFIED → reclassify 路径 + 候选 replacement 机器规则（frozen by Design v0.1.4 G3 **+ Slice 0 final correction B1**）

- 原 UNCLASSIFIED event **immutable / no-effect 永远**。
- `CHANGE/reclassify` 是 non_state corrective record；以 `supports` 引用原 UNCLASSIFIED event id；
  `extensions.x_reclassifies` 记录原 event id；
  `extensions.x_replacement_event_id` 记录 **declared/expected** replacement id（**声明性**，不强制唯一）。
- **replacement event**（normal registered type/subtype）以 `supports` 引用 reclassify；
  `extensions.x_reclassified_from` 记录原 event id。

### F.8.1 Replacement candidate set — 机器规则（frozen by Slice 0 final correction B1）

给定 reclassify event R：

1. **Declared id**: R.`extensions.x_replacement_event_id` 是 **declared/expected** replacement id，**不是** exclusive time/file-order selector。

2. **Candidate predicate**（replacement event C for reclassify R）：
   - C 是 valid authorized event（即通过 §F.6 receipt validity）；
   - C.`prior_refs.supports` 包含 R.event_id；
   - C.`extensions.x_reclassified_from` == R.`extensions.x_reclassifies`（同一 original event id）。

3. **Candidate-set outcomes**（machine-deterministic）：
   - **0 valid/authorized candidates** → **unresolved / non-green**；reclassify 未被任何 replacement 覆盖；该 subject 当前保持 UNCLASSIFIED 历史。
   - **恰好 1 valid/authorized candidate C**, AND C.event_id == R.`extensions.x_replacement_event_id` → **unambiguous**；replacement = C；仅 C 参与 fold / effect eligibility。
   - **恰好 1 valid/authorized candidate C**, AND C.event_id ≠ R.`extensions.x_replacement_event_id`（declared missing/wrong） → **unresolved / non-green**；declared id 不匹配；不可 silent fallback；需要 explicit RESOLUTION 或 correction。
   - **≥ 2 valid/authorized candidates** → **ambiguous / conflict / non-green**；**no time/file-order winner**；任何与 declared id 匹配或否都不解除冲突；需 explicit RESOLUTION / correction 决定 effective candidate。

4. **No silent fallback rule**: 任何上述非绿色结果下，formal COMPLETE / ACCEPTED 等 derived state **不可**获得；formal upgrade blocked；待 Slice 1+ validator + RESOLUTION event 显式收敛。

5. **Slice 0 只 freeze bytes / expected contract**：上述判定规则的真实执行在 Slice 1+ 实现；Slice 0 通过 fixture 集合（l1a/b/c + 新 l1d/l1e）预演 candidate-set 枚举；**不**实现 fold / validator。

### F.8.2 Append-only 不变量

- 不原地变更原 UNCLASSIFIED event；replacement 通过独立 event_id + content_hash 参与 fold；
- 不回溯赋予原 event effect；
- 不伪造授权（reconciliation 也受此约束；见 F.9）。

### F.8.3 旧事件不重写（Slice 0 fixture 边界）

Slice 0 fixture corpus（l1a/l1b/l1c + l1d/l1e）是 test-profile material，**不**代表项目 governance history；
本节添加的 candidate-set 规则通过新 fixture（l1d + l1e）演示，**不** mutate 旧 l1a/l1b/l1c bytes；
任何后续 slice MUST NOT 改变历史 l1a/l1b/l1c 字节。

## F.9 reconciliation lifecycle（frozen by Design v0.1.4 G4）

- `reconciliation`：`genesis_allowed=true`，`parent_cardinality = at_most_one`。
- 当 subject 无 prior canonical head（孤儿无 head）：reconciliation `parent=[]`，创建 recorded head；必绑 observed post-state + observed_at + recorded_at + reason。
- 当 subject 已有 head：reconciliation `parent=[current head]`，normal conflict 规则。
- reconciliation **不** retroactively fabricate authorization/verification。

## F.10 observation profile（F4）

- portable `observation_profile.json`：governed-path / ignore 列表；`.blackbox/**` **固定** exclusion（不可覆盖）。
- baseline **pin profile identity/hash**；profile 变更 → baseline **STALE/INVALID**；缺失/损坏 → health **UNKNOWN**。
- reconciliation MUST bind **实际** observed post-state，不伪造先前 mutation 的授权。

## F.11 checkpoint / manifest profile（frozen by Design v0.1.4 I + E6）

- checkpoint id = `cp-<content-derived-id>`（content-derived，无全局排序）。
- checkpoint = `derived, non-authority`；首部标 `DERIVED / NON-AUTHORITY RECOVERY INDEX`。
- enumerable manifest：event-id + content-hash 列表（**可枚举**，非仅 Merkle root）。
- staleness 判定 = **event-set / manifest 集合差**（非 "canonical tail" 顺序）。
- checkpoint id 无治理/因果含义。

## F.12 历史模型（D）

canonical history = **event set + per-subject causal DAG**；MUST NOT 依赖 wall-clock total order 或 recorded_at 承担因果顺序。current state 按 subject `parent` 链 fold（从 genesis）。

## F.13 参考 / 命令名（仅设计；Slice 1+ 实现）

参考 CLI 命名 `bbx`（placeholder）。Slice 0 不实现 CLI；仅冻结 wire/format/fixtures。

## F.14 与 Formal Spec v0.3.2 + Design v0.1.4 的关系

- 任何与 Formal Spec v0.3.2 不可调和的矛盾 → 标 `SPEC-BLOCKER` 停设计层，不偷偷改规范。
- 不修改 v0.3.2 / Design v0.1.4；本 FORMAT 是它们的**实现冻结**。

## F.15 Slice 0 范围声明

本 FORMAT 在 Slice 0 中**只**冻结 wire/profile/fixture（schema/canonicalization），**不**包含 validator / projector / CLI / hooks 实现（属 Slice 1+）。
