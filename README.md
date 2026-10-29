# 南信大课表 · AstrBot 插件

获取南京信息工程大学教务系统的理实一体课表，通过 AstrBot 所连接的平台发送当日课程。支持理论课、实验课、教学周、调休映射、未排课提醒，以及登录失效后的有限期缓存。

要求 **AstrBot ≥ 4.5.0**，平台适配器须支持 `Context.send_message` 主动消息。学校网络查询在后台线程执行，不阻塞机器人处理其他消息。Server酱通知渠道、SendKey 配置和独立 systemd 推送任务已移除。

**2.2.0：** 新增大模型工具调用，支持用自然语言查询、分析课表，以及设置、取消每日推送；新增 `/帮助`（别名 `/课表帮助`）列出全部指令。沿用 2.1.0 的服务端自动登录、验证码私聊交互和缓存机制。

## 聊天命令

| 命令 | 功能 |
|---|---|
| `/帮助` 或 `/课表帮助` | 查看本插件所有指令、权限说明和自然语言示例 |
| `/设置时间 8` | 开启当前会话每天北京时间 **08:00** 的课表推送 |
| `/设置时间 08:30` | 改为每天 **08:30**，小时范围 0–23 |
| `/设置时间` | 按插件配置的默认时间开启推送，初始为 **07:00** |
| `/查看课表` | 立即查询北京时间当天课表，不影响定时推送 |
| `/取消推送` | 停止当前会话的每日推送 |
| `/绑定课表登录` | 管理员私聊绑定图片、短信验证码接收会话 |
| `/课表登录` | 在后台开始或重试学校登录 |
| `/课表验证码 编号 验证码` | 回复机器人要求的本次图片或短信验证码 |
| `/课表登录状态` | 查看自动登录和待验证状态 |
| `/取消课表登录` | 结束本次登录，保留已有文件 |

首次使用时，在接收课表的私聊或群聊中发送 `/设置时间 8` 即完成订阅；命令会回复下次推送日期。各私聊、各群分别保存时间，在群内设置会将课表推送到该群；同一群的设置由该群共用。请确保 AstrBot 的唤醒前缀包含 `/`；群聊如要求唤醒，可先 @机器人再发命令。

**一个插件实例使用一套学校登录会话，所有订阅者查看同一份课表。** 这是原有单账号程序的延续，不是多学生账号绑定系统。

## 自然语言查询与课表分析

插件复用 **AstrBot 已配置的大模型**，通过原生 `filter.llm_tool` 注册工具。无需为插件另填模型 API Key，也不需要安装另一套大模型 SDK。请先在 AstrBot 中启用 LLM 聊天，并选择支持 **工具调用 / Function Calling** 的模型及接口；在工具管理或当前会话所用配置中，确保下列 `nuist_course_` 工具可用。修改插件后重载，或安装本版 ZIP 后启用插件。

可以直接对机器人说：

> 帮我看看今天的课表，看看有没有在一栋教学楼的。
>
> 查一下明天的课，下午几点结束？
>
> 每天早上八点半给我推送课表。
>
> 取消课表推送。

群聊仍按 AstrBot 的唤醒设置 @机器人或添加前缀。模型调用查询工具后，插件返回真实课程的名称、时间、节次、原始地点、教师及理论/实验类型，再由模型分析并回复。例如，同楼判断可比较“明德楼 N101”和“明德楼 N203”；缺少地点、校区或楼名无法确定时，应说明无法确认。“一号教学楼”和“同一栋教学楼”也应按用户实际含义区分。

| 工具名 | 功能 |
|---|---|
| `nuist_course_query` | 查询今天、明天、后天、昨天、前天或 `YYYY-MM-DD` 的课表，返回结构化数据供模型分析 |
| `nuist_course_set_push_time` | 开启或修改当前会话的每日推送，与 `/设置时间` 共用保存逻辑 |
| `nuist_course_cancel_push` | 取消当前会话的每日推送，与 `/取消推送` 共用保存逻辑 |
| `nuist_course_help` | 返回本插件全部指令和使用说明 |

相对日期和推送时间都按北京时间计算。查询不改变订阅或投递记录；群聊设置对当前群共用。使用缓存时工具会返回 `source=cache`、同步时间和警告，模型应在回复中说明。查询失败返回 `ok=false`，不能据此断言没有课；未排课程也不能当作当天安排。查询范围仍受已同步学期限制。

学校登录与验证码继续使用管理员私聊指令。自然语言调用需要模型实际发起工具请求；若模型只回答文字而不查课表，请检查工具是否开启、模型和接入服务是否支持工具调用。可先用 `/查看课表` 核对学校数据，用 `/课表帮助` 查看插件说明；后者也适用于与其他插件的 `/帮助` 重名时。

## 安装到 AstrBot

1. 将发行包 `astrbot_plugin_nuist_course.zip` 上传到 AstrBot 管理面板的插件安装入口；或将源码放入 `AstrBot/data/plugins/astrbot_plugin_nuist_course/`。插件目录下应直接包含 `main.py`、`metadata.yaml`、`_conf_schema.json`、`requirements.txt` 和 `coursebot/`。
2. 让 AstrBot 安装 `requirements.txt` 中的依赖；如需手工安装，使用 **AstrBot 自己的 Python 环境**执行 `python -m pip install -r requirements.txt`。
3. 启用插件，按下文配置自动登录，或导入人工登录助手生成的学校会话。在聊天中发送 `/查看课表` 核对内容，再发送 `/设置时间 8` 开启每日推送。

