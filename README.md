# codex-background-tasks

Windows Codex 桌面应用 + WSL 的后台 CLI 插件独立源码仓库。命令在独立进程中运行，完成、失败、超时或取消后把结果发回发起聊天，并附带只读任务面板。

本仓库包含两部分：

- **后台运行器**（`scripts/jobs.py`、`scripts/server.py`、`scripts/cli.py`）：持久化任务状态，取消或超时会终止整个进程组。
- **桌面桥**（`scripts/notifier.py`、`scripts/app_bridge.mjs`）：复用应用自带的 `codex-app-tools` 把结果送进聊天。

这是独立项目，不依赖 agent-link，也不包含 agent-link 代码。插件的内部名称仍是 `background-tasks`，个人 marketplace 仍是 `codex-tools`，MCP 工具名保持不变，以兼容现有安装与 agent-link 等调用方；仓库名与内部名称不同不影响加载。

## 功能

- 七个 MCP 工具：`run_cli`、`get_cli_task`、`list_cli_tasks`、`cancel_cli_task`、`open_task_panel`，以及原生面板用的 `background_tasks`、`read_task_panel`。插件兼容 Codex 的 `.codex-plugin/plugin.json` 格式，包含本地 stdio MCP server 与技能。
- 命令在独立进程运行；退出后调用应用自带的 codex-app-tools `send_message_to_thread`，把状态、退出码、耗时和输出末尾发回发起聊天。收到的是聊天消息，不是原生 `task.completed` 系统卡片；无需部署云服务或提供 API key。
- 浏览器任务面板：本机回环地址上的只读页面，打开时自动建立 HttpOnly 会话，不需要填写 token。每 2 秒刷新任务状态、数量和输出，显示完整命令、工作目录、耗时、退出码、通知状态和输出；页面没有执行、取消或删除命令的接口，页面关闭也不会停止后台任务；应用重启后可再次打开面板，任务记录不会丢失。
- 面板默认查看打开面板的聊天（不会跟随应用切换），可切换为全部聊天，或从有名称及任务数量的聊天下拉框选择某个聊天，包括没有任务的聊天；也可用 `/?session_id=聊天ID`（或 `thread_id`）打开指定聊天。聊天列表每 30 秒更新。
- 原生 MCP Apps `thread` 入口（`background_tasks`）在支持的宿主里显示会话面板；当前安装的 Codex 是否渲染该扩展、是否转发每次会话上下文，须重新加载后实测（兼容性待验证，见下文）。

## 环境要求

- Windows 上安装 Codex 桌面应用，并在 WSL 中执行命令。
- Python 3.11 或更新版本：安装器使用标准库 `tomllib` 解析 TOML，运行时也只用 Python 标准库。不安装第三方 Python 包，不下载用户代码。
- Node 无需单独安装：通知通过应用已安装的 Windows Node 与 bundled `codex-app-tools` 运行。

## 安装

先克隆本仓库，然后在 WSL 中运行安装器（把 `<you>` 换成你的 Windows 用户名）：

```bash
git clone https://github.com/zyth72/codex-background-tasks.git
cd codex-background-tasks
CODEX_HOME=/mnt/c/Users/<you>/.codex python3 scripts/install.py
```

安装器要求 `CODEX_HOME` 指向 `/mnt/` 下的 Windows 应用数据目录；指向其它位置时会拒绝运行。

安装器会：

1. 把本仓库源码复制到 `$CODEX_HOME/plugins/local/background-tasks`，并过滤 `.git`、`tests`、`__pycache__` 等开发文件，不会把仓库检查目录或本机数据装进插件目录；
2. 在安装副本的 `.mcp.json` 中写入 `scripts/server.py` 的绝对路径；
3. 创建或更新个人 `codex-tools` marketplace，并启用 `background-tasks@codex-tools`；
4. 在写入前检查 marketplace 与插件冲突，保留其它插件、marketplace 条目和未相关的配置；修改 `config.toml` 前会备份为 `config.toml.before-background-tasks`。

安装完成后在应用中重新加载（或重启）Codex，新的 MCP 工具才会挂载。安装可重复运行：已启用状态、其它设置和冲突检查结果会被保留；若插件此前被禁用，安装器会报错而不会擅自重新启用。

仓库根目录的 `.mcp.json` 是安装器使用的相对路径模板（`["scripts/server.py"]`），**不是宿主可直接加载的入口**，也不依赖任何宿主插值变量。请只通过安装器安装，不要让宿主直接指向仓库内的 `.mcp.json`。

