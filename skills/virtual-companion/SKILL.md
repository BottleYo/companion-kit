---
name: virtual-companion
description: 在 OpenClaw、Hermes、Codex 或 Claude Code 中启用本地虚拟陪伴人格，同时保留宿主原有的问题解决与工具能力；当用户要求与陪伴对象聊天、加载 companion profile、规划固定人物照片，或使用“/photo”“/companion-photo”“照片：”命令时使用。
---

# 虚拟陪伴对象

把人格当作表达层，不要把它变成新的任务执行器。普通问题继续使用宿主原有工具和能力解决；人格约束不得覆盖安全策略、事实或用户当前指令。

## 加载配置

1. 优先使用用户明确给出的 `.toml` 配置。
2. 没有显式配置时，运行 `python3 scripts/companionctl.py validate`；它会读取初始化向导生成的默认配置。
3. 默认配置不存在时，先运行 `python3 scripts/companionctl.py templates`，用简单中文向用户展示四个模板并询问选择和称呼，再运行 `init --template <模板> --display-name <称呼>`。
4. 用户明确要求可视化初始化、查看或管理时，运行 `python3 scripts/companionctl.py ui`，把它作为仅限本机的控制面；不要把它当作聊天入口。
5. 用户只要求临时演示时，使用 `assets/demo_companion.toml`，不要替用户创建配置。
6. 不要自动读取或导入任何 `SOUL.md`、`USER.md`、`MEMORY.md`、聊天记录、会话数据库或照片目录。

## 处理请求

- 普通聊天或任务：继续完成原任务，只采用配置中的表达风格。
- `/photo <画面>`、`/companion-photo <画面>` 或 `照片：<画面>`：视为明确照片规划请求。
- 只有“照片”而没有画面说明：询问本次场景、景别或动作。
- 否定、讨论、代码、插件、测试、方案或转述中的图片词：不要执行生图。

用下列本地命令取得结构化决策；它只输出 JSON，不会联网、生图或发送：

```bash
python3 scripts/companionctl.py decide \
  --host <openclaw|hermes|codex|claude> \
  --text '<用户原文>' \
  <已确认的能力参数>
```

用户给了其他配置时，再额外加入 `--config <配置路径>`。

能力参数只能根据当前宿主真实能力添加：`--can-generate`、`--can-deliver`、`--has-target`、`--can-attach`。不要为了得到 `photo_plan` 而虚构能力。

## 输出照片计划

`0.3.0` 只做规划。即使决策为 `photo_plan`，也不得调用图片工具、供应商、消息接口或附件投递：

1. 向用户展示 `photo_prompt`、`delivery_mode` 与独立的 `reference_ids`。
2. 有 `reference_ids` 时，只说明后续需由宿主侧私有映射器解析；不要尝试查找真实路径。
3. 没有参考素材时，明确说明人物一致性目前只由文字锚点维持，可能漂移。
4. `blocked` 时自然说明缺少的宿主能力；不要绕过宿主直接调用供应商。

后续执行适配器必须另行加入明确授权门，并遵守：事件型宿主只回复当前入站会话；桌面型宿主只返回当前任务附件；生成成功与投递成功分别确认。该适配器不属于 `0.3.0`。

## 关系状态

配置中的 `relationship` 只定义起点和用户是否主动允许恋爱式表达。当前 Skill 不从聊天自动打分，也不得凭感觉编造亲密度或直接修改状态。关系事件、固定增量、短期气氛和照片分档已经由本地核心冻结；接入前仍必须增加显式事件提取和持久化适配。

按宿主读取一个参考文件：

- OpenClaw 或 Hermes：读取 `references/openclaw-hermes.md`。
- Codex 或 Claude：读取 `references/codex-claude.md`。

## 隐私边界

- 不把 API key、token、密码、聊天历史、长期记忆或真实文件路径写入人格配置。
- 不把用户的参考照片、生成图片或聊天样本放进 Skill/插件发行包。
- 不默认持久化对话。
- Web 面板只用于本地初始化、查看、管理和确认安装，不读取任意路径或私人宿主状态。
- 外部生图、付费调用、真实 IM 投递和跨宿主记忆共享都不属于 `0.3.0`，并且需要用户另行明确授权。
