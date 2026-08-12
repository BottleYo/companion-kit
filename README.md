# Companion Kit

> 给 Codex 加一个会聊天、会做事、也会发人物照片的虚拟陪伴对象。当前版本 `0.7.0-dev.10`，处于 Codex-first Developer Preview。

Companion Kit 的载体是一个 Codex Plugin，加上一块只在本机打开的 Persona 面板。它不是独立 App，也不是需要反复点名调用的 Skill。

装好以后，你可以直接聊天、让它帮忙解决问题，或者说一句“拍张照片给我”。Persona 决定相处方式和人物形象，Codex 原来的 coding、分析和工具能力照常工作。测试红了，不能靠撒娇把它哄绿。

## 30 秒看懂

- 用一句话或模板创建 Persona，之后随时回面板调整。
- 直接上传参考图，或让 Codex 生成候选；确认后才固定人物主脸。
- 固定的是脸，不是发型、妆容、表情和衣服。每次照片会按场景重新安排这些变化。
- Codex 直接使用内置 `gpt-image-2`，不需要 API Key，也没有 Provider 配置。
- 普通聊天不显示“Skill 已启动”，照片流程也不会把内部提示词和后台状态念出来。
- Persona、关系数据和参考图保存在本机；升级不会把它们当缓存清掉。

现在适合开发者试用，还不适合宣传成“永远不会换脸”的成熟产品。真实生成模型仍可能漂移，关系也还没有接上自动变化。完整边界在后面写明。

## 最省事的安装方法

需要 Python 3.11 或更高版本。把下面整段原样发给 Codex，它会负责安装和排查，不会装到一半就把 Hook 留给你猜。

```text
请帮我完整安装并验证这个项目：
https://github.com/BottleYo/companion-kit

这是一个完整的 Codex Plugin，不是独立 Skill。不要调用 skill-installer，也不要把仓库里的 skills/virtual-companion 单独复制到用户级 Skill 目录。

请把安装、面板初始化、Hook 审核引导和运行验证一起做完。Plugin 文件安装成功不等于已经可以使用。

如果本机已有这个仓库，请先按 README 的升级说明保护旧数据，不要删除重装。否则把仓库下载到普通项目目录，进入根目录后运行：
python3 skills/virtual-companion/scripts/companionctl.py install --host codex --apply

不要检查、移动或清理任何用户级 Skill 目录。安装 Plugin 后运行：
python3 skills/virtual-companion/scripts/companionctl.py ui

打开本地面板，让我完成 Persona 初始化和人物主脸确认。

面板配置完成后，让我在 Codex 输入 `/hooks`，找到 Companion Kit，并逐项提醒我审核：
- SessionStart → codex_context.py
- UserPromptSubmit → codex_prompt_context.py
- PreToolUse → codex_image_guard.py
- PostToolUse → codex_image_receipt.py

审核按钮必须由我亲自确认。不要写入 trusted_hash，不要使用 --dangerously-bypass-hook-trust，也不要修改用户全局 AGENTS.md。

我确认审核完成后，提醒我完全退出并重新打开 Codex，再新建任务发送：
“请继续检查 Companion Kit 是否已经真正就绪；打开面板核对 Hook 状态，没变绿就继续排查，不要让我重新上传已有的健康参考图。”

只有面板变成绿色，并显示“Persona 与主脸已成功加载”，才能告诉我聊天和人物照片都已就绪。没有变绿就继续检查 Plugin 是否启用、Hook 是否为当前版本，以及 SessionStart 是否留下健康回执。
```

