# Companion Kit

Companion Kit 是一个轻量、宿主无关的虚拟陪伴示例。它让 OpenClaw、Hermes、Codex 和 Claude 共用一套人格与人物照片决策规则，同时保留这些工具原有的问题解决能力。

当前 `0.3.0` 在本地 Web 初始化/管理面板、命令行向导、四宿主独立安装和照片决策核心之外，新增了版本化关系状态、图片 Provider 选择契约、照片任务状态转换骨架与本地关系状态库。关系事件提取和真实图片执行尚未接入宿主，因此它仍不会实际联网、生图或发送消息。

实际交付物是包含 `SKILL.md`、模板、脚本和插件元数据的完整项目目录（或其中的 `skills/virtual-companion` 目录），不是 Python wheel。`pyproject.toml` 只用于校验和复用核心 Python 库；终端操作统一使用下文的 `companionctl.py`，不使用 `python -m companion_kit`。

## 最简单的使用方式

启动只允许本机访问的管理面板：

```bash
python3 skills/virtual-companion/scripts/companionctl.py ui
```

浏览器会自动打开。面板可以：

- 从四套通用模板中选择人格；
- 选择“自然认识”或“已有一些熟悉感”，并决定是否允许关系逐步发展为恋爱式陪伴；
- 设置称呼并预览聊天方式、固定形象和照片质感；
- 查看与更新当前配置；
- 预览安装方案，并在第二次确认后安装到 OpenClaw、Hermes、Codex 或 Claude。

面板会按实际配置展示高级字段；只修改称呼不会覆盖它们。明确切换到另一套内置模板时，界面会提示该操作将用新模板重建高级字段。

面板每次启动都会生成随机授权链接，授权只在本次面板进程中有效；浏览器读取后会从地址栏清除授权值。服务只监听 `127.0.0.1`，不接受任意文件路径，也不读取现有 SOUL、USER、记忆、聊天或照片目录。

## 命令行初始化

直接运行：

```bash
python3 skills/virtual-companion/scripts/companionctl.py init
```

向导只问两个问题：选择风格、确认称呼。两项都可以直接按回车，使用推荐模板和默认称呼。配置默认保存在用户主目录下的 `.companion-kit/profiles/default.toml`，不会写进项目；高级用户可用 `COMPANION_HOME` 更换配置根目录。

内置模板：

- 温柔治愈（推荐）：耐心、柔和，适合日常陪伴；
- 元气朋友：明快、有行动力；
- 冷静搭档：理性、可靠，适合工作和问题解决；
- 轻松幽默：自然、有趣，但保持分寸。

想先查看模板，可以运行：

```bash
python3 skills/virtual-companion/scripts/companionctl.py templates
```

初始化后校验配置，不需要再填写路径：

```bash
python3 skills/virtual-companion/scripts/companionctl.py validate
```

验证普通任务不会被拦截：

```bash
python3 skills/virtual-companion/scripts/companionctl.py decide \
  --host codex \
  --text '帮我检查这个项目的问题'
```

验证明确照片请求：

```bash
python3 skills/virtual-companion/scripts/companionctl.py decide \
  --host codex \
  --text '照片：窗边自然光半身照' \
  --can-generate --can-attach
```

输出为 JSON。`pass_through` 表示继续使用宿主原能力；`photo_plan` 表示已得到可供未来执行适配器使用的提示词与投递约束，但 `0.3.0` 只展示计划；`blocked` 表示缺少真实能力，不会假装成功。

## 安装

- OpenClaw：使用原生 `openclaw skills install`，安装到共享 Skill 目录；
- Hermes：安装到 `HERMES_HOME/skills/virtual-companion`，未设置时使用用户 Hermes 目录；
- Codex：安装到用户级 `.agents/skills/virtual-companion`；
- Claude：安装到用户级 `.claude/skills/virtual-companion`。

不使用 Web 面板时，可以先预览：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install --host codex
```

确认后再执行：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install --host codex --apply
```

把 `codex` 换成 `openclaw`、`hermes` 或 `claude` 即可。安装器默认只预览；只有显式加入 `--apply` 或在 Web 面板二次确认才会写入。Codex 与 Claude 的 manifest 用于插件封装，不代表已注册 marketplace。

