# 南信大课表 · AstrBot 插件

获取南京信息工程大学教务系统的理实一体课表，通过 AstrBot 所连接的平台发送当日课程。支持理论课、实验课、教学周、调休映射、未排课提醒，以及登录失效后的有限期缓存。

**2.1.0：** 默认每 5 分钟自动保活，保存学校轮换的 Cookie；教务登录过期时，尝试复用已有门户认证恢复会话。新增 `/登录状态` 查看检查结果。保留 2.0.1 对 `filelock==4.2.4` 的兼容，升级无需新增依赖。

要求 **AstrBot ≥ 4.5.0**，平台适配器须支持 `Context.send_message` 主动消息。学校网络查询在后台线程执行，不阻塞机器人处理其他消息。Server酱通知渠道、SendKey 配置和独立 systemd 推送任务已移除。

**2.0.1：** 移除 `filelock<4` 的依赖限制，兼容 AstrBot 4.29.3 固定使用的 `filelock==4.2.4`。如果 2.0.0 因依赖冲突安装失败，请重新上传新版安装包，无需修改 AstrBot 核心依赖。

## 聊天命令

| 命令 | 功能 |
|---|---|
| `/设置时间 8` | 开启当前会话每天北京时间 **08:00** 的课表推送 |
| `/设置时间 08:30` | 改为每天 **08:30**，小时范围 0–23 |
| `/设置时间` | 按插件配置的默认时间开启推送，初始为 **07:00** |
| `/查看课表` | 立即查询北京时间当天课表，不影响定时推送 |
| `/取消推送` | 停止当前会话的每日推送 |
| `/登录状态` | 查看自动保活状态、最近检查及真实接口验证成功的时间 |

首次使用时，在接收课表的私聊或群聊中发送 `/设置时间 8` 即完成订阅；命令会回复下次推送日期。各私聊、各群分别保存时间，在群内设置会将课表推送到该群；同一群的设置由该群共用。请确保 AstrBot 的唤醒前缀包含 `/`；群聊如要求唤醒，可先 @机器人再发命令。

**一个插件实例使用一套学校登录会话，所有订阅者查看同一份课表。** 这是原有单账号程序的延续，不是多学生账号绑定系统。

## 安装到 AstrBot

1. 将发行包 `astrbot_plugin_nuist_course.zip` 上传到 AstrBot 管理面板的插件安装入口；或将源码放入 `AstrBot/data/plugins/astrbot_plugin_nuist_course/`。插件目录下应直接包含 `main.py`、`metadata.yaml`、`_conf_schema.json`、`requirements.txt` 和 `coursebot/`。
2. 让 AstrBot 安装 `requirements.txt` 中的依赖；如需手工安装，使用 **AstrBot 自己的 Python 环境**执行 `python -m pip install -r requirements.txt`。
3. 启用插件，按下文导入学校会话。在聊天中发送 `/查看课表` 核对内容，再发送 `/设置时间 8` 开启每日推送。

插件运行不需要浏览器。只有人工登录助手需要 Playwright；不要为运行插件单独安装另一个 AstrBot。

## 从当前程序迁移

保留已有 `state/session.json`、`state/snapshot.json`，无需修改文件格式。有两种用法，任选一种：

- **复用原目录：** 在 AstrBot 插件配置中，将 `state_dir` 设为原 `state` 目录的绝对路径。例如，同一台 Windows 电脑可填写 `D:/nuist-course-notifier/state`。
- **复制到默认目录：** 将这两个文件复制到 `AstrBot/data/plugin_data/astrbot_plugin_nuist_course/state/`。插件配置的 `state_dir` 保持空白。Docker 部署须复制到持久化的 AstrBot `data` 卷，并使用容器内路径。

插件配置通过 AstrBot 面板管理，原 `config.json` 仅供命令行登录助手使用。已有 `date_overrides` 等配置需相应填入插件配置。不要复制旧的推送日志、密钥文件；插件不读取它们。

如果服务器以前安装了旧 systemd 定时器，请先停用，避免旧版已安装程序继续发送通知：

```bash
sudo systemctl disable --now nuist-course.timer
sudo systemctl stop nuist-course.service
```

本次源码转换不会远程修改旧服务器上已安装的服务。

## 首次登录或登录失效后更新

