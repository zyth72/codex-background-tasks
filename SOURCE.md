# 来源说明

本仓库由本机 Codex 桌面应用个人插件目录（`$CODEX_HOME/plugins/local/background-tasks`）中的**自定义 0.1.9 快照**整理为独立源码仓库，不是上游项目历史，也不包含任何机器专用路径、密钥或用户数据。

## 版本与兼容名称

- 插件内部名称：`background-tasks`
- 个人 marketplace：`codex-tools`
- MCP 工具名：`run_cli`、`get_cli_task`、`list_cli_tasks`、`cancel_cli_task`、`open_task_panel`、`background_tasks`、`read_task_panel`

以上名称保持与 0.1.9 快照一致，以便现有安装、agent-link 及其它调用方在仓库更名后继续工作。

## 安装入口边界

仓库根目录的 `.mcp.json` 只是安装器使用的相对路径模板（`["scripts/server.py"]`），不是宿主可直接加载的入口，也不依赖任何宿主插值变量。`scripts/install.py` 会把源码复制到 `$CODEX_HOME/plugins/local/background-tasks`，并在安装副本中改写为绝对路径。请只通过安装器安装。

## 外部依赖（未复制，也不在本仓库许可范围内）

- 应用自带的 **`codex-app-tools`**（bundled MCP server 与 Windows Node）：`scripts/notifier.py`、`scripts/app_bridge.mjs` 只调用它来发送聊天通知，仓库不包含其代码，也不对其重新授权。
- 其它运行依赖：仅 Python 3.11+ 标准库（`tomllib` 用于解析 TOML）与应用已安装的 Node。

## 许可

本仓库自有代码使用 MIT 许可，见 `LICENSE`。MIT 仅适用于本仓库包含的源码；应用 bundled 的 `codex-app-tools` 等外部组件适用其各自许可。

## 维护提示

应用更新后，bundled `codex-app-tools` 的私有接口可能变化；通知路径（`send_message_to_thread`、管道环境变量）需要在目标机器上重新验证。项目未在“全新电脑”上做过端到端实测，README 中的安装步骤按本机快照的安装器行为编写。