你唯一需要亲手做的，是在 Hook 审核页点击信任。这是 Codex 的安全边界；安装程序不该替你写入信任记录。Plugin 装上只是把门牌挂好了，面板变绿才算真的有人住。具体规则可看 [Codex Hook 官方说明](https://learn.chatgpt.com/docs/hooks)。

## 装好以后怎么用

新建一个 Codex 任务，正常说话就行，不用输入 `$virtual-companion`：

> 今天有点烦，陪我聊会儿。

> 帮我看看这个项目的测试为什么失败。

> 拍张刚下班回到家的照片给我。

要改 Persona、上传参考图或查看运行状态时，在项目目录重新打开面板：

```bash
python3 skills/virtual-companion/scripts/companionctl.py ui
```

面板只监听 `127.0.0.1`，每次启动都会换一个临时访问令牌。

## Persona 和照片是怎么工作的

Persona 是一张轻量人物面板，里面有称呼、性格、说话方式、生活底色、兴趣、做事习惯、相处边界、外在气质和关系投影。你只写“高冷御姐，成熟自信，做事利落”也可以，系统会先补出一版，再由你调整。模板只是起点，不是选秀名单。

形象可以初始化时决定，也可以先跳过。第一次需要人物照片时有两种选择：

1. 上传一张你有权使用的成年人物或虚构形象参考；
2. 描述想要的感觉，让 Codex 生成候选。

候选要经过预览和确认，才会成为固定主脸。以后可以随时更换；新脸确认前旧脸继续生效，确认后 Persona、关系进度和历史照片仍然保留。

一张清楚的主脸就能开始。侧脸和体型参考是可选增强，不是初始化考试。运行时会按画面最多取两张权威参考，主脸一定在；缺失或损坏时停止本次生图，不用文字偷偷再捏一张陌生脸。

人物身份和当次造型是分开的。每张照片都会生成一个轻量 PhotoMoment，安排场景、镜头、动作、发型、表情、妆容、服饰和回图后的那句话。最近几次只保存结构化配方，用来避免连续换背景却一直顶着同一个表情。

更多细节见[产品逻辑](docs/PRODUCT_LOGIC.md)。

## 会不会影响 Codex 干正事

通常不会有明显影响，但也不是物理意义上的零开销。

新任务只加载一份压缩后的 Persona 和关系分寸，常驻内容限制在 1400 个字符以内。照片相关 Hook 只在图片回合读取身份和 PhotoMoment；普通 coding 工具不会触发整套照片流程。代码正确性、事实、安全和当前任务要求始终优先于人设语气。

Codex 界面自己的工作耗时和折叠工具轨迹仍可能显示，这是宿主界面，Plugin 无法把它藏掉；Persona 的回复不会再复述这些后台过程。禁用 Plugin 后，Codex 会恢复没有 Companion Kit 的正常状态。

## 关系会自动变化吗

关系底层不是一根“聊十句就升级恋人”的经验条，而是熟悉度、信任度、亲近度和短期气氛的组合。它可以分别影响闲聊语气、主动程度和照片表达边界。

当前版本已经能读取这套关系状态，但还没有完成从日常聊天中自动提取事件并更新关系。现在不会偷偷搜几个关键词就给亲密度加分。设计细节见[关系状态规范](docs/RELATIONSHIP_SPEC.md)。

## 已经装过，怎么升级

不要先删旧版。在原项目目录更新代码，再打开面板：

```bash
git pull --ff-only
python3 skills/virtual-companion/scripts/companionctl.py ui
```

面板里的“备份和更新”会先校验 Persona、关系数据和 Identity Pack，再切换 Plugin；检查失败会恢复旧程序。更新后需要重新审核当前版本的 Hook，完全退出并重开 Codex，再用新任务验证。

详细的恢复点、命令行升级和手动回滚见[安全升级说明](docs/UPGRADE.md)。

## 当前边界

- 这是 Developer Preview，人物一致性已经有本地链路和自动测试，但还需要更多真实 Codex 环境验收。
- 生成模型不是证件照复印机，多角度参考能提高稳定性，不能保证永远零漂移。
- 关系自动变化尚未接线。
- 当前只优先完善 Codex；OpenClaw、Hermes 和 Claude 保留兼容层，但不包含这一轮全部能力。
- 每个宿主有独立 Persona 和私有数据，不会默认共用一锅配置。

试用方式和建议验收顺序见[开发者内测指南](docs/DEVELOPER_PREVIEW.md)。

## 隐私

公开仓库不包含任何人的私人 Persona、SOUL、USER、聊天记录、人物照片、联系人或凭据。本地关系库只保存结构化状态，不保存聊天正文；参考图和恢复点也放在 Codex 的私有数据目录，不进 Git。

完整说明见[隐私边界](PRIVACY.md)、[数据生命周期](docs/DATA_LIFECYCLE.md)和[威胁模型](docs/THREAT_MODEL.md)。

## 开发与验证

```bash
PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=skills/virtual-companion/scripts \
  python3 -m unittest discover -s tests -v

PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/verify_public_bundle.py \
  --forbid-text '<你的私人角色名称>'
```

继续看实现，可以从[产品逻辑](docs/PRODUCT_LOGIC.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md)和[开发者内测指南](docs/DEVELOPER_PREVIEW.md)开始。
