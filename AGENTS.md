# AI 协作约定

你是一位会反复检查的专家，你持怀疑态度，而且你会做研究。我并不总是对的，你也不是，但我们都力求准确。

通用定义见 Agent Link 的[术语约定](https://github.com/zyth72/agent-link/blob/main/docs/TERMINOLOGY.md)。本项目统一用语：

- codex-background-tasks：本源码仓库；background-tasks：插件和 skill；background_tasks：原生面板 MCP 工具；run_cli：后台启动工具。不得混用或为了统一中文术语重命名公开标识。
- 后台运行器（Task Runner）：启动、查询、取消、执行超时与退出管理；task_id 是运行器的原生任务句柄。
- 通知桥（Notification Bridge）：向 Codex 父聊天投递消息的具体实现，不等于后台运行器或完整宿主适配器。
- 完成通知（Completion Notification）：执行终态报告，区别于子 agent 运行中的汇报（report）。命令退出成功与通知成功分别记录。
- 本项目可供 Agent Link 的 Codex 接入使用，但不依赖 Agent Link。框架、会话和父子 agent 角色不是同一个概念。

修改前读取 README.md 与 SOURCE.md，遵守外部 bundled 工具的许可边界。不要编辑已安装插件/缓存或真实 Codex 配置来完成测试；安装器测试仅用临时目录，通知仅用 mock。不得把任务记录、凭据、日志或聊天标识提交入库。

代码改动按需运行 `python3 -m unittest discover -s tests -v`；桥接脚本改动另运行 `node --check scripts/app_bridge.mjs`。纯文档改动检查链接和语义即可。
