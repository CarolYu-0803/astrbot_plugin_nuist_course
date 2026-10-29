import asyncio
import importlib.util
import json
import logging
import sys
import threading
import types
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from filelock import FileLock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coursebot import notifier, runner
from coursebot.errors import CourseError, DataError, LoginExpired
from coursebot.llm import HELP_TEXT, resolve_date
from coursebot.storage import TZ, config_values, write_json

MOMENT = datetime(2026, 10, 29, 9, 0, tzinfo=TZ)
TARGET = MOMENT.date()


def snapshot():
    def course(name, room, begin, end, weekday=4, kind="理论"):
        return {"name": name, "teacher": "测试教师", "room": room, "weekday": weekday,
                "begin": begin, "end": end, "weeks": [9], "start_time": "", "end_time": "",
                "kind": kind, "adjusted": False}

    return {
        "version": 1, "term": "2026-2027-1", "start_date": "2026-08-31", "total_weeks": 20,
        "fetched_at": MOMENT.isoformat(),
        "calendar_check": {"date": TARGET.isoformat(), "week": 9, "weekday": 4},
        "periods": {"1": {"start": "08:00", "end": "08:45"},
                    "2": {"start": "08:55", "end": "09:40"},
                    "3": {"start": "10:10", "end": "10:55"},
                    "4": {"start": "11:05", "end": "11:50"},
                    "5": {"start": "14:00", "end": "14:45"},
                    "6": {"start": "14:55", "end": "15:40"}},
        "courses": [course("高等数学", "东校区明德楼 N101", 1, 2),
                    course("大学英语", "东校区明德楼 N203", 3, 4),
                    course("物理实验", "东校区实验楼 101", 5, 6, kind="实验"),
                    course("地点未定", "", 5, 6),
                    course("周五课程", "西校区明德楼 N101", 1, 2, weekday=5)],
        "unplaced": ["尚未排定的实习"],
    }


@pytest.fixture
def cfg(tmp_path):
    return config_values({}, tmp_path)


@pytest.fixture
def live(monkeypatch):
    def fetch(cfg, target):
        data = snapshot()
        data["calendar_check"] = {"date": target.isoformat(), "week": (target - date(2026, 8, 31)).days // 7 + 1,
                                  "weekday": target.isoweekday()}
        return data

    monkeypatch.setattr(runner, "sync", fetch)
    monkeypatch.setattr(runner, "now", lambda: MOMENT)


@pytest.mark.parametrize("value, offset", [("", 0), ("今天", 0), ("今日", 0), ("明天", 1),
                                         ("明日", 1), ("后天", 2), ("昨天", -1), ("前天", -2)])
def test_relative_dates_use_beijing(value, offset):
    utc = datetime(2026, 10, 28, 16, 30, tzinfo=timezone.utc)
    assert resolve_date(value, utc) == date(2026, 10, 29) + timedelta(days=offset)


def test_explicit_date_and_whitespace():
    assert resolve_date(" 2026-11-01 ", MOMENT) == date(2026, 11, 1)


@pytest.mark.parametrize("value", [None, 20261029, "2026-02-30", "2026-1-2", "20261029", "随便哪天"])
def test_invalid_date_is_rejected(value):
    with pytest.raises(CourseError, match="日期"):
        resolve_date(value, MOMENT)


def test_structured_data_preserves_rooms_missing_locations_and_unplaced(cfg, live):
    result = runner.prepare_timetable(cfg, TARGET)
    assert result["source"] == "live" and result["warning"] == ""
    assert result["date"] == "2026-10-29" and result["timezone"] == "Asia/Shanghai"
    by_name = {c["name"]: c for c in result["courses"]}
    assert {name: c["room"] for name, c in by_name.items()} == {
        "高等数学": "东校区明德楼 N101", "大学英语": "东校区明德楼 N203",
        "物理实验": "东校区实验楼 101", "地点未定": "",
    }
    assert result["courses"][0]["start_time"] == "08:00"
    assert by_name["物理实验"]["kind"] == "实验"
    assert result["unplaced"] == ["尚未排定的实习"]
    assert "不能据此认定当天上课" in result["notice"]


