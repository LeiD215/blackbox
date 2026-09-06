<!--
本文件也是补记性质：这是 2026-07-22 一次性整理出来的现状快照，
不是全程实时维护的 STATUS。
-->


# Blackbox vNext - STATUS

## Current vNext state (2026-09-06)

- vNext productization validation: **PASS** (Windows suite, Linux vm2, installed/no-Git F2, Slice2/4/5 guards, manifest coverage, CRLF fail-closed).
- Official Codex final migration candidate review: **REVIEW PASS** (round 5; evidence commit `9093ca7d823d6d1e2ffe59efc5ab49d204417565`, L1-L4 all PASS).
- Remote `vnext` migration branch: **CREATED** at `e344ef1f913112d661365a2ad3cb9ddb5dec1bf6` (tree `35f8fed246008a7009dae25b4bf0c094ee5a81e3`) via USER-DIRECT push; coordinator independently re-verified remote state matches.
- Default branch: still `main` (`011680e9cfda68e65010a4e402a269fa871ccf20`, tree `a049228cca4a9b75f77784fa56ae0ac646448548`). No default-branch switch has occurred.
- Legacy preservation: `main`, `legacy/v1`, `v1.0.0` (annotated tag, peeled to `011680e9...`) all preserved unchanged. 16 historical product paths preserved byte-for-byte from the v1 baseline.
- No v2 release tag created; no license added or decided; `pyproject.toml` version remains `2.0.0.dev0`.
- Branch availability vs formal release: presence of remote `vnext` is a reviewed branch artifact, not a published release. Public release remains blocked until license status is explicitly resolved by the USER.
- Next decision boundary (separate USER gates, NOT performed by this commit):
  - adoption / default-branch switch from `main` to `vnext`;
  - release/tag/license decision for v2.

## Historical context - 2026-07-22 snapshot of blackbox Skill (preserved)

The remainder of this file preserves the 2026-07-22 self-snapshot of the legacy blackbox Skill project as historical context. That snapshot predates the vNext migration work and is NOT current operational status. It is preserved byte-for-byte from the original 2026-07-22 content; do not interpret it as the present state of the `vnext` branch.

--- BEGIN 2026-07-22 legacy snapshot (preserved as historical context) ---

# 现状速览——blackbox Skill 项目本身

## 关键事实

```yaml
仓库地址: https://github.com/LeiD215/blackbox.git   # 建好仓库后替换成实际地址
项目内容: blackbox Skill 本体（SKILL.md + assets/ 七份模板）+ 项目自身的
  记录（STATUS.md/CHANGELOG.md/adr/）
前身: logbook.skill（已退休，见 adr/0001）
定稿日期: 2026-07-21～2026-07-22
当前版本状态: 已修复三次真实使用中发现的问题（write-through + 共享规范
  修改权限，见 adr/0002；交接文档可发现性，见 adr/0004；多agent并行
  协作场景补丁，见 adr/0005），2026-07-24 起纳入 git 版本控制
交付形式: git 仓库（此前是仅靠对话发送的 .skill 打包文件，没有独立、可
  核对的存放位置，这次迁移就是为了解决这个问题）
设计验证方式: 两轮设计层面的多AI外部审核 + 一次真实使用（hermes 维护
  override-rules 项目）+ 一轮针对真实问题的聚焦多AI咨询
```

## 项目状态

`进行中` —— 已从"设计定稿"进入"真实使用中持续修订"阶段，不再是一次性
交付就结束。

## 已知盲点

| 内容 | 状态 |
|---|---|
| blackbox 与项目专属 Skill（如 override-rules-fork-ops）的关系未理清 | 未补 |
| CHANGELOG 归档/裁剪机制只有原则方向，无具体触发时机定义 | 未补 |
| override-rules 项目此前用 logbook 记录，未迁移到 blackbox 格式 | 未补 |
| "骨架先行"机制尚未经过真实使用检验，agent 会不会误判"什么时候该用"未知 | 未补 |
| 结构化上报共享规范疑点这条路径，尚未被真实触发过 | 未补 |
| 本记录（CHANGELOG/ADR/STATUS）本身是补记重建的，非实时记录，精度仅到日期级 | 已知，非缺陷，见 CHANGELOG 开头声明 |
| "交接文档索引"规则（adr/0004）依赖 agent 自觉在交接后回头补 STATUS 索引，暂无技术强制手段，与 STATUS 软锁同类局限 | 已知，非缺陷 |
| "交接文档索引"规则本身尚未在更多真实项目中检验，字段是否够用（目前只有文件名/摘要/归档状态三项）未知 | 未补 |
| adr/0005 新增的多agent协作补丁（任务归属追踪表/协作协议/证据类型字段/框架自检等）尚未在多个真实项目中检验，目前只有一个下游真实使用场景 | 未补 |
| 归档硬触发的具体阈值数值（体量50~100KB/时间跨度7~14天）是本轮咨询建议区间，未经验证是否普适 | 未补 |

## 当前阶段

设计定稿后进入真实使用阶段，第一次真实使用（hermes/override-rules）就
发现并修复了一个核心机制问题（write-through），修复方案已交付新版本。

## 上次做了什么

（2026-08-13）下游真实使用项目单日内首次出现
两个具名 agent 并行协作，暴露 blackbox 当前设计未覆盖"多agent并行"
这类场景，9条真实证据（任务撞车/派发通道缺失/编号打架/验证盲区/
凭据落笔未脱敏/STATUS体量失控等）。协调者起草补丁初稿，用户提交8家
多家 AI 独立咨询，
综合分歧整合定稿，用户拍板同意后更新本体：SKILL.md新增会话收尾摘要
区块/CHARTER可选文件/CHANGELOG证据类型字段+两字段收紧/两类新毕业
文件(任务归属追踪表+协作协议)/框架自检一节(归档硬触发+漏记检测)/
凭据处理一节(此前空白)；新增3份模板文件；ADR-0005记录完整决策过程。

（2026-08-01）下游真实使用项目在一次 AI 会话
交接（限流期临时接管）中暴露规则14的可发现性缺陷——交接产生的卫星
文档记录详实但无法被新会话发现，直到第三方审核者偶然扫到才浮出水面。
新增"交接文档索引"毕业文件类别修复（SKILL.md + STATUS_TEMPLATE.md），
记录 ADR-0004 和对应 CHANGELOG 条目。

## 下一步待办

- [ ] 用户把新版 blackbox.skill 换掉 hermes 那边的旧版本（含 hermes 自己
      私改过的那份），避免版本分叉
- [ ] 观察"骨架先行"机制在后续真实使用中是否好用，是否需要按 ADR-0002
      的回退方案简化
- [ ] 观察结构化上报共享规范疑点这条路径第一次被真实触发时的实际效果
- [ ] 观察"交接文档索引"规则（ADR-0004）在更多真实项目中是否够用
- [ ] 视情况决定要不要把 override-rules 项目的记录迁移到 blackbox 格式
- [ ] 视情况理清 blackbox 与项目专属 Skill 的关系

--- END 2026-07-22 legacy snapshot ---