## 安装后启用

`0.3.0` 是按任务或会话启用的 Skill，不会改写全局人格：

- OpenClaw：新会话中明确说“请使用 virtual-companion，并加载我的配置”；
- Hermes：启动时使用 `hermes --skills virtual-companion`，或在当前会话明确指定该 Skill；
- Codex：在任务中使用 `$virtual-companion`；
- Claude 独立 Skill：在会话中使用 `/virtual-companion`；开发态以插件加载时使用 `/companion-kit:virtual-companion`。

初始化后的默认配置会被自动发现。只想临时演示时，也可以显式使用包内的 `demo_companion.toml`。

## 高级配置

不想使用向导时，可以复制 `skills/virtual-companion/assets/demo_companion.toml`，主要字段是：

- `display_name`：称呼；
- `persona.traits`、`speaking_style`、`boundaries`：人格表达；
- `visual.identity_anchor`、`appearance`、`default_style`：固定人物形象；
- `visual.reference_ids`：本地参考素材的不透明标识，不是文件路径。
- `relationship.starting_mode`：从“自然认识”或“已有一些熟悉感”开始；
- `relationship.romance_enabled`：是否由用户主动允许恋爱式表达，默认关闭。

不要把密钥、聊天记录、真实照片路径写进配置。校验器会拒绝敏感字段名和常见本机路径，但无法可靠识别藏在自然语言句子里的任意密钥；它不是通用秘密扫描器。完整边界见 `PRIVACY.md`。

自动化场景可以跳过问答：

```bash
python3 skills/virtual-companion/scripts/companionctl.py init \
  --template calm_partner \
  --display-name '阿序' \
  --starting-mode familiar \
  --json
```

需要允许恋爱式陪伴时再显式加入 `--enable-romance`；不填写时默认关闭。

已有配置默认不会被覆盖；只有用户显式加入 `--force` 才会替换。

## 设计边界

```text
宿主事件 → HostAdapter → CompanionKernel → Decision → 当前任务回复
```

核心不依赖任何宿主 SDK。`0.3.0` 只返回照片决策，并提供尚未自动接线的关系与 Provider 协议；未来执行适配器中，OpenClaw/Hermes 只能回复当前入站会话，Codex/Claude 只能返回当前任务附件或本地制品，而且生成和投递必须分别确认。

两类宿主的差异被压缩成很少的能力开关：

- OpenClaw/Hermes：必须同时有图片生成、媒体回复和当前会话目标；
- Codex/Claude：必须同时有图片生成和当前任务附件能力。

`reference_ids` 会作为独立字段返回给宿主侧私有映射器，不会写入供应商提示词。

图片质量基线固定为 `gpt-image-2`、`high` 和参考图编辑。普通模式优先使用合格的宿主原生路径，严格模式只接受能从回执证明实际模型与画质的路径；无法证明时关闭照片，不隐藏降级。四个宿主是独立选项，不要求用户同时安装。

## 核心规格

- [关系状态规范](docs/RELATIONSHIP_SPEC.md)：三维关系、短期气氛、冷却、每日上限和照片分档；
- [图片 Provider 契约](docs/PROVIDER_CONTRACT.md)：四宿主独立配置、能力证明、原型图流程和失败关闭；
- [数据生命周期](docs/DATA_LIFECYCLE.md)：本地存储、导出、重置、删除及 Provider 留存边界；
- [威胁模型](docs/THREAT_MODEL.md)：本地面板、关系库、图片外发和公开发行风险。

## 发布前自检

```bash
PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=skills/virtual-companion/scripts \
  python3 -m unittest discover -s tests -v

PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/verify_public_bundle.py \
  --forbid-text '<你的私人角色名称>'
```

第二条会扫描整个项目，并拒绝私人角色文件、聊天/会话状态、人物媒体、密钥文件、符号链接、缓存，以及常见 macOS、Linux、Windows 用户目录路径。

## 当前未包含

- 真实图片供应商调用；
- 真实 IM 发送；
- 从聊天自动提取并写入关系事件；
- 自动跨宿主共享关系状态；
- 原型图生成、参考资产库与图片历史；
- 真实人物照片或私人 persona。

这些功能会作为可选适配器逐项加入，而不是进入通用核心。
