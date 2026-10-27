import asyncio
import json
from contextlib import suppress

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .coursebot.errors import CourseError
from .coursebot.notifier import CourseNotifier, parse_time
from .coursebot.session_keeper import SessionKeeper
from .coursebot.storage import config_values

PLUGIN_NAME = "astrbot_plugin_nuist_course"


@register(PLUGIN_NAME, "NUIST Course Notifier", "南信大每日课表推送", "2.1.0")
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
        self.notifier = CourseNotifier(cfg, data_dir, self._send, logger=logger)
        self.session_keeper = SessionKeeper(cfg, self.notifier.maintain_login, logger=logger)
        self._task = None
        self._keepalive_task = None

    async def _send(self, origin, message):
        return await self.context.send_message(origin, MessageChain().message(message))

    async def initialize(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_scheduler(), name=PLUGIN_NAME + ":daily")
        if self.session_keeper.interval and (self._keepalive_task is None or self._keepalive_task.done()):
            self._keepalive_task = asyncio.create_task(self._run_keepalive(), name=PLUGIN_NAME + ":keepalive")

    async def _run_keepalive(self):
        while True:
            try:
                await self.session_keeper.tick()
            except Exception as error:
                logger.error("保存登录保活状态失败（%s），请检查文件权限。", type(error).__name__)
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
        tasks = [task for task in (self._task, self._keepalive_task) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._task = self._keepalive_task = None

    @filter.command("登录状态")
    async def login_status(self, event: AstrMessageEvent):
        """查看自动保活状态与最近一次真实接口验证时间。"""
        yield event.plain_result(self.session_keeper.describe())
        event.stop_event()

    @filter.command("设置时间")
    async def set_time(self, event: AstrMessageEvent, time: str = ""):
        """设置当前会话每天接收课表的时间，如 /设置时间 8 或 /设置时间 08:30。"""
        try:
            next_run = await self.notifier.set_time(event.unified_msg_origin, time or self.default_time)
            text = (f"已为当前会话开启每日课表推送，时间为北京时间 {next_run:%H:%M}。\n"
                    f"下次推送：{next_run:%Y-%m-%d %H:%M}。\n"
                    "发送 /查看课表 立即查看今天课表；/取消推送 停止本会话推送。")
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
            removed = await self.notifier.cancel(event.unified_msg_origin)
            text = "已取消当前会话的每日课表推送。" if removed else "当前会话尚未开启每日课表推送。"
        except Exception as error:
            logger.error("取消课表推送失败（%s）。", type(error).__name__)
            text = "取消设置未能保存，请管理员检查插件数据目录的写入权限。"
        yield event.plain_result(text)
        event.stop_event()
