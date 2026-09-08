# Blackbox v2.0.0 — 使用指南

[**English**](./USAGE.en.md) · **中文使用指南**

**适用范围。** 本指南说明如何端到端地*使用*已发布的 Blackbox v2.0.0 参考
运行时：安装、初始化项目、记录规范事件、理解派生状态、验证工作、checkpoint、
以及运行发布前检查门禁（pre-release gate）。本指南面向具备技术能力的用户，
而不是面向实现该协议的人。确切的线上格式与规范化规则见 `.blackbox/FORMAT`
（随产品冻结）以及 `schemas/` 中的 schema。运营契约与设计原理见
[`SKILL.md`](./SKILL.md) 和 [`PRODUCT-RUNTIME.md`](./PRODUCT-RUNTIME.md)。

> 如果你是新手，请先读这里：第 1–5 节给出模型和完整演练。第 6–12 节解释
> 每个概念与失效模式。第 15 节是紧凑的命令索引。

---

## 1. Blackbox 是什么——以及不是什么

Blackbox v2.0.0（vNext）是一种**可移植的项目记录与溯源机制**（portable
project recording and provenance mechanism）。它为项目维护一份规范化、
只追加（append-only）的正式记录（“事件”）历史，通过折叠（folding）从该
历史推导当前状态，并在历史未知、损坏、过期、冲突或未授权时失败关闭
（fail closed）。

定义该产品的两个不变量：

- **声明不是事实（Claim Is Not Fact）。** 执行者的声明——包括自我验证
  （self-verification）——无法自行升级为独立事实。缺少合格规范凭证
  （canonical receipt）支撑的受治理效果声明，属于孤儿（orphan）/ 非绿色
  （non-green）状态。
- **未知从不继承绿色（Unknown Never Inherits Green）。** `UNKNOWN`、
  `CONFLICT`、`STALE` 以及非独立证据永远不会产生绿色派生状态。

Blackbox **不是**：

- IAM / 授权系统（它记录并推导状态；它不强制执行组织权威）；
- 任务调度器或工作流引擎（它记录派发与完成）；
- Git 的替代品（核心操作不要求 Git）；
- 散文的“第二个事实源”（Markdown、STATUS、交接文档都是上下文或投影，
  永远不是第二个可写的规范化事实源）。

---

## 2. 安装

该包**未发布到 PyPI**。受支持的安装方式是从仓库安装。需要 **Python ≥ 3.11**；
运行时没有任何第三方依赖。

从仓库克隆：

