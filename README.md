# Companion Kit

> 在 Codex 里放进一个能聊天、能做事，也能用固定人物形象发照片的 Persona。当前版本 `0.9.0-dev.5`，仍是 Developer Preview。

它是一个 Codex Plugin，不是独立 App，也不是每次聊天都要先喊名字的 Skill。人物面板直接显示在 Codex 里，Persona、关系数据和参考照片只保存在本机。

## 先看懂这四件事

- 普通 Codex 任务默认什么也不加载，写代码、查问题、做分析还是原来的 Codex。
- 想长期陪伴聊天时，说“把这个任务设为陪伴任务”。想退出就说“退出陪伴任务”。
- 不想绑定任务也可以临时说“拍张照片给我”，Persona 只为这一轮出现。
- Codex 直接使用内置 `gpt-image-2`，不需要 API Key，也没有 Provider 配置页。

人物照片固定的是脸部身份，不是参考图里的发型、妆容、表情、头部角度和衣服。身体可见时会采用自然偏高挑的成年人比例，并避开大头、窄肩、短颈和超广角造成的头大身小。换一个场景，不该像把同一张证件照贴到新背景上。

## 安装

需要 Python 3.11 或更高版本。把下面整段原样发给 Codex：

```text
请帮我完整安装并验证这个项目：
https://github.com/BottleYo/companion-kit

这是一个完整的 Codex Plugin，不是独立 Skill。不要调用 skill-installer，也不要把仓库里的 skills/virtual-companion 单独复制到用户级 Skill 目录。

请把安装、Codex 内人物面板、Hook 审核引导和运行验证一起做完。Plugin 文件安装成功不等于已经可以使用。

如果本机已经有这个仓库，先按 README 的升级说明保护旧数据，不要删除重装。否则把仓库下载到普通项目目录，进入根目录后运行：
python3 skills/virtual-companion/scripts/companionctl.py install --host codex --apply

不要检查、移动或清理任何用户级 Skill 目录。

Plugin 安装后，让我在 Codex 输入 `/hooks`，找到 Companion Kit，并逐项提醒我审核：
- SessionStart → codex_context.py
- UserPromptSubmit → codex_prompt_context.py
- PreToolUse → codex_image_guard.py
- PostToolUse → codex_image_receipt.py

审核按钮必须由我亲自确认。不要写入 trusted_hash，不要使用 --dangerously-bypass-hook-trust，也不要修改用户全局 AGENTS.md。

审核完成后，提醒我完全退出并重新打开 Codex。新建任务后先说“打开 Companion Kit 人物面板”，确认 Plugin UI 能显示；不要启动独立 App Server。

在人物面板中让我用一句话或模板创建 Persona，也可以上传并预览参考照片，确认后固定主脸。不要让我重新上传已有的健康参考图。

然后让我说“把这个任务设为陪伴任务”。只有面板显示“Persona 已加载，主脸参考已就绪”，才能告诉我聊天和人物照片都已就绪。没变绿就继续检查 Plugin 是否启用、Hook 是否为当前版本，以及 Hook 是否留下健康回执。
```

你需要亲手做两件事：审核 Hooks，以及确认人物参考图。前者是 Codex 的安全边界，后者是为了避免一张路过的图片莫名其妙变成 TA 的脸。

## 装好以后怎么用

先说：

> 打开 Companion Kit 人物面板。

面板里可以：

- 用一句话创建或调整 Persona，也可以从四个模板起步；
- 上传 PNG、JPG 或 WebP，预览后固定或更换主脸；
- 查看 Persona、固定形象、Hooks 和陪伴任务是否真的就绪；
- 看 TA 今天穿什么，也可以调整这个人的私有造型偏好、换一套、微调、记住感觉或暂停；
- 一键把当前任务设为陪伴任务、拍照或退出；
- 任务连接过多或索引异常时，可明确确认后只清空任务连接；Persona、关系和参考照片仍会保留。

之后直接聊天即可，不用输入 `$virtual-companion`：

> 把这个任务设为陪伴任务。

> 今天有点烦，陪我聊会儿。

> 你今天穿什么？

> 顺便帮我看看这个项目的测试为什么失败。

> 拍张刚下班回到家的照片给我。

Persona 只影响闲聊和不关键的表达。代码正确性、事实、安全、测试和当前任务要求永远排在前面。测试红了，不能靠撒娇把它哄绿。

## 它怎么运行

```text
安装 Plugin
  → 用户审核四个 Hooks
  → 在 Codex 打开人物面板，保存 Persona / 主脸
  → 用户明确绑定某个任务
  → 该任务才加载 Persona
  → 只有照片回合才加载身份参考和 PhotoMoment
```

