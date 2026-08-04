---
name: virtual-companion
description: 在 OpenClaw、Hermes、Codex 或 Claude Code 中启用本地虚拟陪伴人格，同时保留宿主原有的问题解决与工具能力；当用户要求与陪伴对象聊天、加载 companion profile、生成或规划固定人物照片，或使用“/photo”“/companion-photo”“照片：”命令时使用。Codex 支持当前任务原生预览与经单次确认的 gpt-image-2/high 严格模式。
---

# 虚拟陪伴对象

把人格当作表达层，不要把它变成新的任务执行器。普通问题继续使用宿主原有工具和能力解决；人格约束不得覆盖安全策略、事实或用户当前指令。

## 加载配置

1. 优先使用用户明确给出的 `.toml` 配置。
2. 没有显式配置时，运行 `python3 scripts/companionctl.py validate`；它会读取初始化向导生成的默认配置。
3. 默认配置不存在时，运行 `python3 scripts/companionctl.py templates`，用简单中文展示模板并询问选择和称呼，再运行 `init --template <模板> --display-name <称呼>`。
4. 用户要求可视化初始化、查看或管理时，运行 `python3 scripts/companionctl.py ui`。面板只是本机控制面，不是聊天入口。
5. 用户只要求临时演示时，使用 `assets/demo_companion.toml`，不要替用户创建配置。
6. 不要自动读取或导入任何 `SOUL.md`、`USER.md`、`MEMORY.md`、聊天记录、会话数据库或照片目录。

## 处理请求

- 普通聊天或任务：继续完成原任务，只采用配置中的表达风格。
- `/photo <画面>`、`/companion-photo <画面>` 或 `照片：<画面>`：视为明确照片请求。
- 只有“照片”而没有画面说明：自然询问本次场景、景别或动作。
- 否定、讨论、代码、插件、测试、方案或转述中的图片词：不要生图。

先用本地命令取得结构化决策：

```bash
python3 scripts/companionctl.py decide \
  --host <openclaw|hermes|codex|claude> \
  --text '<用户原文>' \
  <当前宿主真实具备的能力参数>
```

能力参数只能按当前宿主真实能力加入：`--can-generate`、`--can-deliver`、`--has-target`、`--can-attach`。不要虚构能力。用户给出其他配置时，额外加入 `--config <配置路径>`。

## Codex 图片模式

先运行 `python3 scripts/companionctl.py photo status`。四个宿主彼此独立；本版本只有 Codex 接入真实图片闭环。

用户第一次只说了照片画面、没有指定模式且尚无固定参考时，只问一个短问题：“想先快速试拍，还是把长相固定下来？固定形象会单独确认 API 费用。”不要一次抛出模型、端点、参数和命令。已有有效固定参考时默认进入严格模式的单次确认；严格模式未就绪时再让用户选择原生预览或稍后配置，绝不自动降级。

### 原生模式：默认轻便选择

用户明确说“快速试拍”“直接拍”或选择原生模式时，使用当前 Codex 提供的原生图片生成能力：

- 无需单独配置 API Key；图片计入 Codex 当前方案的使用量或额度。
- 原生能力使用 `gpt-image-2`，但公开接口不能让 Companion Kit 独立证明 `quality=high`。
- 只把结果返回当前 Codex 任务；不保存为跨任务固定身份，不外发到 IM。
- 不宣称已建立长期一致人物，也不把原生模式伪装成严格模式。

当前 Codex 没有图片工具时，说明此模式不可用，并提供严格模式配置说明；不要使用占位图或不明图片网站。

### 严格模式：固定人物与 high 请求

用户明确选择“固定形象”“严格模式”“high”或跨任务人物一致性时，使用以下流程。严格模式固定官方 OpenAI Image API、`gpt-image-2`、`high`，不允许兼容网关、模型降级或自动重试。

若 `photo status` 显示未配置，只说明需要在当前宿主进程环境中设置 `OPENAI_API_KEY`。不要索要用户在聊天里粘贴密钥，也不要把密钥写入配置、面板或项目。

首次尚无参考图时：

1. 用用户的画面要求准备一张虚构成年人候选原型：

   ```bash
   python3 scripts/companionctl.py photo prepare \
     --purpose prototype \
     --task-scope '<当前任务内稳定的不透明值>' \
     --text '<用户原文>'
   ```

2. 用自然语言说明一次：本次会按 OpenAI API 用量计费，会发送本次图片提示，只处理不模仿真人的虚构成年人。询问是否继续。照片请求本身不等于付费确认。
3. 只有用户明确确认本次调用后，使用同一 `task-scope`、完全相同的原文和返回的 `plan_id` 运行：

   ```bash
   python3 scripts/companionctl.py photo run \
     --purpose prototype \
     --task-scope '<同一值>' \
     --plan-id '<plan_id>' \
     --text '<完全相同的用户原文>' \
     --confirm-once
   ```

4. 只在当前任务展示返回的候选图片。自然询问用户是否喜欢这个形象，不展示内部提示词、路由、路径、耗时或任务状态。
5. 用户明确确认候选后，用 `photo run` 返回的 `candidate_id` 与 `profile_version` 固定身份：

   ```bash
   python3 scripts/companionctl.py identity confirm \
     --candidate-id '<candidate_id>' \
     --profile-version '<profile_version>' \
     --task-scope '<同一值>'
   ```

用户不喜欢候选时，不固定它。再次生成属于新的付费调用，必须重新 `prepare` 并重新确认；新候选会替换旧候选，不形成图库。

已有固定参考图时：

1. 以 `--purpose photo` 运行 `photo prepare`。
2. 用自然语言取得本次单次付费确认。
3. 以 `--purpose photo` 和 `--confirm-once` 运行 `photo run`；它会用已确认参考图走 Image API `edits`。
4. 只把 `artifact_path` 附加到当前 Codex 任务。宿主确认附件已接管后，运行：

   ```bash
   python3 scripts/companionctl.py photo delivered \
     --artifact-id '<artifact_id>' \
     --task-scope '<同一值>'
   ```

生成成功不等于投递成功。附件失败时不要报告已发送；也不要把本地路径转发到外部聊天。

## 保持陪伴感

- 不向用户播报 `Router`、Provider、后台生图、运行秒数、轮询、plan id 或内部提示词。
- 不先复述“收到，开始生成”。需要等待时最多用一句符合人格的自然表达；图片出来后用自然短句配图。
- 把“拍照”理解为陪伴对象分享当下，而不是用户向图片机器人下达生产指令；技术说明只在配置、授权或失败时出现。
- 普通问题解决不受关系表达影响。当前版本不从聊天自动打亲密度分，也不得凭感觉篡改关系状态。

## 其他宿主

OpenClaw、Hermes 与 Claude 在 `0.4.0` 仍只输出照片计划，不真实调用 Provider 或外发图片。按宿主读取一个参考文件：

- OpenClaw 或 Hermes：读取 `references/openclaw-hermes.md`。
- Codex 或 Claude：读取 `references/codex-claude.md`。

## 隐私边界

- 不把 API key、token、密码、聊天历史、长期记忆或真实文件路径写入人格配置。
- 不把用户参考图、生成图片或聊天样本放进 Skill/插件发行包。
- 严格模式只接受 Companion Kit 当次生成、用户确认的虚构成年人原型；本版本不导入任意外部照片或真人身份。
- 本地只保留当前身份版本的一张参考图；候选只有一个槽位，普通成图是待投递的短期文件，不形成图片历史。
- 不默认持久化对话，不自动跨宿主共享关系或图片状态。