```text
git clone https://github.com/LeiD215/blackbox.git
cd blackbox            # default branch vnext (v2.0.0)
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install .
blackbox-vnext --help
```

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install .
blackbox-vnext --help
```

控制台入口点是 `blackbox-vnext`。你也可以在已安装该包的环境中通过
`python -m blackbox_vnext` 运行同一套 CLI。

验证安装：

```powershell
blackbox-vnext --help        # prints the top-level command list
python -m pip show blackbox-vnext   # Version: 2.0.0
```

> 如果你是从源码检出安装的，事件/观测 schema、子类型注册表（subtype
> registry）和观测画像（observation profile）都是受治理的冻结字节；它们
> 会在 `init` 时（第 3 节）被复制到每个项目里，任何漂移都会被检测出来并
> 阻止绿色状态。

---

## 3. 初始化项目

选择一个项目目录（可以是空目录，也不一定要是 Git 仓库）。

```powershell
mkdir C:\work\myproject
blackbox-vnext init --root C:\work\myproject
```

输出：

```text
INIT_OK: C:\work\myproject\.blackbox
```

`init` 会创建一个可移植的项目本地记录区 `.blackbox/`，包含：

| 路径 | 是什么 |
|---|---|
| `.blackbox/events/` | 规范事件文件，每个被接受的事件一个文件（`evt-<id>.json`）——持久记录 |
| `.blackbox/schema/` | 受治理的 schema / 子类型注册表 / 观测画像（冻结、机器校验） |
| `.blackbox/authority/bootstrap.json` | 引导权威画像（哪个主体可以写什么） |
| `.blackbox/FORMAT` | 冻结的线上/规范化规范（`src/blackbox_vnext/data/FORMAT.md` 的副本） |
| `.blackbox/checkpoints/`、`.blackbox/projections/`、`.blackbox/recovery/` | 运行时区（init 时创建） |

`init` 是幂等的（再次运行也会成功）。如果已有项目里的受治理资源与包内
字节不一致，`init` 会以 `GOVERNED-RESOURCE-DRIFT` 拒绝，而不是静默重写它们。

> 使用 `status`/`observe` 之前先运行 `init`：`write` 会在目录缺失时创建
> events 目录，但没有 `init` 就没有观测画像和 schema，`status`/`observe`
> 会以 `sensor UNKNOWN: observation profile missing` 失败关闭。

---

## 4. 核心命令快速上手

除 `validate-receipt`（它接收一个凭证文件路径）外，所有命令都带
`--root <project>`（默认：当前目录）。

| 当你想要…… | 使用 |
|---|---|
| 记录一个规范事件（派发、结果、完成……） | `blackbox-vnext write <event.json> --root <project>` |
| 记录执行者自我验证作为规范支撑记录 | `blackbox-vnext self-verify --root <project> --event <event.json>` |
| 重新摄取整个事件语料并检查每个文件 | `blackbox-vnext validate --root <project>` |
| 把工作区与已确认的基线（baseline）比较 | `blackbox-vnext observe --root <project>` |
| 确认一份干净的新基线 / 恢复丢失的基线 | `observe --ack` / `observe --rebaseline` |
| 推导当前每个主体（subject）的状态（T3 + fold） | `blackbox-vnext status --root <project>` |
| 写一份派生的、非权威的恢复索引 | `blackbox-vnext checkpoint --root <project>` |
| 中断后 / 跨会话交接时重新推导状态 | `blackbox-vnext resume --root <project>` |
| 为某个工件生成独立验证凭证 | `blackbox-vnext independent-verify …` |
| 校验独立验证凭证文件 | `blackbox-vnext validate-receipt <receipt.json>` |
| 运行发布前/验收门禁 | `blackbox-vnext pre-release-check --root <project> --requirements <reqs.json> …` |

每个命令的细节与确切语法见下面各节。

---

## 5. 第一个端到端示例

这个示例刻意做得很小但完整：它授权一个任务、记录执行与完成、自我验证、
确认观测基线，并推导出 `COMPLETE`。下面所有 JSON 都已对照 v2.0.0 实现
验证过。

### 5.1 准备项目与一个工作区工件

```powershell
mkdir C:\work\demo ; cd C:\work\demo
blackbox-vnext init --root .
New-Item -ItemType Directory -Force -Path .\docs
Set-Content -Path .\docs\release-notes.md -Value "# v2.0.0 release notes" -Encoding UTF8
```

### 5.2 派发——人类授权一个任务

`dispatch.json`（`TASK / dispatch / claim`，actor `human:owner`）：

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-dddddddddddddddddddddddddddddddd",
  "content_hash": "sha256:efb47f0713b3b43a8237b4e28272739c40a138241d8848af9fc0c662e7944a83",
  "actor": "human:owner",
  "type": "TASK",
  "subtype": "dispatch",
  "receipt_class": "claim",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:00:00Z",
  "prior_refs": { "parent": [], "supports": [] },
  "extensions": { "dispatch": { "assignee": "executor:worker", "surface": "project:reference" } },
  "body": "Authorize writing v2.0.0 release notes"
}
```

`content_hash` 是怎么算出来的？它是 SHA-256（RFC 8785 规范化 JSON，作用于
**去掉** `content_hash` 键之后的事件对象）——见 `.blackbox/FORMAT` 的
F.4 节。用 Python：

```python
from blackbox_vnext.canonical import sha256_canonical
event = {
  "schema_version": "0.3.2",
  "event_id": "evt-dddddddddddddddddddddddddddddddd",
  "actor": "human:owner",
  "type": "TASK",
  "subtype": "dispatch",
  "receipt_class": "claim",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:00:00Z",
  "prior_refs": {"parent": [], "supports": []},
  "extensions": {"dispatch": {"assignee": "executor:worker", "surface": "project:reference"}},
  "body": "Authorize writing v2.0.0 release notes",
}
print(sha256_canonical(event))   # sha256:efb47f0713b3b43a8237b4e28272739c40a138241d8848af9fc0c662e7944a83
```

