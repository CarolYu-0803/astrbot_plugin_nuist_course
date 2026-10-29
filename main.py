import asyncio
import base64
import json
from contextlib import suppress

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .coursebot.errors import CourseError
from .coursebot.llm import HELP_TEXT, resolve_date
from .coursebot.notifier import CourseNotifier, parse_time
from .coursebot.interaction import LoginInteraction
from .coursebot.recovery import LoginRecovery
from .coursebot.storage import TZ, config_values

PLUGIN_NAME = "astrbot_plugin_nuist_course"


@register(PLUGIN_NAME, "NUIST Course Notifier", "南信大课表查询、自然语言工具与每日推送", "2.2.0")
class NuistCoursePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        options = dict(config or {})
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        overrides = options.get("date_overrides", "{}")
        if isinstance(overrides, str):
            try:
                overrides = json.loads(overrides)
            except ValueError:
                raise CourseError('调休配置必须是 JSON 对象，例如 {"2026-10-31": "2026-10-27"}。') from None
        self.default_time = parse_time(options.get("default_push_time", "07:00"))
        cfg = config_values({**options, "date_overrides": overrides}, data_dir)
        self.interaction = LoginInteraction(data_dir, self._send_login, cfg["login_code_timeout_seconds"])
        self.login = LoginRecovery(cfg, self.interaction, logger=logger)
        self.notifier = CourseNotifier(cfg, data_dir, self._send, logger=logger,
                                       recover=self.login.refresh if self.login.enabled else None)
        self._task = None
        self._login_task = None

    async def _send(self, origin, message):
        return await self.context.send_message(origin, MessageChain().message(message))

    async def _send_login(self, origin, message, image=None):
        chain = MessageChain().message(message)
        if image is not None:
            chain.base64_image(base64.b64encode(image).decode("ascii"))
        return await self.context.send_message(origin, chain)

    async def initialize(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_scheduler(), name=PLUGIN_NAME + ":daily")
        if self._login_task is None or self._login_task.done():
            self._login_task = asyncio.create_task(self._run_login_checks(), name=PLUGIN_NAME + ":login-check")

    async def _run_login_checks(self):
        while True:
            try:
                await self.login.maintain()
            except CourseError as error:
                logger.warning("学校登录检查：%s", error)
            except Exception as error:
                logger.error("学校登录检查异常（%s）。", type(error).__name__)
            await asyncio.sleep(20)

    async def _run_scheduler(self):
        while True:
            try:
                await self.notifier.tick()
            except CourseError as error:
                logger.error("课表定时任务：%s", error)
            except Exception as error:
                logger.error("课表定时任务异常（%s），请检查配置和文件权限。", type(error).__name__)
            await asyncio.sleep(20)

    async def terminate(self):
        for task in (self._task, self._login_task):
            if task is not None:
                task.cancel()
        await self.login.close()
        for task in (self._task, self._login_task):
            if task is None:
                continue
            with suppress(asyncio.CancelledError):
                await task
        self._task = self._login_task = None

    def _login_admin(self, event, *, bound=True):
        if not event.is_admin() or not event.is_private_chat():
            raise CourseError("请 AstrBot 管理员在私聊中操作学校登录。")
        if bound and not self.interaction.owns(event.unified_msg_origin, event.get_sender_id()):
            raise CourseError("请先在当前管理员私聊发送 /绑定课表登录。")

    @filter.command("帮助", alias={"课表帮助"})
    async def help(self, event: AstrMessageEvent):
        """查看本插件所有命令及自然语言用法。"""
        yield event.plain_result(HELP_TEXT)
        event.stop_event()

    async def _set_time_text(self, origin, value):
        next_run = await self.notifier.set_time(origin, value or self.default_time)
        return (f"已为当前会话开启每日课表推送，时间为北京时间 {next_run:%H:%M}。\n"
                f"下次推送：{next_run:%Y-%m-%d %H:%M}。\n"
                "发送 /查看课表 立即查看今天课表；/取消推送 停止本会话推送。")

    async def _cancel_push_text(self, origin):
        removed = await self.notifier.cancel(origin)
        return "已取消当前会话的每日课表推送。" if removed else "当前会话尚未开启每日课表推送。"

    async def _tool_result(self, operation, label):
        """Return results to AstrBot's LLM loop instead of ending the chat event."""
        try:
            result = {"ok": True, **await operation()}
        except CourseError as error:
            result = {"ok": False, "error": str(error)}
        except Exception as error:
            logger.error("课表工具%s失败（%s）。", label, type(error).__name__)
            result = {"ok": False, "error": f"{label}失败，请管理员检查配置、学校会话和文件权限；查询失败不代表没有课。"}
        return json.dumps(result, ensure_ascii=False)

    @filter.llm_tool(name="nuist_course_query")
    async def query_course_tool(self, event: AstrMessageEvent, target_date: str = "今天") -> str:
        """查询南信大真实课表，供回答上课时间、地点、教师、空闲时间及同楼课程等问题。

        用户询问课表或要求分析课程时，应先调用本工具，再根据返回的数据回答。
        相对日期以北京时间计算，查询今天请直接传“今天”，无需猜测当前日期。
        返回课程原始 room、时间、节次、理论/实验类型，以及校历、调休、缓存来源和未排课程。
        判断是否在同一栋教学楼时比较原始地点中的校区和楼名，列出相关课程和时间；
        不得将不同教室直接视为不同楼，也不得因房间号相似就断定同楼。
        地点缺失或楼名不明确时应说明无法确认；区分“一号教学楼”与“同一栋教学楼”。
        必须说明缓存警告；ok=false 时说明查询失败，不能断言无课；未排课程不代表当天上课。

        Args:
            target_date(string): 查询日期，今天、明天、后天、昨天、前天或 YYYY-MM-DD；不填为今天。
        """
        async def query():
            moment = self.notifier.clock().astimezone(TZ)
            target = resolve_date(target_date, moment)
            timetable = await self.notifier.timetable(target)
            return {**timetable, "current_time": moment.isoformat()}

        return await self._tool_result(query, "查询课表")

    @filter.llm_tool(name="nuist_course_set_push_time")
    async def set_push_time_tool(self, event: AstrMessageEvent, time: str = "") -> str:
        """按用户要求开启或修改当前会话的每日课表推送时间，时区为北京时间。

        仅在用户要求设置或更改推送时调用。群聊设置对当前群共用，私聊设置仅作用于当前私聊。
        返回保存后的实际时间和下次推送日期，只有 ok=true 才能告知设置成功。

        Args:
            time(string): 每日推送时间，如 8 或 08:30；请将自然语言时间转换为 24 小时制。留空使用插件默认时间。
        """
        async def update():
            return {"message": await self._set_time_text(event.unified_msg_origin, time)}

        return await self._tool_result(update, "设置推送")

    @filter.llm_tool(name="nuist_course_cancel_push")
    async def cancel_push_tool(self, event: AstrMessageEvent) -> str:
        """仅在用户要求取消课表推送时调用，取消当前私聊或当前群聊的每日课表推送。

        群聊操作对当前群共用。用户仍可随时查询课表，其他会话的订阅不受影响。
        """
        async def cancel():
            return {"message": await self._cancel_push_text(event.unified_msg_origin)}

        return await self._tool_result(cancel, "取消推送")

    @filter.llm_tool(name="nuist_course_help")
    async def course_help_tool(self, event: AstrMessageEvent) -> str:
        """查看南信大课表插件全部指令、自然语言用法及学校登录的管理员私聊要求。"""
        return json.dumps({"ok": True, "help": HELP_TEXT}, ensure_ascii=False)

    @filter.command("绑定课表登录")
    async def bind_login(self, event: AstrMessageEvent):
        """管理员私聊绑定图片/短信验证码接收会话。"""
        try:
            self._login_admin(event, bound=False)
            self.interaction.bind(event.unified_msg_origin, event.get_sender_id())
            text = "已将学校登录验证绑定到当前管理员私聊。开启自动登录并配置账号密码后，发送 /课表登录 开始。"
        except CourseError as error:
            text = str(error)
        except Exception as error:
            logger.error("学校登录绑定失败（%s）。", type(error).__name__)
            text = "绑定保存失败，请检查插件数据目录的写入权限。"
        yield event.plain_result(text)
        event.stop_event()

    @filter.command("课表登录")
    async def school_login(self, event: AstrMessageEvent):
        """启动一次后台登录，验证码通过绑定私聊交互。"""
        try:
            self._login_admin(event)
            self.login.start(manual=True)
            text = "学校登录已在后台开始；如需图片或短信验证码，将在当前私聊提示。"
        except CourseError as error:
            text = str(error)
        yield event.plain_result(text)
        event.stop_event()

    @filter.command("课表验证码")
    async def login_code(self, event: AstrMessageEvent, token: str = "", code: str = ""):
        """/课表验证码 验证编号 验证码。"""
        try:
            self._login_admin(event)
            self.interaction.submit(event.unified_msg_origin, event.get_sender_id(), token, code)
            text = "验证码已提交，正在继续登录。"
        except CourseError as error:
            text = str(error)
        yield event.plain_result(text)
        event.stop_event()

    @filter.command("取消课表登录")
    async def cancel_login(self, event: AstrMessageEvent):
        try:
            self._login_admin(event)
            active = await self.login.cancel()
            text = "已取消本次学校登录。" if active else "当前没有进行中的学校登录。"
        except CourseError as error:
            text = str(error)
        yield event.plain_result(text)
        event.stop_event()

    @filter.command("课表登录状态")
    async def login_status(self, event: AstrMessageEvent):
        try:
            self._login_admin(event)
            text = ("自动登录：" + ("已开启" if self.login.enabled else "未开启") + "。\n" + self.login.last_status)
            if self.interaction.pending:
                text += "\n正在等待" + ("图片验证码" if self.interaction.pending.kind == "image" else "手机短信验证码")
        except CourseError as error:
            text = str(error)
        yield event.plain_result(text)
        event.stop_event()

    @filter.command("设置时间")
    async def set_time(self, event: AstrMessageEvent, time: str = ""):
        """设置当前会话每天接收课表的时间，如 /设置时间 8 或 /设置时间 08:30。"""
        try:
            text = await self._set_time_text(event.unified_msg_origin, time)
        except CourseError as error:
            text = str(error)
        except Exception as error:
            logger.error("课表时间设置失败（%s）。", type(error).__name__)
            text = "推送设置未能保存，请管理员检查插件数据目录的写入权限。"
        yield event.plain_result(text)
        event.stop_event()

    @filter.command("查看课表")
    async def view_timetable(self, event: AstrMessageEvent):
        """立即查看北京时间当天课表，不改变每日订阅或发送记录。"""
        try:
            text = await self.notifier.view()
        except CourseError as error:
            text = str(error)
        except Exception as error:
            logger.error("课表查询失败（%s）。", type(error).__name__)
            text = "课表查询失败，这不代表今天没课。请管理员检查配置、学校会话和文件权限。"
        yield event.plain_result(text)
        event.stop_event()

    @filter.command("取消推送")
    async def cancel_push(self, event: AstrMessageEvent):
        """取消当前会话的每日推送，仍可随时查看课表。"""
        try:
            text = await self._cancel_push_text(event.unified_msg_origin)
        except Exception as error:
            logger.error("取消课表推送失败（%s）。", type(error).__name__)
            text = "取消设置未能保存，请管理员检查插件数据目录的写入权限。"
        yield event.plain_result(text)
        event.stop_event()
