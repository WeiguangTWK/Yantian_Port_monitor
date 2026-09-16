# OpenViking / Codex 接入

配置与只读验证日期：2026-09-16。

## 已落地

- Codex：`0.154.0-alpha.6.2`；Node：`24.21.0`。
- 官方 marketplace：`https://github.com/volcengine/OpenViking.git`，名称 `openviking`。
- 官方插件：`openviking-memory@openviking`，版本 `0.9.0`，已启用。
- 用户配置：`C:/Users/weiguangtwk/.codex/config.toml`，已开启 `[features] hooks = true`。
- 保留原有 `C:/Users/weiguangtwk/.openviking/ovcli.conf`；目标为 `http://127.0.0.1:1933`。后端版本 `0.4.19`，loopback dev 模式，无 API key。
- 显式 MCP：`openviking-memory`，供不加载插件的 IDE 使用，复用官方 stdio 代理及同一份 ovcli 配置。CLI 同名入口只有一份。
- 项目配置 `.openviking/config.json`：按修正后的 `AGENTS.md` 关闭自动全文采集，长期决定通过工具明确保存；不固定 peer，使用插件默认的项目身份推导。

显式 MCP 启动参数：

```toml
[mcp_servers.openviking-memory]
command = "node"
args = ["C:/Users/weiguangtwk/.codex/plugins/cache/openviking/openviking-memory/0.9.0/servers/mcp-proxy.mjs"]

[mcp_servers.openviking-memory.env]
OPENVIKING_CLI_CONFIG_FILE = "C:/Users/weiguangtwk/.openviking/ovcli.conf"
```

## 必须由用户完成

1. 重启 IDE 的 Codex 扩展，开启新会话；确认 MCP 列表中 `openviking-memory` 已连接。
2. 在本项目目录启动 `codex`，运行 `/hooks`，确认 `SessionStart`、`UserPromptSubmit`、`Stop`、`PreCompact`、`SessionEnd` 的当前定义已信任。最终 doctor 已发现 5 个信任记录，但没有观察到 Hook 执行记录。
3. 使用 `/mcp` 确认工具可用。Hook 信任完成后再验证自动召回和采集；安装本身不能证明 Hook 已执行。

插件默认会自动召回用户背景/相关记忆、采集对话，并在压缩或正常结束时提交用于长期记忆提取。本项目按 `AGENTS.md` 关闭自动全文采集，不能期待例行对话自动落入记忆；仅明确保存长期结论。按当前官方文档，完整插件使用 Codex CLI；IDE 支持 MCP，但不支持插件，不能把 MCP 连通等同于 IDE 自动 Hook 生效。

## 验证结果

官方只读 doctor：0 failures。真实数据 API、记忆目录及 `/ready` 全部通过。

通过官方代理完成 stdio `initialize`、`tools/list`（15 个工具）、只读 `health` 和 `list` 调用。未创建测试记忆、未改动后端配置、未重启后端。安装时离线诊断确认 auto-capture OFF、5 个 Hook 存在信任记录。随后按修正后的 `AGENTS.md` 移除了错误的 `aegissu` peer 固定值；本项目不启用自动捕获/提取。

诊断命令：

```cmd
node C:\Users\weiguangtwk\.codex\plugins\cache\openviking\openviking-memory\0.9.0\scripts\ov-memory-doctor.mjs --no-color
codex mcp list
```

Windows 使用 ACL 判断权限，不直接照搬 doctor 的 Unix `chmod 600` 提示。若未来配置 API key，应保持用户级存储并复核 ACL，不提交到源码。

## 升级

运行 `codex plugin marketplace upgrade openviking`，随后核对实际已安装插件版本。显式 MCP 当前固定在 `0.9.0` 缓存路径：升级或删除旧缓存后必须将 `args` 更新为新版本代理路径，并重启客户端。Hook 定义变更后需重新在 `/hooks` 审核。

## 文档依据

- [OpenViking Codex Memory Plugin](https://github.com/volcengine/OpenViking/blob/main/docs/en/agent-integrations/04-codex.md)
- [OpenViking 插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md)
- [OpenAI Hooks：发现与信任机制](https://learn.chatgpt.com/docs/hooks)
- [OpenAI MCP：CLI/IDE 共享配置](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [OpenAI Plugins：客户端支持范围](https://learn.chatgpt.com/docs/plugins)