接受它：

```powershell
blackbox-vnext write .\dispatch.json --root .
# WRITE_OK: event_id=evt-dddddddddddddddddddddddddddddddd content_hash=sha256:efb47f07...
```

### 5.3 执行结果——执行者记录效果

`execution-result.json`（`TASK / execution-result / claim`，actor
`executor:worker`，parent = 上面的派发事件）：

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "content_hash": "sha256:f4fd67458633084b36a34dbaf10e8994bbf64cc58833fa76a0200f9b15e3070a",
  "actor": "executor:worker",
  "type": "TASK",
  "subtype": "execution-result",
  "receipt_class": "claim",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:05:00Z",
  "prior_refs": { "parent": ["evt-dddddddddddddddddddddddddddddddd"], "supports": [] },
  "extensions": { "effect": [ { "change": "added", "path": "docs/release-notes.md", "post_identity": "sha256:0000000000000000000000000000000000000000000000000000000000000000" } ] },
  "body": "Drafted v2.0.0 release notes"
}
```

```powershell
blackbox-vnext write .\execution-result.json --root .
```

### 5.4 完成声明

`completion-claim.json`（`TASK / completion-claim / claim`，同一 subject，
parent = 执行结果）：

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-ffffffffffffffffffffffffffffffff",
  "content_hash": "sha256:c84868bf1add73cf77552fd6d43437a20efff84c692d047c58fb970ab7aa4d83",
  "actor": "executor:worker",
  "type": "TASK",
  "subtype": "completion-claim",
  "receipt_class": "claim",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:10:00Z",
  "prior_refs": { "parent": ["evt-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"], "supports": [] },
  "extensions": { "effect": [ { "change": "added", "path": "docs/release-notes.md", "post_identity": "sha256:0000000000000000000000000000000000000000000000000000000000000000" } ] },
  "body": "v2.0.0 release notes complete"
}
```

```powershell
blackbox-vnext write .\completion-claim.json --root .
```

### 5.5 自我验证

`self-verify.json`（`VERIFICATION / self-verification / self-verification`，
actor `executor:worker`，`supports` = 完成声明）：

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-99999999999999999999999999999999",
  "content_hash": "sha256:1ef7b4f559798acf402b63b96da9d210752dbedb46162ec5960497e4aaaa6b0c",
  "actor": "executor:worker",
  "type": "VERIFICATION",
  "subtype": "self-verification",
  "receipt_class": "self-verification",
  "subject": "task:release-notes",
  "recorded_at": "2026-09-07T00:12:00Z",
  "prior_refs": { "parent": [], "supports": ["evt-ffffffffffffffffffffffffffffffff"] },
  "extensions": {},
  "body": "Self-verified release notes content"
}
```

```powershell
blackbox-vnext self-verify --root . --event .\self-verify.json
# WRITE_OK: event_id=evt-99999999999999999999999999999999 content_hash=sha256:1ef7b4f5...
```

### 5.6 验证、确认基线并读取状态

```powershell
blackbox-vnext validate --root .
# VALID: ...\events\evt-9999...  evt-99999999999999999999999999999999
# VALID: ...\events\evt-dddd...  evt-dddddddddddddddddddddddddddddddd
# VALID: ...\events\evt-eeee...  evt-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
# VALID: ...\events\evt-ffff...  evt-ffffffffffffffffffffffffffffffff
# corpus: VALID=4  UNCLASSIFIED=0  INVALID=0  UNKNOWN=0

blackbox-vnext observe --root . --ack
# sensor UNKNOWN: no acknowledged baseline (run observe --ack first)
# corpus: VALID=4  UNCLASSIFIED=0  INVALID=0  UNKNOWN=0
# BASELINE_ACKNOWLEDGED: profile_identity=sha256:ffcc14e20126c2ccd1e2f846a606fc20e6aadf3a25f3c64d7170ae4fb4282d7f paths=0