`SessionStart` 会在每个新任务留下一个不含聊天内容的健康回执，但未绑定任务不会注入 Persona。绑定记录只保存任务 ID 的本机 HMAC，不保存原始任务 ID 或聊天正文。

照片回合会从 Identity Pack 选择一至两张参考图。主脸必带；侧脸和体型是可选增强。最近四次照片只保存场景、发型、表情、面部动态、妆容和拍摄关系等结构化配方。自拍和被拍不再只是换个构图：被拍时会先确定相机真的在哪里、人物因什么瞬间回头或改变神态，再让光线、前景和背景层级跟这个空间关系对得上。

每日 OOTD 不在凌晨偷偷跑任务。每天第一次聊到穿搭或拍人物照片时，轻量 Persona Stylist 才从人物设定和私有造型偏好里安排当天主题：服装、配饰、发型、妆容、神态方向，以及一句“今天的小心思”。这不是固定模板；PhotoMoment 会在当天方向里继续改变表情、视线、头部角度和局部造型，用户本轮要求永远优先。

关系只控制互动距离、姿态分寸和照片发出后怎么说，不拿亲密度决定衣服该保守还是大胆。配图文字会结合 Persona、当前关系、用户上一句话和画面里真正可见的一处细节：可以分享、克制地等评价、轻轻逗一下，或在关系允许时用这个人的方式撒娇，但不会突然换成千篇一律的甜妹口吻。

同一天保留主要服装、配色和气质。用户明确说“今天换一套”时才替换，明确说“只这张这样穿”时不会改掉当天主题。只有照片真的生成成功，面板才显示“已出片”。

连续聊天不用每次背完整口令。已绑定任务里，问完“今日的 OOTD 是什么”后可以直接说“拍吧”；“拍一张我看看”这类本身已经明确的说法也能直接续上。极短句只在十分钟内、同一任务和同一 Persona 的明确照片语境中生效，用过一次就清除；单独冒出一句“拍吧”不会劫持普通任务。

界面仍可能显示 Codex 自己的工具调用或工作耗时，这是宿主 UI，Plugin 不能假装它不存在。日常陪伴回复不会再播报“Skill 已启动”、内部提示词、重连次数或生图状态。

更多细节见[产品逻辑](docs/PRODUCT_LOGIC.md)。

## 已安装旧版，怎么更新

不要先删旧版，也不要清空本地数据。在原来的项目目录运行：

```bash
git pull --ff-only
python3 skills/virtual-companion/scripts/companionctl.py upgrade check
python3 skills/virtual-companion/scripts/companionctl.py upgrade apply --confirm
```

升级会先校验 Persona、关系数据、Identity Pack 和每日穿搭记录，创建恢复点，再切换 Plugin。完成后重新审核当前版本 Hooks，完全退出并重开 Codex。旧任务不会半路换一套新 Hook。

完整回滚与恢复说明见[安全升级](docs/UPGRADE.md)。

## 当前边界

- 这是开发者预览版，还需要更多未参与开发的 Codex 环境做真实图片验收。
- 主脸参考能明显改善一致性，但生成模型不是证件照复印机，不能承诺永远零漂移。
- 关系底层已经是熟悉度、信任度、亲近度和短期气氛的组合；自动从聊天生成关系事件还没接线。
- 当前只优先完善 Codex。OpenClaw、Hermes 和 Claude 保留旧兼容层，不跟着这一版一起长大。
- 当前开发与协议验收以 macOS 为主；Windows Codex 还没有完成真机安装验收，暂不能宣称已正式支持。
- 每个宿主仍有独立 Persona，不做跨宿主同步。

## 隐私

公开仓库不包含任何人的私人 Persona、SOUL、USER、聊天记录、照片、联系人或凭据。本地关系库不保存聊天正文；Plugin UI 的工具结果也不返回任务 ID、参考图 ID、图片内容或本机路径。

详见[隐私边界](PRIVACY.md)、[数据生命周期](docs/DATA_LIFECYCLE.md)和[威胁模型](docs/THREAT_MODEL.md)。

## 开发与验证

```bash
PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=skills/virtual-companion/scripts \
  python3 -m unittest discover -s tests -v

python3 scripts/verify_public_bundle.py --forbid-text '<私人角色名称>'
```

内测顺序见[开发者内测指南](docs/DEVELOPER_PREVIEW.md)，实现进度见[实施计划](docs/IMPLEMENTATION_PLAN.md)。
