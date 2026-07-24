# blackbox

一套"事件溯源风格"的项目记录 Skill——给任何项目（代码仓库、物理设备/
基础设施部署、长期维护的系统）用同一套记录方式，使得多年后任何不了解
背景的人，都能安全地接手继续工作。

设计细节、16 条触发规则、核心文件规范，见 [`SKILL.md`](./SKILL.md)。
这套设计的完整决策历史见 [`adr/`](./adr/) 和 [`CHANGELOG.md`](./CHANGELOG.md)。

## 这个仓库存的是什么

- **`SKILL.md` + `assets/`**：blackbox 这个 Skill 本身的定义和模板——
  这是"产品"，是要被安装/引用的东西
- **`STATUS.md` / `CHANGELOG.md` / `adr/`**：blackbox 这个项目自己的
  记录（用 blackbox 自己的规则记录 blackbox 自己的历史）——这是"关于
  这个产品的开发记录"，不是给别人安装用的

## 怎么安装使用

### Claude（claude.ai / Claude Code / Claude Cowork）

下载 `.skill` 包（如果本仓库没有直接提供，把 `SKILL.md` + `assets/`
打包成 zip，扩展名改成 `.skill`），上传后点 "Save skill"。

### Hermes Agent

把 `SKILL.md` + `assets/` 这个文件夹，放进 Hermes 的技能目录，比如：
```
~/.hermes/skills/<分类>/blackbox/
```
或者配置 `external_dirs` 指向这个仓库的本地 clone 路径。

## 为什么这个仓库存在（不只是发文件）

blackbox 早期只是在对话里生成、打包成 `.skill` 文件发给用户下载，没有
一个独立于"某次对话"的、持久的、可核对的存放位置。这导致后来真实发生
过"无法确认某份 Skill 副本是不是官方版本、有没有被私自改过"的问题
（见 [`adr/0003-put-blackbox-under-git.md`](./adr/0003-put-blackbox-under-git.md)）。

现在任何时候，`git clone`/`git pull` 这个仓库拿到的，就是确定无疑的
最新版本；有疑问直接 `git diff`/`git log`，不需要依赖间接推理或者单纯
信任对话记录。

## 版本同步提醒

如果你在某次对话里让 Claude/Hermes 修改了 blackbox 的规则，**记得把
改动同步推送到这个仓库**，不要只停留在对话里——不同步的话，仓库里的
版本会滞后于实际在用的版本，失去这个仓库存在的意义。