blackbox-vnext status --root .
# corpus: VALID=4  UNCLASSIFIED=0  INVALID=0  UNKNOWN=0
# subject=task:release-notes  head=evt-ffffffffffffffffffffffffffffffff  conflicts=0
#   derived_complete=ok (derived COMPLETE; head=evt-ffff...; VERIFIED-SELF=evt-9999...)
```

`derived_complete=ok` 就是绿色状态：该 subject 的历史可以折叠（无冲突），
head 是一个完成声明，并且有一条有效的自我验证支撑它。注意这是**派生**的：
没有任何事件声称“COMPLETE”——是 CLI 从规范历史中推导出来的。

### 5.7 Checkpoint 与 resume

```powershell
blackbox-vnext checkpoint --root .
# {"checkpoint_id": "cp-e3f73547d2805a0282a964b1b6197ae4d46bba08fb7bde5c2286c2afaedc60ab", "ok": true, "out": "...\.blackbox\checkpoint.json"}

blackbox-vnext resume --root .
# same output as status (resume is an alias for the status command)
```

---

## 6. 声明、证据与验证

- **声明（Claim）。** 由某个主体（principal）创作的任何规范事件。声明是
  派发、执行结果、完成、接受、变更和观测进入记录的方式。声明是*断言*，
  不是事实。
- **证据（Evidence）。** 机器可读的效果绑定位于 `extensions.effect`
  （`path` + `post_identity`），支撑引用位于 `prior_refs.supports`。
  凭证（独立验证事件）把观测到的工件身份绑定到它们所佐证的需求
  （requirement）上。
- **自我验证（self-verification）**（`VERIFICATION /
  self-verification`）是执行者自己的支撑记录。它可以为它所支撑的任务的
  派生 `COMPLETE` 状态做出贡献，但永远不能声称独立性。
- **独立验证（independent verification）** 是由非执行者的验证者、在独立
  来源上、在有限期限内执行的读回（readback）；它产生一个
  `independent-verification` 凭证（第 11 节）。只有它能满足发布前门禁
  （第 10 节）。

为什么执行者报告 PASS 还不够：系统把该报告当作一条声明。
`pre-release-check` 和 `status` 只信任规范历史和合格凭证所确立的事实——
永远不信任报告里的措辞。

---

## 7. 状态 / 派生状态解读

`status` 先执行 T3（严格语料重摄取 + 传感器新鲜度），有任何未知/损坏即
失败关闭，然后对每个 subject 做折叠。

可观察结果（全部对照 v2.0.0 验证过）：

| 输出 | 含义 | 典型原因 / 下一步 |
|---|---|---|
| `corpus: VALID=N ...` | 严格重摄取摘要 | 健康语料：VALID 计数，INVALID/UNKNOWN 为零 |
| `derived_complete=ok (derived COMPLETE; head=…; VERIFIED-SELF=…)` | 某 subject 的绿色状态 | 继续 / 交接 |
| `T3_BLOCKED: fail-closed; no green state can be derived` | 某个门禁挡住了整个项目 | 阅读其下列出的 blockers |
| `sensor UNKNOWN: no acknowledged baseline (run observe --ack first)` | 观测画像存在但没有基线 | 在已调和（reconciled）的工作区上运行 `observe --ack` |
| `sensor UNKNOWN: observation profile missing` | 项目没有 `init`（或画像被删除） | 运行 `init` |
| `INVALID: <file> content_hash mismatch …` | 存储的事件被动过或损坏 | 从 checkpoint/备份恢复；不要手工编辑规范文件 |
| `ORPHAN: <path> category=ORPHAN` | 工作区文件发生变化但没有匹配的凭证绑定 | 调和（记录这次变更）或还原该文件 |
| `subject-blocker: <subject> <reason>` | 单个 subject 的治理阻塞（如 `UNCLASSIFIED receipt …`、`UNAUTHORIZED receipt …`） | 按原因解决，或记录一条纠正事件 |

需要记住的规则：

- `UNKNOWN`、`STALE`、`CONFLICT`、`ORPHAN` 以及非独立证据永远不继承绿色。
- 已注册但未授权的凭证是显式阻塞项（它不是未分类事件）。
- 未注册的子类型是 `UNCLASSIFIED` / 无效果的历史证据，直到只追加的
  `reclassify` + 替换路径解决它。

---

## 8. 观测 / 漂移用途

`observe` 把新鲜的工作区重算与持久化的已确认基线进行比较，使用**观测画像**
（`.blackbox/schema/observation-profile.json`，`init` 时种入；`.blackbox/**`
是固定排除项，不能被覆盖）。

```powershell
blackbox-vnext observe --root <project>            # report current delta
blackbox-vnext observe --root <project> --ack      # acknowledge a clean fresh baseline
blackbox-vnext observe --root <project> --rebaseline  # explicit recovery of corrupt/stale/lost baseline
```

- 在新项目里第一步是 `observe --ack`；在那之前 status 是 `sensor UNKNOWN`。
- 任何没有被匹配凭证绑定覆盖的工作区变化都会被报告为 `ORPHAN`
  （未被覆盖的变更）。随后 `status` 以非零退出，`--ack` 会被**拒绝**——
  确认会让孤儿静默合法化。先调和（为该变更记录凭证/效果绑定）或还原文件，
  然后再 ack。
- `--rebaseline` 只会在工作区已经调和之后覆盖损坏/过期/丢失的基线；存在
  未被覆盖的变更时它会拒绝。
- 如果观测画像本身变了，基线会变成 `STALE`/`INVALID`，必须显式恢复。

---

## 9. Checkpoint 与 resume

- **Checkpoint。** 运行 `blackbox-vnext checkpoint --root <project>` 写一份
  派生的、非权威的恢复索引（默认 `.blackbox/checkpoint.json`）：已接受事件
  的可枚举清单（事件 id + content hash）、每个 subject 的前沿
  （frontier）、以及一个内容派生的 `checkpoint_id`。它是恢复辅助，**不是**
  授权来源，也**不是**第二份记录。
- **Resume。** `resume` 是 `status` 的别名——它重新运行 T3 并重新推导当前
  状态。中断后或从另一个会话回来时使用它。它不会恢复文件或重新应用工作；
  它从规范历史和已确认的基线重新推导状态（如果离开期间基线丢失了，用
  `observe --rebaseline` 恢复）。
- 频繁 checkpoint 是一份廉价保险；`resume`（或 `status`）是跨会话的
  “我们在哪”命令。

---

## 10. 发布前检查

`pre-release-check` 是**门禁**，不是发布器：它从规范需求和证据凭证推导
资格判定，并且永远不会创建接受凭证（acceptance receipt）。

```text
blackbox-vnext pre-release-check --root <project> --requirements <reqs.json>
    [--evidence <receipt.json> ...] [--dispatch-event-id <evt-id> ...]
```

- `--requirements` 是一个需求对象的 JSON**数组**（字段：`effect_id`、
  `scope`、`target`、`subject`、`artifact_identity`、`input_scope`、
  `expected_identity`、`expected_algorithm`、`executor`、`claim_source`、
  `required_read_path`、`required_source_id`、`required_verifier`、
  `effect_class`、`prior_authorized`、`lifecycle_ready`、
  `completion_claimed`）。
- 高风险效果类别（`production`、`external`、`irreversible`、`high-risk`、
  `security`、`authority-boundary`、`release`、`acceptance`）需要**事先
  授权**：为已记录在该项目中、且 subject/scope/assignee 与需求匹配的绿色
  `TASK/dispatch` 事件传入 `--dispatch-event-id`。
- `--evidence` 接收规范的**独立验证凭证事件文件**（由 `independent-verify`
  产生，第 11 节）。只有凭证匹配需求的完整身份画像时，需求才通过。

具体示例（继续演示项目，为发布工件把关）：

```powershell
# artifact to be released
Set-Content -Path .\release.bin -Value "green" -NoNewline -Encoding Ascii
$hash = (Get-FileHash -Algorithm SHA256 .\release.bin).Hash.ToLower()
$now  = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$obs  = [DateTime]::UtcNow.AddSeconds(-30).ToString("yyyy-MM-ddTHH:mm:ssZ")

blackbox-vnext independent-verify --root . `
  --read-root . --read-path release.bin `
  --target release.bin --subject task:release-notes `
  --artifact-identity artifact:release-notes --input-scope project:reference `
  --expected-identity "sha256:$hash" --expected-algorithm sha256 `
  --executor executor:worker --claim-source workspace:executor `
  --verifier agent:reviewer --source-id filesystem:reviewer `
  --event-id evt-c1000000000000000000000000000001 `
  --observed-at $obs --now $now --recorded-at $now --max-age-seconds 300 `
  | Set-Content .\receipt.json
```

`requirements.json`：

```json
[
  {
    "effect_id": "effect:release-notes",
    "scope": "project:reference",
    "target": "release.bin",
    "subject": "task:release-notes",
    "artifact_identity": "artifact:release-notes",
    "input_scope": "project:reference",
    "expected_identity": "sha256:<hash of release.bin>",
    "expected_algorithm": "sha256",
    "executor": "executor:worker",
    "claim_source": "workspace:executor",
    "required_read_path": "file://fixture-root/release.bin",
    "required_source_id": "filesystem:reviewer",
    "required_verifier": "agent:reviewer",
    "effect_class": "release",
    "prior_authorized": true,
    "lifecycle_ready": true,
    "completion_claimed": true
  }
]
```

```powershell
blackbox-vnext pre-release-check --root . `
  --requirements .\requirements.json `
  --evidence .\receipt.json `
  --dispatch-event-id evt-dddddddddddddddddddddddddddddddd
```

通过的结果（已验证）：

```json
{"affected_effects": ["effect:release-notes"], "blockers": [],
 "creates_acceptance_receipt": false, "derived_non_authority": true,
 "result_id": "sha256:315d07ae5cd0a651280b043961ef808bf4e981ff73fc5110e293aba6670111f4",
 "verdict": "PASS"}
```

说明：

- 证据中凭证的规范化读路径（normalized read path）是
  `file://fixture-root/<relative-path>`；需求的 `required_read_path` 必须
  与它完全一致（见第 11 节和 `validate-receipt`）。
- 缺少派发授权会产生 `AUTHORIZATION-MISSING`；凭证不匹配会产生
  `ASSURANCE-GAP`（两者都已验证）。
- `derived_non_authority: true` 和 `creates_acceptance_receipt: false`
  提醒你：门禁只推导资格——它不执行也不记录发布。

---

## 11. 凭证校验

`validate-receipt` 检查一份规范的 `independent-verification` 事件文件
（信封、content hash、判别符、扩展画像）并报告有效性：

```powershell
blackbox-vnext validate-receipt .\receipt.json
# {"reason": "OK", "valid": true}
```

收到或构造独立验证凭证时都要用它：在把它喂给 `pre-release-check` 之前，
它先确认信封是真的。凭证事件本身由 `independent-verify` 以这种形态产生
（已裁剪）：

```json
{
  "schema_version": "0.3.2",
  "event_id": "evt-…",
  "actor": "agent:reviewer",
  "type": "VERIFICATION",
  "subtype": "independent-verification",
  "receipt_class": "independent-verification",
  "subject": "task:release-notes",
  "recorded_at": "…",
  "prior_refs": { "parent": [], "supports": [] },
  "extensions": {
    "independent_verification": {
      "algorithm": "sha256",
      "observed_identity": "sha256:…",
      "expected_identity": "sha256:…",
      "result": "PASS",
      "reason": "EXACT-INDEPENDENT-MATCH",
      "verifier": "agent:reviewer",
      "executor": "executor:worker",
      "independent_source": true,
      "transport_ok": true,
      "read_path": "file://fixture-root/release.bin",
      "source_id": "filesystem:reviewer",
      "observed_at": "…",
      "provenance": "local-read-only-filesystem",
      "evidence_digest": "sha256:…"
    }
  }
}
```

凭证也会作为规范事件被重新接受进语料
（`VERIFICATION / independent-verification`），因此它们参与记录，同时
永远不能伪造独立性。

---

## 12. 恢复 / 常见失效模式

| 症状（已验证） | 原因 | 怎么办 |
|---|---|---|
| `WRITE_REJECT: JSON parse error: Expecting value at col 1` | 事件文件不是合法 JSON | 修正文件，再写一次 |
| `WRITE_REJECT: event shape invalid (missing required fields or prior_refs shape)` | 事件缺少必需的信封字段 | 使用 schema（`schemas/event.schema.json`） |
| `WRITE_REJECT: content_hash mismatch: stored content_hash != sha256(canonical(obj - content_hash))` | `content_hash` 错误或缺失 | 用 `blackbox_vnext.canonical.sha256_canonical` 重算 |
| `validate` 列出 `INVALID` / `status` 打印 `T3_BLOCKED` 且带 `INVALID: … content_hash mismatch` | 存储的事件文件被编辑/篡改 | 从 checkpoint/备份恢复文件；规范事件文件不得手工编辑 |
| `sensor UNKNOWN: no acknowledged baseline (run observe --ack first)` | 基线尚未确认 | 运行 `observe --ack` |
| `sensor UNKNOWN: observation profile missing` | 项目未初始化 | 运行 `blackbox-vnext init --root .` |
| `ORPHAN: <path> category=ORPHAN`；`ACK_REJECTED: non-COVERED workspace changes present` | 工作区变更没有凭证绑定 | 记录该变更（或还原它），然后 ack |
| `ACK_REJECTED: baseline is STALE/LOST…` | 基线损坏/过期/丢失 | 调和工作区，然后 `observe --rebaseline` |
| pre-release-check 里出现 `AUTHORIZATION-MISSING` | 高风险需求没有匹配的绿色派发 | 记录派发并传 `--dispatch-event-id` |
| `ASSURANCE-GAP: exact independent evidence missing` | 没有匹配的独立凭证 | 产生/验证凭证并通过 `--evidence` 传入 |
| 没有 Git 仓库、没有网络 | 核心命令不要求 Git | 无需处理；Blackbox 直接操作项目目录 |

通用恢复规则：

- 永远不要手工编辑 `.blackbox/events/` 下的规范事件文件；用只追加记录
  （如 `CHANGE/correction` 或 reclassify/替换路径）纠正历史，绝不通过改写
  过去来纠正。
- 备份 `.blackbox/`（或依赖 `checkpoint` + 你的常规备份），这样损坏的文件
  可以被恢复。
- 如果某个 subject 的链冲突（例如一个 head 有两个子记录），用显式列出所有
  冲突 head 的 `CHANGE/resolution` 解决——系统绝不会静默选择时间/文件
  顺序上的赢家。

---

## 13. 目录 / 文件解剖

规范（持久、不得手工编辑）：

```text
<project>/
└── .blackbox/
    ├── events/                       # canonical events: evt-<id>.json (append-only)
    ├── authority/bootstrap.json      # bootstrap authority profile (governed)
    ├── schema/                       # governed schema/subtype/profile bytes
    │   ├── event.schema.json
    │   ├── checkpoint.schema.json
    │   ├── manifest.schema.json
    │   ├── observation-profile.json
    │   ├── observation-profile.schema.json
    │   └── subtypes.json
    └── FORMAT                        # frozen format spec
```

派生 / 运行时（可重建、非权威）：

```text
<project>/.blackbox/
    ├── checkpoint.json               # after checkpoint: derived recovery index
    ├── checkpoints/                  # checkpoint runtime area
    ├── projections/                  # projection runtime area
    └── recovery/                     # recovery runtime area
```

`.blackbox/**` 下的所有内容都被固定排除规则排除在观测传感器之外；它是
实现的记录区，不是受治理的工作区路径。

---

## 14. 推荐操作模式

Blackbox 记录并证明；它不自行决定组织权威。一个最小的编排者集成：

```text
dispatch ──► execution claim ──► evidence ──► verification ──► status / gate
owner        executor            effect        self-verify /   derive COMPLETE /
TASK/        TASK/               bindings      independent-    pre-release-
dispatch     execution-result                 verify          check
```

具体来说：

1. 负责人记录一条 `TASK/dispatch` 事件，授权被指派人。
2. 执行者在其变更受治理路径时，记录带 `extensions.effect` 绑定的
   `TASK/execution-result` 事件。
3. 执行者完成时记录 `TASK/completion-claim`，并记录一条
   `VERIFICATION/self-verification` 支撑记录。
4. 对于必须独立证明的事项（发布级效果），独立验证者运行
   `independent-verify` 产生凭证。
5. `status` 推导每个 subject 的状态；`pre-release-check` 推导门禁。
6. 只有绿色派生状态（带合格证据）才算完成——执行者或验证者的报告本身
   永远不是权威。

---

## 15. 命令参考表

| 命令 | 用途 | 典型输入 | 典型输出 / 效果 |
|---|---|---|---|
| `init --root <dir>` | 创建可移植记录区 | 项目目录 | `INIT_OK`，种入 `.blackbox/`、受治理资源 |
| `write <event.json> --root <dir>` | 接受一个规范事件 | 规范事件 JSON 的路径 | `WRITE_OK`（+ `event_id`、`content_hash`），存储 `events/evt-<id>.json`；否则拒绝 |
| `self-verify --root <dir> --event <f>` | 记录执行者自我验证 | 规范的 `VERIFICATION/self-verification` JSON | `WRITE_OK`；不产生独立凭证 / 绿色门禁 |
| `validate --root <dir>` | 严格重摄取语料 | 项目根 | 逐文件 `VALID/UNCLASSIFIED/INVALID/UNKNOWN` + `corpus:` 摘要；有阻塞项时退出码 2 |
| `status --root <dir>` | T3 + 折叠派生状态 | 项目根 | 逐 subject 的 head/冲突/`derived_complete`；失败关闭时退出码 2 |
| `resume --root <dir>` | status 的别名 | 项目根 | 与 `status` 相同 |
| `observe --root <dir> [--ack\|--rebaseline]` | 工作区 vs 基线的增量 | 项目根 | `ORPHAN`/覆盖行；`BASELINE_ACKNOWLEDGED` / `BASELINE_RECOVERED`；存在未覆盖变更时拒绝 ACK/RECOVER |
| `checkpoint --root <dir> [--out <f>]` | 派生恢复索引 | 项目根 | `{"ok": true, "checkpoint_id": "cp-…", "out": …}` |
| `independent-verify --root <dir> …` | 独立读回凭证 | 绑定 + 读根/路径 + 身份 + RFC3339 时间 | 在 stdout 输出规范凭证事件；未独立验证时退出码 2 |
| `validate-receipt <f>` | 校验凭证事件 | 凭证 JSON 文件 | `{"valid": true, "reason": "OK"}`，否则退出码 2 |
| `pre-release-check --root <dir> --requirements <f> [--evidence <f>…] [--dispatch-event-id <id>…]` | 资格门禁 | 需求 JSON 数组 + 凭证文件 | `{"verdict": "PASS"}` 或 `NON-GREEN` + blockers；NON-GREEN 时退出码 2 |

运行 `blackbox-vnext <command> --help` 查看权威选项列表；本表是方便索引，
不是替代品。

---

## 16. 安全 / 诚实边界

- **声明不是事实（Claim Is Not Fact）**：声明（包括自我验证）不会仅仅因为
  执行者报告 PASS 就变得可信。
- **未知从不继承绿色（Unknown Never Inherits Green）**：`UNKNOWN` /
  `STALE` / `CONFLICT` / `ORPHAN` 以及非独立证据永远不会产生绿色派生状态。
- Blackbox 不会声称证明它没有独立验证过的外部事实；
  `independent-verification` 是唯一可以贡献独立证据的路径，它永远不可能被
  语料伪造。
- 除已实现的溯源语义外，Blackbox 不提供法律、安全或 IAM 权威：它记录授权
  并推导资格；它不强制执行它们。
- 本指南只描述已发布的实现。如果文档与实现不一致，以实现及其测试为准；
  报告此类不一致，而不是猜测。