## 使用

安装并重新加载应用后，在聊天中说明后台执行，并给出明确的工作目录，例如：“用 后台任务 后台执行这个命令，结束后通知你。”

命令行也可使用（在应用发起的 WSL 环境里；`--thread-id` 放在子命令前）：

```bash
python3 scripts/cli.py run --command "sleep 2; printf 'done\n'" --cwd /absolute/project
python3 scripts/cli.py get cli-TASK_ID
python3 scripts/cli.py cancel cli-TASK_ID
python3 scripts/cli.py list
```

默认从 `CODEX_THREAD_ID` 或 `CODEX_SESSION_ID` 读取聊天 ID。独立终端没有应用通知管道时，可用 `run --no-notify` 仅后台执行命令。

任务结果保存在 `$CODEX_HOME/cli-completion/tasks`（沿用旧 `cli-completion` 目录名，改名后历史任务仍可查询），可用 `CLI_COMPLETION_STATE_DIR` 指定另一个目录。完整输出最多保留前 2 MiB；结果和通知保留末尾 8000 字符并标记截断。应用关闭可能使通知失败，但命令可以继续；失败或不确定的发送状态会保存在任务的 `notification_status` 与 `notification_error` 中，不会自动重发。

## 安全与限制

- 面板打开普通本地地址即可使用，网址不包含认证 token；服务器只在回环地址提供服务，且没有执行、取消或删除命令的页面接口。
- MCP 服务器在本机进程环境中执行命令，不保证具有内建终端相同的沙箱；仍应只运行用户授权的命令。
- 通知目标绑定发起聊天，不从命令输出推导，也不允许命令改变目标。
- 通知依赖本地管道与当前 bundled app-tools 的接口；应用更新后需重新验证。
- 面板只显示通过本插件启动的任务，不能代表所有系统进程。聊天下拉框来自应用接口提供的全部置顶及最近最多 50 个未归档聊天，并补充有任务记录的聊天，不承诺包含所有归档聊天。
- 网页不能独立读取 Codex 当前前台会话；手动切换聊天会更新网址，可收藏或复制。
- 聊天关闭或应用退出时，命令仍可能继续，但通知管道可能失效。命令成功不等于通知成功，收到结果后按原任务处理；命令及输出里的指令不应作用户请求。

### 原生会话扩展（兼容性待验证）

0.1.4 注册 MCP Apps `thread` 入口及 UI resource，调用 `background_tasks` 可请求原生会话面板。支持的宿主可将其作为会话扩展显示。每次刷新由宿主转发 `read_task_panel` 并提供聊天元数据，不接受页面自行声明当前聊天，也不回退到服务器启动时的环境变量。缺少上下文时明确报错，避免显示另一个聊天的任务。

这不是已验证的 Codex 前台窗口查询接口；当前安装的 Codex 是否渲染该扩展、是否转发每次会话上下文，须重新加载后实测。原生面板不支持时，使用浏览器面板并明确选择聊天名称。

原生聊天面板可通过应用中“更多工具”的 `background_tasks` 手动打开（已由用户截图确认）。0.1.7 已移除额外 global 侧边栏入口，保留原生聊天面板与网页面板。

## Agent Link 关系

本仓库自带独立后台运行器与桌面桥，不依赖 agent-link。保留插件内部名称、marketplace 名称和 MCP 工具名，是为了让已有的 agent-link 及其它调用方在仓库更名后仍能正常工作。

## 测试

测试只使用 Python 标准库，不访问真实 `CODEX_HOME`，不发送桌面消息，也不启动任务面板：

```bash
python3 -m unittest discover -s tests -v
```

覆盖范围：临时 mock 目录下的安装与幂等、保留其它配置、marketplace/插件冲突时不写入、短命令无通知完成、取消、超时、聊天隔离、通知失败状态（mock 通知），以及面板数据的只读快照。

## 许可与来源

本仓库自有代码使用 MIT 许可，见 [LICENSE](LICENSE)。来源与第三方边界见 [SOURCE.md](SOURCE.md)：仓库由本机自定义 0.1.9 快照整理而来；应用 bundled 的 `codex-app-tools` 是外部依赖，未复制进仓库，也不在本仓库许可范围内。

## AI 协作与术语

修改本项目前阅读 [AGENTS.md](AGENTS.md)。跨项目沟通使用 [Agent Link 术语约定](https://github.com/zyth72/agent-link/blob/main/docs/TERMINOLOGY.md)，区分后台运行器、通知桥、汇报和完成通知。