在能打开学校网页的 Windows 电脑上，进入本项目目录，执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[login]"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item config.example.json config.json
.\.venv\Scripts\python.exe -m coursebot login
```

`Copy-Item` 仅在首次配置时执行，已有 `.venv`、`config.json` 和 `state` 可以继续使用。使用电脑已有的 Edge 可跳过 Chromium 下载，改为：

```powershell
.\.venv\Scripts\python.exe -m coursebot login --channel msedge
```

在浏览器中自行完成账号、密码及验证码，看到信息门户后回到终端按回车。助手复用原标签页进入教务与理实一体周课表；核对后再按回车，保存会话并同步整学期课表。关于 `about:blank` 的处理见 [登录助手说明](docs/windows-login-fix.md)。

```powershell
.\.venv\Scripts\python.exe -m coursebot sync
.\.venv\Scripts\python.exe -m coursebot preview
```

`sync` 用于确认实时接口；`preview` 仅输出内容。如果使用缓存，输出会明确标记，不能据此认定实时连接成功。异机部署时，将更新后的两个 JSON 文件重新复制到插件实际使用的 `state_dir`。验证码仍需人工处理，会话期限和跨 IP 可用性取决于学校。

## 自动维持登录（2.1.0）

启用插件后，会立即开始独立的后台保活任务，默认每 **5 分钟**访问门户入口并读取当前学期。读取接口验证成功后保存服务端返回的新 Cookie；无需发送聊天命令，也无需先订阅推送。学校网络请求与查看课表共用文件锁，避免同时覆盖会话。

当接口提示未登录时，自动尝试沿现有门户 → 教务 → 课表入口完成普通 SSO 跳转，再重试一次只读接口。只有真实接口返回有效数据才视为成功；单纯出现 HTML 登录页不算恢复。此方式使用现有 Cookie，不需要录入账号密码，也不需要在 AstrBot 中安装浏览器。

**升级时如果旧会话已经完全过期，请先重新人工登录并导入一次新会话，再保持 AstrBot 持续运行。** 新文件会在下一次检查时自动读取，通常 5 分钟内生效。旧版本在两次查询之间没有保活；新版主要解决因长时间无请求而触发的闲置超时。学校强制到期、异地登录踢出、门户认证彻底失效或验证码仍可能需要人工登录，不能保证永久在线。

发送 `/登录状态` 可看到最近检查结果和最近一次接口验证时间；命令显示最近的后台检查记录，不会立即触发新认证。持续失败时每 5 分钟再尝试，日志在状态变化时记录。`session-health.json` 保存检查状态；它不包含 Cookie。保活本身不更新 `snapshot.json` 的同步时间，避免把旧课表当作新数据。

## 插件配置

| 配置项 | 默认值 | 用途 |
|---|---|---|
| `state_dir` | 空白 | 使用插件持久化数据目录下的 `state/`；也可填写已有目录 |
| `default_push_time` | `07:00` | `/设置时间` 不带参数时使用的时间；不覆盖已有订阅 |
| `cache_max_age_days` | `7` | 学校读取失败时备用课表的最长天数，0 关闭 |
| `request_timeout_seconds` | `25` | 学校接口单次请求超时，单位秒 |
| `date_overrides` | `{}` | 调休映射的 JSON 文本 |
| `keepalive_interval_minutes` | `5` | 后台保活间隔，1–20 分钟；0 关闭 |
| `auto_sso_recovery` | `true` | 接口未登录时尝试复用门户认证恢复，最多重试一次 |

相对的 `state_dir` 从 `data/plugin_data/astrbot_plugin_nuist_course/` 计算。更改插件配置后重载插件使其生效；设置时间命令实时生效。

调休格式示例（仅展示格式，请以学校通知为准）：

```json
{"2026-10-31":"2026-10-27","2026-11-01":null}
```

表示前者按指定日期的周次与星期上课，后者停课。保留学校提供的教学日历校验，不会把接口异常、缓存过期或超出学期范围当作“今天没课”。

## 定时与数据保存

- 按 `Asia/Shanghai` 计算当天日期和推送时间，不依赖服务器时区；每 20 秒检查一次，到点后读取课表并发送，学校网络延迟会影响实际到达时间。
- 订阅和投递记录保存在 `data/plugin_data/astrbot_plugin_nuist_course/subscriptions.json`，重启和插件重载后保留。同一会话每天最多正常推送一次；已推送后再次修改时间，从次日起执行。
- 设置已过的时间从明天开始。已订阅后若机器人离线，当天恢复且已到时会补发当天课表；不补发以往日期。
- 学校实时接口失败时，只使用有效期内的同学期缓存并标记“缓存”；无有效缓存时发送获取失败提醒。缓存不能反映同步后发生的临时调课。
- AstrBot 找不到会话对应的平台时，每分钟重试；适配器报错或发送超时则记录结果未知，当天不自动重发，避免重复。需要立即查看时发送 `/查看课表`。投递成功表示适配器调用完成，不代表终端已读。
- 禁用或重载插件会取消推送和登录保活两个后台任务；重载后均会重新启动。请保持 AstrBot 运行、平台连接有效，并持久化整个 `data` 目录。

学校会话包含 Cookie，请仅保存于自己的电脑或服务器。发行包不包含 `.venv`、`config.json`、`state` 或用户数据。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pip install ".[login,test]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖课表解析、缓存与调休、人工登录助手的本机浏览器回归，以及会话订阅、定时、重启去重、北京时间跨日、异常投递、插件命令、会话保活和 SSO 恢复。插件边界测试使用公开接口的模拟对象，未连接真实 AstrBot 或聊天平台，详见 [验证记录](docs/verification.md)。

修改源码后，运行 `.\.venv\Scripts\python.exe tools/package_plugin.py` 重新生成 `dist/astrbot_plugin_nuist_course.zip`。打包器只收录明确列出的公开源码与文档。