def test_existing_plain_text_output_is_preserved(cfg, live):
    title, body, source = runner.prepare(cfg, TARGET)
    assert title == "10月29日课表｜4项安排"
    assert source == "live" and "第 9 周 · 星期四" in body
    assert "08:00–09:40　高等数学\n地点：东校区明德楼 N101" in body
    assert "地点：教务系统未注明" in body
    assert "未排入课表的课程" in body and "数据同步：2026-10-29 09:00" in body
    assert runner.build_message(cfg, TARGET) == title + "\n\n" + body


def test_query_other_date_and_adjusted_calendar(cfg, live):
    friday = runner.prepare_timetable(cfg, date(2026, 10, 30))
    assert [c["name"] for c in friday["courses"]] == ["周五课程"]
    cfg["date_overrides"] = {"2026-10-29": "2026-10-30"}
    adjusted = runner.prepare_timetable(cfg, TARGET)
    assert adjusted["courses"] == friday["courses"]
    assert "调休：按 2026-10-30 上课" in adjusted["calendar_note"]


def test_no_classes_is_distinct_from_failed_query(cfg, live):
    cfg["date_overrides"] = {TARGET.isoformat(): None}
    result = runner.prepare_timetable(cfg, TARGET)
    assert result["courses"] == [] and result["calendar_note"] == "手动设置为停课日"


def test_live_calendar_mismatch_is_not_reported_as_no_classes(cfg, monkeypatch):
    data = snapshot()
    data["calendar_check"]["date"] = "2026-10-28"
    monkeypatch.setattr(runner, "sync", lambda *args: data)
    with pytest.raises(DataError, match="实时校历"):
        runner.prepare_timetable(cfg, TARGET)


def test_cache_keeps_source_and_warning(cfg, monkeypatch):
    write_json(cfg["state_dir"] / "snapshot.json", snapshot())
    monkeypatch.setattr(runner, "now", lambda: MOMENT)

    def fail(*args):
        raise LoginExpired("学校登录已失效")

    monkeypatch.setattr(runner, "sync", fail)
    result = runner.prepare_timetable(cfg, TARGET)
    assert result["source"] == "cache" and "学校登录已失效" in result["warning"]
    assert result["fetched_at"] == MOMENT.isoformat() and len(result["courses"]) == 4
    assert runner.prepare(cfg, TARGET)[0].startswith("【缓存】")


@pytest.mark.parametrize("case", ["missing", "expired", "disabled", "outside_term"])
def test_invalid_cache_never_returns_empty_timetable(cfg, monkeypatch, case):
    data = snapshot()
    if case == "expired":
        data["fetched_at"] = (MOMENT - timedelta(days=8)).isoformat()
    if case == "disabled":
        cfg["cache_max_age_days"] = 0
    if case != "missing":
        write_json(cfg["state_dir"] / "snapshot.json", data)
    monkeypatch.setattr(runner, "now", lambda: MOMENT)

    def fail(*args):
        raise LoginExpired("学校登录已失效")

    monkeypatch.setattr(runner, "sync", fail)
    target = date(2027, 6, 1) if case == "outside_term" else TARGET
    service = notifier.CourseNotifier(cfg, cfg["state_dir"].parent, AsyncMock(), clock=lambda: MOMENT)
    with pytest.raises(CourseError):
        asyncio.run(service.timetable(target))


def test_structured_reads_are_threaded_and_do_not_change_subscriptions(cfg, monkeypatch):
    main_thread = threading.get_ident()
    fetch_threads = []

    def fetch(*args):
        fetch_threads.append(threading.get_ident())
        return snapshot()

    monkeypatch.setattr(runner, "sync", fetch)
    sender = AsyncMock()
    service = notifier.CourseNotifier(cfg, cfg["state_dir"].parent, sender, clock=lambda: MOMENT)

    async def check():
        await service.set_time("chat-a", "10")
        before = service.path.read_bytes()
        result = await service.timetable()
        assert len(result["courses"]) == 4
        assert service.path.read_bytes() == before

    asyncio.run(check())
    assert fetch_threads and all(t != main_thread for t in fetch_threads)
    sender.assert_not_awaited()