自动登录需要在 **AstrBot 所在服务器或容器**安装 Playwright 的浏览器；关闭自动登录时，课表查询仍不需要启动浏览器。不要为运行插件单独安装另一个 AstrBot。

## 自动登录与验证码交互（推荐）

1. 更新插件，并在 **AstrBot 自己的 Python 环境**安装浏览器：

   ```bash
   python -m pip install -r data/plugins/astrbot_plugin_nuist_course/requirements.txt
   python -m playwright install --with-deps chromium
   ```

   Windows 可使用 `python -m playwright install chromium`。Docker 中须在运行 AstrBot 的容器内执行；重建镜像后要确保浏览器及系统依赖仍存在。

2. 在插件配置开启 `auto_login_enabled`，填写 `login_username`、`login_password` 并重载插件。也可通过服务器环境变量 `NUIST_COURSE_USERNAME`、`NUIST_COURSE_PASSWORD` 提供，环境变量优先；密码项可留空。环境变量改动需要重启 AstrBot。
3. 将自己的聊天账号设为 **AstrBot 管理员**，然后**私聊**机器人发送 `/绑定课表登录`，再发送 `/课表登录`。
4. 机器人发来图片验证码时，按提示回复 `/课表验证码 本次编号 图片字符`；新设备需要手机验证时，机器人会请求学校发送短信，按同样格式回复手机收到的数字验证码。
5. 完成后机器人提示同步成功，服务器自动保存 `session.json`、`snapshot.json` 和 `browser_state.json`。以后查询或定时检查发现会话失效，就会重新登录；需要验证时仍在绑定私聊提示，无需每天上传文件。

默认每小时检查并同步一次学校会话，每次验证码等待 180 秒；自动登录失败后至少间隔 30 分钟再尝试。账号或密码被学校拒绝时暂停自动尝试，检查配置后发送 `/课表登录` 恢复。成功的短信验证会选择学校提供的“信任此设备”并保存浏览器状态；学校仍可能因设备、IP 或策略变化再次要求短信。

登录命令仅限管理员私聊，验证码还会核对绑定会话、发送者、本次验证编号及有效期。账号密码只在面板或环境变量中设置，机器人不会要求在聊天中发送密码。滑块、扫码等未适配验证仍可使用人工助手；具体配置和 Docker 说明见 [自动登录说明](docs/auto-login.md)。

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

## 人工登录助手（备用）

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

`sync` 用于确认实时接口；`preview` 仅输出内容。如果使用缓存，输出会明确标记，不能据此认定实时连接成功。关闭自动登录且异机部署时，需将更新后的两个 JSON 文件重新复制到插件实际使用的 `state_dir`。启用自动登录后，续期在 AstrBot 所在机器完成。

## 插件配置

| 配置项 | 默认值 | 用途 |
|---|---|---|
| `state_dir` | 空白 | 使用插件持久化数据目录下的 `state/`；也可填写已有目录 |
| `default_push_time` | `07:00` | `/设置时间` 不带参数时使用的时间；不覆盖已有订阅 |
| `cache_max_age_days` | `7` | 学校读取失败时备用课表的最长天数，0 关闭 |
| `request_timeout_seconds` | `25` | 学校接口单次请求超时，单位秒 |
| `date_overrides` | `{}` | 调休映射的 JSON 文本 |
| `auto_login_enabled` | `false` | 开启服务端登录与会话检查，须先绑定管理员私聊 |
| `login_username` / `login_password` | 空白 | 统一身份认证账号密码；同名 `NUIST_COURSE_…` 环境变量优先 |
| `login_code_timeout_seconds` | `180` | 每次等待验证码 60–600 秒 |
| `login_retry_minutes` | `30` | 自动登录重试最短间隔，5–1440 分钟 |
| `session_check_minutes` | `60` | 后台会话检查与同步间隔，5–1440 分钟 |
| `login_browser_channel` | 空白 | 默认 Chromium；可选已安装的 `chrome` / `msedge` |

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
- 禁用或重载插件会取消后台定时任务、登录和待回复验证码。请保持 AstrBot 运行、平台连接有效，并持久化整个 `data` 目录。

学校会话及浏览器状态包含 Cookie 和可信设备信息，请仅保存于自己的电脑或服务器。面板填写的密码会存入 AstrBot 插件配置，应限制该配置及环境变量的访问权限。发行包不包含 `.venv`、`config.json`、`state` 或用户数据。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pip install ".[login,test]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

本版测试覆盖结构化课表、北京时间日期、原始教室和缺失地点、调休与未排课、缓存回退和失效、登录恢复、文件锁、后台线程、会话独立推送、工具错误与日志脱敏、帮助完整性及登录权限。AstrBot 边界使用模拟对象，未连接真实大模型或聊天平台；此前学校登录和同步的历史验证边界见 [验证记录](docs/verification.md)。

修改源码后，运行 `.\.venv\Scripts\python.exe tools/package_plugin.py` 重新生成 `dist/astrbot_plugin_nuist_course.zip`。打包器只收录明确列出的公开源码与文档。
