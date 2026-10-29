"""Shared help text and Beijing date parsing for AstrBot function tools."""

import re
from datetime import date, timedelta

from .errors import CourseError
from .storage import TZ


HELP_TEXT = """南信大课表插件 · 指令帮助

课表与推送（北京时间）
/帮助 或 /课表帮助：查看本插件全部指令
/查看课表：立即查看今天的课表
/设置时间 8：每天 08:00 向当前私聊或群聊推送课表
/设置时间 08:30：每天 08:30 推送
/设置时间：按插件默认时间开启推送（初始为 07:00）
/取消推送：停止当前会话的每日推送

学校登录（仅限 AstrBot 管理员私聊）
/绑定课表登录：绑定图片、短信验证码接收会话
/课表登录：开始或重试学校登录
/课表验证码 编号 验证码：提交本次验证码
/课表登录状态：查看自动登录和待验证状态
/取消课表登录：取消正在进行的学校登录

自然语言用法（需 AstrBot 已启用支持工具调用的大模型）
“帮我看看今天的课表，看看有没有在一栋教学楼的”
“查一下明天的课，下午几点结束？”
“每天早上八点半给我推送课表”
“取消课表推送”
群聊请按 AstrBot 配置 @机器人或使用唤醒前缀。
一个插件实例共用一个学校账号和同一份课表。"""


def resolve_date(value, moment):
    """Resolve relative days against Beijing time, never the server's local date."""
    if not isinstance(value, str):
        raise CourseError("查询日期请使用今天、明天、后天、昨天、前天或 YYYY-MM-DD。")
    value = value.strip()
    offsets = {"": 0, "今天": 0, "今日": 0, "明天": 1, "明日": 1,
               "后天": 2, "昨天": -1, "昨日": -1, "前天": -2}
    if value in offsets:
        return moment.astimezone(TZ).date() + timedelta(days=offsets[value])
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise CourseError("查询日期无效，请使用今天、明天、后天、昨天、前天或有效的 YYYY-MM-DD 日期。") from None