def test_structured_query_recovers_login_before_reading_cache(cfg, monkeypatch):
    calls = []

    def fetch(*args):
        calls.append("fetch")
        if calls == ["fetch"]:
            raise LoginExpired("expired")
        return snapshot()

    async def recover():
        # Recovery must run after releasing the file lock.
        with FileLock(str(cfg["state_dir"] / "run.lock"), timeout=0):
            calls.append("recover")

    monkeypatch.setattr(runner, "sync", fetch)
    service = notifier.CourseNotifier(cfg, cfg["state_dir"].parent, AsyncMock(), recover=recover)
    result = asyncio.run(service.timetable(TARGET))
    assert calls == ["fetch", "recover", "fetch"] and result["source"] == "live"


def test_failed_recovery_uses_valid_cache(cfg, monkeypatch):
    write_json(cfg["state_dir"] / "snapshot.json", snapshot())
    monkeypatch.setattr(runner, "now", lambda: MOMENT)

    def fail(*args):
        raise LoginExpired("expired")

    monkeypatch.setattr(runner, "sync", fail)
    service = notifier.CourseNotifier(cfg, cfg["state_dir"].parent, AsyncMock(),
                                      recover=AsyncMock(side_effect=CourseError("等待验证码")))
    result = asyncio.run(service.timetable(TARGET))
    assert result["source"] == "cache" and "expired" in result["warning"]


def test_sync_lock_conflict_is_a_query_error(cfg):
    service = notifier.CourseNotifier(cfg, cfg["state_dir"].parent, AsyncMock())
    with FileLock(str(cfg["state_dir"] / "run.lock"), timeout=0):
        with pytest.raises(CourseError, match="正在被登录或同步助手更新"):
            asyncio.run(service.timetable(TARGET))


class Event:
    def __init__(self, origin="platform:GroupMessage:group-a", admin=False, private=False):
        self.unified_msg_origin = origin
        self.stopped = False
        self.admin = admin
        self.private = private

    def plain_result(self, text):
        return text

    def stop_event(self):
        self.stopped = True

    def is_admin(self):
        return self.admin

    def is_private_chat(self):
        return self.private

    def get_sender_id(self):
        return "user-a"


