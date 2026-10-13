# agent-skills

小而独立的 AI 技能。每个技能用一个 `SKILL.md` 说明要达到的效果和必要约束。

## plain-plan

让 AI 生成或改写简单易懂的技术方案。读者应能说清谁遇到了什么问题、原来怎么做、本次只改什么，以及如何验证。

它会提醒 AI 优先复用已有能力，用具体角色和动作讲流程，解释必要术语，并区分事实与待确认事项。权限、失败处理和数据安全等重要条件仍会保留。

技能文件：[skills/plain-plan/SKILL.md](skills/plain-plan/SKILL.md)

### 在 Codex 中首次安装

```bash
git clone https://github.com/Wfyywj/agent-skills.git
mkdir -p ~/.codex/skills
cp -R agent-skills/skills/plain-plan ~/.codex/skills/
```

### 使用

在支持技能的 AI 工具中调用 `plain-plan`。在 Codex 中可以输入：

```text
$plain-plan 帮我写一个订单导出方案，先讲清已有能力、本次改动和处理流程。
```

也可以把已有方案交给它：

```text
$plain-plan 我看不懂下面这份方案，请用一个具体场景重写，保留关键条件和待确认事项。
```

## 许可证

[MIT](LICENSE)
