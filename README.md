# 盐田船期监控

按船名或码头航次查询公众船期，保存状态并比较 ETB、ETD 等字段。公众查询无需账号或查询 Token；浏览器用于获取 EdgeOne Cookie。

## 当前功能

CLI 支持单轮查询、循环监控、变更比较及告警。GUI 支持目标管理、手动查询和结果查看；查询设置、通知配置及独立常驻监控尚未实现。

## GUI 目标管理

进入“目标管理”页可新增、编辑、启用 / 停用和删除船名或码头航次。每次操作立即保存到 watchlist.json；没有配置文件时，首次保存会创建配置，无需手工复制模板。

左侧导航收起时显示主页和船舶图标；点击菜单展开后，图标右侧显示“主页”和“目标管理”。

新增和编辑时去除首尾空白；同类型目标按名称不区分大小写去重，停用目标也参与检测。显示名称仅影响展示。删除需要确认，不删除查询历史；修改查询名称或类型后建立新的比较基线。

查询进行中暂不可修改目标。保存保留其他查询设置、通知配置和注释字段；若文件已被外部修改，请点击“重新加载”后重新编辑。首次创建的配置没有通知通道，可后续通过配置文件设置。

## 源码运行

主环境为 Python 3.8、PySide2 / Qt 5.15。安装对应依赖后复制配置：

```bat
python -m pip install -r requirements.txt
python -m pip install -r requirements-gui.txt
copy watchlist.example.json watchlist.json
python run_monitor.py
python -m gui.app
```

修改 targets：ship 按船名查询，voyage 按码头航次查询。船名建议填写完整，以减少子串匹配。

```bat
python run_monitor.py --watch 1800
python run_monitor.py --no-notify
python run_monitor.py --test-alert
python run_monitor.py --dump-html snapshots
python tools/env_check.py --browser C:\Supermium\supermium.exe
```

--test-alert 会真实发送通知；env_check 会启动浏览器并访问站点。--dump-html 仅保存首分页 HTML。--show-browser 显示 Cookie 引导窗口，--verbose 显示调试日志。
退出码：0 无变更、10 有变更、1 配置或通道错误、3 查询失败。

## 配置与通知

配置见 [watchlist.example.json](watchlist.example.json)。旧 token 与 settings.precheck 被忽略，通过 AppConfig 保存时不再输出；GUI 目标管理仅修改 targets，不清理其他字段。旧 --token、--no-auto-renew 参数与续期工具已移除。
通知支持 Windows、钉钉、企业微信、飞书、webhook 和邮件。默认在变更、未查到及连续错误达到阈值时告警，首次查询用于建立基线。Windows 通知需要已登录的交互式桌面会话；无人值守可配置邮件或 webhook。

stale_after_hours 在恢复运行时检测长时间未成功查询；heartbeat_hours 定期发送运行消息。进程停止后两者都不能自行告警。heartbeat_url 可接入外部心跳服务，由外部服务检测停止。
watchlist.json 可能包含凭证，勿提交或分享。Git 忽略和打包排除不能代替实际交付检查。

## 查询与本地数据

默认起始日为今天减 7 天，max_pages 限制查询分页。state/ 保存状态，每个航次最多保留 200 条观察记录；.cache/ 保存 Cookie，.browser_profile/ 保存浏览器配置，snapshots/ 为可选快照。这些运行数据不随源码交付，清理前确认是否需要保留历史。
会话失效时重引导 Cookie 并重试一次；HTTP 429、503、567 采用退避重试。首个目标发生会话、网络或限流错误时，本轮跳过后续目标。

## Windows 7 与交付

Win7 使用 Python 3.8 和兼容浏览器，依赖见 requirements-win7*.txt。[实机验证指南](docs/Win7-实机验证指南.md) 列出现场检查步骤；本轮静态清理不构成新的实机验证。
tools/make_bundle.py 生成源码交付包，tools/build_gui.py 和 tools/build_release.py 用于构建程序。

以下为历史记录，不作为当前运行指南，也不进入源码交付包：

- [站点勘察](RECON-盐田船期.md)
- [GUI 技术选型](docs/GUI-技术选型与基座.md)
- [GUI 打包记录](docs/GUI-Win7打包实测.md)

项目文件与现行源码优先于历史文档及 OpenViking 记忆。
