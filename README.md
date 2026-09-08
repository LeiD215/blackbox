# Blackbox v2.0.0

[**English**](./README.en.md) · **使用指南：[USAGE.md](./USAGE.md)** · **English usage guide: [USAGE.en.md](./USAGE.en.md)**

Blackbox 是一套项目记录与溯源机制（project recording and provenance
mechanism）。v2.0.0（vNext）通过规范化的规范事件与凭证历史（canonical
event and receipt history）让正式的治理记录变得可靠；它不是通用的 IAM 或
工作流产品。

## 快速开始

完整的端到端使用指南见 **[USAGE.md](./USAGE.md)**（安装、项目初始化、
记录/声明/验证工作流、checkpoint/resume、凭证校验、以及发布前检查门禁）。
本 README 其余部分是产品定位与仓库现状的摘要。

## vNext 操作方式

运行 `blackbox-vnext init --root <project>` 创建可移植的项目本地记录区
`.blackbox/`。正式的治理变更必须使用其中的规范记录，并遵循校验器/投影
（validator/projector）语义。`write` 在接收记录前会同步校验；
`validate`、`status`、`checkpoint`、`observe`、`resume`、`self-verify`、
`independent-verify`、`pre-release-check` 都作用于同一个被选中的项目根。
核心操作不依赖 Git。

Markdown 只是人类上下文、投影或冻结的遗留材料，永远不是第二个可写的
规范事实源。当前状态不存在长期双写。已有项目需要显式切换：
vNext 不会把遗留散文解析成经过验证的类型化历史。

## 兼容性与切换

v1 产品原样保留在 `v1.0.0` 和 `legacy/v1`。保留的 v1 风格工作流仅是
**legacy v1 模式**，不提供 vNext 的治理或保证语义。完整切换矩阵见本树
中的 `V1-TO-VNEXT-COMPATIBILITY.md`。

## 分支、发布、版本与许可证状态

- `main`、`legacy/v1`、`v1.0.0` 作为遗留基线原样保留
  （commit `011680e9cfda68e65010a4e402a269fa871ccf20`）。
- `vnext` 是仓库默认分支（可变、持续演进的开发/当前分支；
  静态文档不固化其活 tip 的 SHA/tree）。
- Git 标签 `v2.0.0` 已存在并指向同一 commit
  （`f26db56d0cabdfae900a3befc222547a23c3d909`，
  tree `69b582239920c71f7d0588a38d6b0d1658842abf`）；GitHub Release
  `v2.0.0` 已发布：
  <https://github.com/LeiD215/blackbox/releases/tag/v2.0.0>。
- `pyproject.toml` 版本为 `2.0.0`。该包**未**发布到 PyPI；请从仓库安装
  （见 [USAGE.md](./USAGE.md#installation)）。
- 根目录 `LICENSE` 提供 Blackbox Community License 1.0（BCL 1.0）。
  许可证定位为 Source Available / Community License，而非 OSI Open Source。
  BCL 1.0 的合格法律审查尚未进行；按用户决定推迟且不阻塞发布。

## 这个 `vnext` 分支是什么、不是什么

该分支是仓库默认分支，是 v2.0.0 不可变发布快照的经过评审的接续演进；
它可以在不改变 v2.0.0 标签/Release 的前提下继续前进，不是 legacy v1 产物。
从 `main` 切换到 `vnext` 作为默认分支已经完成；不再存在任何 `main` ->
`vnext` 的切换门禁。后续任何默认分支变更都属于单独的用户拍板事项。
