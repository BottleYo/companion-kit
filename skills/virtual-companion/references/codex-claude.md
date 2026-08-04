# Codex / Claude 桌面适配

这两类宿主都只能把结果返回当前任务，不推断外部联系人，也不默认投递到 IM。四个宿主是独立安装项；用户只安装一个完全正常。

## Codex

安装通用 Skill：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host codex \
  --apply
```

默认安装到用户级 `.agents/skills/virtual-companion`。仓库中的 `.codex-plugin/plugin.json` 用于插件封装；manifest 本身不代表已注册 marketplace。

`0.5.0` 有两种 Codex 图片模式：

- 原生模式：当前任务内使用 Codex 图片能力，无需 Companion Kit API Key；适合快速预览。模型由 Codex 管理，不能由本项目独立证明 `quality=high`，也不承诺跨任务固定身份。
- 严格模式：当前 Codex 进程环境需要 `OPENAI_API_KEY`，每次真实调用前单独确认；请求固定官方 Image API、`gpt-image-2`、`high`。首次生成候选原型，确认后只保存一张私有参考图，后续使用 `edits`。

检查状态：

```bash
python3 skills/virtual-companion/scripts/companionctl.py photo status
```

macOS、Linux 或 Windows 终端用户，应先在启动 Codex 的同一个环境中设置 `OPENAI_API_KEY`，再启动 Codex。不要把 Key 写进人格 TOML、项目文件、Skill、Web 面板或聊天消息。Codex 桌面应用若没有继承终端环境，严格模式会显示“需要配置”，但原生模式仍可用。

严格模式按 OpenAI API 用量单独计费。每次授权只允许一次调用；原文、人格版本、参考图、任务作用域或路由发生变化后，必须重新确认。API 失败不会自动重试或切换模型。

生成结果只作为当前任务附件。候选确认前不会成为固定形象；普通成图在宿主接管附件后清理，不形成图库。当前版本不允许导入任意外部照片或真人身份。

## Claude Code / Desktop

安装方式：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host claude \
  --apply
```

默认安装到用户级 `.claude/skills/virtual-companion`。开发态也可以用 `claude --plugin-dir .` 加载插件根目录；开发态命令为 `/companion-kit:virtual-companion`，独立 Skill 为 `/virtual-companion`。

Claude 在 `0.5.0` 仍只输出图片计划。即使环境存在图片工具，也不要复用 Codex 或事件型宿主的严格执行命令冒充 Claude 适配器；缺少明确的当前任务附件契约时返回 `image_generation_unavailable`。

## 共同规则

- 普通任务继续由 Codex 或 Claude 原有工具完成。
- 新装 Skill 后用新任务验证，避免旧上下文沿用缓存指令。
- 不展示内部图片提示词、路由、计时或本地路径。
- 不读取现有私人 persona、聊天、记忆或照片目录。
