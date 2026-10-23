# agent-skills

小而独立的 AI 技能。每个技能用一个 `SKILL.md` 说明要达到的效果和必要约束。

## 项目方向与调研

目标是逐步形成从一句话需求到代码、验证和 CI 交付的开发工作流，支持只做前端、只做后端或前后端协作。技能负责说明怎么判断和行动，脚本与现有工程工具负责执行和检查。

目前已实现的技能只有 `plain-plan`，完整工作流尚在调研阶段：

- [2026-10-23：AI 全栈开发工作流开源调研](docs/research/2026-10-23-fullstack-workflow.md)——近一周更新、具体可借鉴文件、工具依赖、许可证、接入取舍与第一版路线。

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