@pytest.fixture
def plugin_module(monkeypatch, tmp_path):
    """Mock AstrBot boundaries; real plugin, notifier, storage and date logic still run."""
    def command(name, alias=None):
        def decorate(handler):
            handler.command_name = name
            handler.command_aliases = alias or set()
            return handler
        return decorate

    def tool(name):
        def decorate(handler):
            handler.tool_name = name
            return handler
        return decorate

    class Star:
        def __init__(self, context):
            self.context = context

    for name in ("astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    api = sys.modules["astrbot.api"]
    api.AstrBotConfig = dict
    api.logger = logging.getLogger("nuist-plugin-test")
    event_api = sys.modules["astrbot.api.event"]
    event_api.AstrMessageEvent = Event
    event_api.MessageChain = object
    event_api.filter = types.SimpleNamespace(command=command, llm_tool=tool)
    star_api = sys.modules["astrbot.api.star"]
    star_api.Context = object
    star_api.Star = Star
    star_api.StarTools = types.SimpleNamespace(get_data_dir=lambda name: tmp_path)
    star_api.register = lambda *args: lambda cls: cls
    package_name = "nuist_plugin_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    monkeypatch.setitem(sys.modules, package_name, package)
    # Alias the domain modules so pytest patches the same code used by the plugin.
    for name, module in list(sys.modules.items()):
        if name == "coursebot" or name.startswith("coursebot."):
            monkeypatch.setitem(sys.modules, f"{package_name}.{name}", module)
    spec = importlib.util.spec_from_file_location(f"{package_name}.main", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin(plugin_module):
    context = types.SimpleNamespace(send_message=AsyncMock())
    plugin = plugin_module.NuistCoursePlugin(context, {"default_push_time": "07:30"})
    plugin.notifier.clock = lambda: MOMENT
    return plugin


def test_query_tool_returns_data_for_llm_and_leaves_event_running(plugin, live):
    event = Event()
    result = json.loads(asyncio.run(plugin.query_course_tool(event)))
    assert result["ok"] is True and len(result["courses"]) == 4
    assert result["current_time"] == MOMENT.isoformat()
    assert result["courses"][0]["room"] == "东校区明德楼 N101"
    assert result["courses"][1]["room"] == "东校区明德楼 N203"
    assert not event.stopped and not plugin.notifier.path.exists()
    plugin.context.send_message.assert_not_awaited()


def test_query_tool_tomorrow_and_invalid_date(plugin, live):
    event = Event()
    result = json.loads(asyncio.run(plugin.query_course_tool(event, "明天")))
    assert result["ok"] and result["date"] == "2026-10-30"
    assert [c["name"] for c in result["courses"]] == ["周五课程"]
    bad = json.loads(asyncio.run(plugin.query_course_tool(event, "2026-02-30")))
    assert bad["ok"] is False and "日期无效" in bad["error"]


def test_query_tool_failure_has_no_success_or_empty_course_list(plugin, monkeypatch):
    def fail(*args):
        raise LoginExpired("尚未保存学校会话")

    monkeypatch.setattr(runner, "sync", fail)
    result = json.loads(asyncio.run(plugin.query_course_tool(Event())))
    assert result == {"ok": False, "error": "尚未保存学校会话"}


def test_query_tool_empty_success_is_different_from_failure(plugin, live):
    plugin.notifier.cfg["date_overrides"] = {TARGET.isoformat(): None}
    result = json.loads(asyncio.run(plugin.query_course_tool(Event())))
    assert result["ok"] is True and result["courses"] == []


def test_push_tools_are_scoped_to_event_and_leave_llm_running(plugin):
    group, private = Event(), Event("platform:FriendMessage:user-a", private=True)

    async def check():
        first = json.loads(await plugin.set_push_time_tool(group, "08:30"))
        second = json.loads(await plugin.set_push_time_tool(private, "10"))
        assert first["ok"] and "2026-10-30 08:30" in first["message"]
        assert second["ok"] and "2026-10-29 10:00" in second["message"]
        before = deepcopy(plugin.notifier._subscriptions[private.unified_msg_origin])
        result = json.loads(await plugin.cancel_push_tool(group))
        assert result["ok"] and "已取消" in result["message"]
        assert group.unified_msg_origin not in plugin.notifier._subscriptions
        assert plugin.notifier._subscriptions[private.unified_msg_origin] == before
        repeated = json.loads(await plugin.cancel_push_tool(group))
        assert repeated["ok"] and "尚未开启" in repeated["message"]

    asyncio.run(check())
    assert not group.stopped and not private.stopped


def test_push_tool_defaults_and_invalid_time(plugin):
    async def check():
        event = Event()
        default = json.loads(await plugin.set_push_time_tool(event))
        assert default["ok"] and "07:30" in default["message"]
        before = plugin.notifier.path.read_bytes()
        invalid = json.loads(await plugin.set_push_time_tool(event, "25:30"))
        assert invalid["ok"] is False and "超出范围" in invalid["error"]
        assert plugin.notifier.path.read_bytes() == before

    asyncio.run(check())


def test_unexpected_tool_errors_are_sanitized(plugin, monkeypatch, caplog):
    def fail(*args):
        raise OSError("secret-cookie-value")

    monkeypatch.setattr(plugin.notifier, "_commit", fail)
    result = json.loads(asyncio.run(plugin.set_push_time_tool(Event(), "10")))
    assert result["ok"] is False and "secret-cookie-value" not in json.dumps(result)
    assert "secret-cookie-value" not in caplog.text
    assert plugin.notifier._subscriptions == {}


def test_help_includes_every_registered_command_and_alias(plugin):
    async def check():
        event = Event()
        generator = plugin.help(event)
        assert await anext(generator) == HELP_TEXT
        assert not event.stopped
        with pytest.raises(StopAsyncIteration):
            await anext(generator)
        assert event.stopped
        tool_event = Event()
        result = json.loads(await plugin.course_help_tool(tool_event))
        assert result == {"ok": True, "help": HELP_TEXT} and not tool_event.stopped

    asyncio.run(check())
    for handler in vars(type(plugin)).values():
        if hasattr(handler, "command_name"):
            assert "/" + handler.command_name in HELP_TEXT
            for alias in handler.command_aliases:
                assert "/" + alias in HELP_TEXT


@pytest.mark.parametrize("admin, private", [(False, False), (True, False), (False, True)])
def test_login_permissions_still_apply(plugin, admin, private):
    async def check():
        event = Event(admin=admin, private=private)
        replies = [text async for text in plugin.bind_login(event)]
        assert "管理员在私聊" in replies[0] and event.stopped
        assert plugin.interaction.contact is None

    asyncio.run(check())
