"""Persistent daily subscriptions, independent of AstrBot's platform adapters."""

import asyncio
import logging
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from .errors import CourseError, LoginExpired
from .runner import build_message, prepare_timetable
from .storage import TZ, now, read_json, write_json


def parse_time(value):
    value = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,2}(?::[0-9]{2})?", value):
        raise CourseError("时间格式错误，请发送 /设置时间 8 或 /设置时间 08:30（北京时间，小时 0–23）。")
    parts = value.split(":")
    hour, minute = int(parts[0]), int(parts[1]) if len(parts) == 2 else 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise CourseError("时间超出范围，小时应为 0–23，分钟应为 0–59。")
    return f"{hour:02d}:{minute:02d}"


def at_time(day, value):
    return datetime.combine(day, time.fromisoformat(value), TZ)


class CourseNotifier:
    def __init__(self, cfg, data_dir, sender, *, clock=now, fetch=build_message, logger=None, recover=None):
        self.cfg = cfg
        self.sender = sender
        self.clock = clock
        self.fetch = fetch
        self.recover = recover
        self.logger = logger or logging.getLogger(__name__)
        self.path = Path(data_dir) / "subscriptions.json"
        self._lock = asyncio.Lock()
        self._tick_lock = asyncio.Lock()
        self._fetch_lock = asyncio.Lock()
        self._subscriptions = self._load()

    def _load(self):
        if not self.path.exists():
            return {}
        data = read_json(self.path)
        try:
            if data["version"] != 1 or not isinstance(data["subscriptions"], dict):
                raise ValueError
            for origin, row in data["subscriptions"].items():
                if not isinstance(origin, str) or not origin or not isinstance(row, dict):
                    raise ValueError
                if parse_time(row["time"]) != row["time"]:
                    raise ValueError
                if datetime.fromisoformat(row["next_run"]).tzinfo is None:
                    raise ValueError
                delivery = row.get("last_delivery", {})
                if not isinstance(delivery, dict):
                    raise ValueError
                if delivery:
                    datetime.fromisoformat(delivery["at"])
                    datetime.strptime(delivery["date"], "%Y-%m-%d")
                    if delivery["status"] not in {"pending", "sent", "unknown", "unavailable"}:
                        raise ValueError
            return data["subscriptions"]
        except (CourseError, KeyError, TypeError, ValueError):
            raise CourseError("课表推送订阅记录损坏，请检查插件数据目录的 subscriptions.json；原文件未被覆盖。") from None

    def _commit(self, subscriptions):
        write_json(self.path, {"version": 1, "subscriptions": subscriptions})
        self._subscriptions = subscriptions

    async def set_time(self, origin, value):
        value = parse_time(value)
        if not origin:
            raise CourseError("无法识别当前会话，请在支持主动消息的平台中设置。")
        async with self._lock:
            moment = self.clock().astimezone(TZ)
            previous = self._subscriptions.get(origin, {})
            delivery = previous.get("last_delivery", {})
            candidate = at_time(moment.date(), value)
            attempted_today = (delivery.get("date") == moment.date().isoformat()
                               and delivery.get("status") != "unavailable")
            if candidate <= moment or attempted_today:
                candidate += timedelta(days=1)
            row = {"time": value, "next_run": candidate.isoformat(), "last_delivery": delivery}
            self._commit({**self._subscriptions, origin: row})
            return candidate

    async def cancel(self, origin):
        async with self._lock:
            if origin not in self._subscriptions:
                return False
            subscriptions = dict(self._subscriptions)
            del subscriptions[origin]
            self._commit(subscriptions)
            return True

    def _read_message(self, target, defer_login=False, structured=False):
        try:
            with FileLock(str(self.cfg["state_dir"] / "run.lock"), timeout=0):
                if structured:
                    return prepare_timetable(self.cfg, target, defer_login=defer_login)
                if self.fetch is build_message:
                    return self.fetch(self.cfg, target, defer_login=defer_login)
                return self.fetch(self.cfg, target)
        except Timeout:
            raise CourseError("课表正在被登录或同步助手更新，请稍后再试。") from None

    async def view(self, target=None, *, structured=False):
        target = target or self.clock().astimezone(TZ).date()
        async with self._fetch_lock:
            try:
                return await asyncio.to_thread(self._read_message, target, bool(self.recover), structured)
            except LoginExpired:
                if self.recover is None:
                    raise
                # The file lock has been released before the user is asked for a code.
                try:
                    await self.recover()
                except CourseError:
                    # Existing cache rules still apply when login is unavailable.
                    pass
                return await asyncio.to_thread(self._read_message, target, structured=structured)

    async def timetable(self, target=None):
        """Read structured data for LLM tools without changing subscriptions or deliveries."""
        return await self.view(target, structured=True)

    async def _claim(self, origin, expected, moment):
        async with self._lock:
            row = self._subscriptions.get(origin)
            if row != expected or self.clock().astimezone(TZ).date() != moment.date():
                return None
            tomorrow = at_time(moment.date() + timedelta(days=1), row["time"])
            delivery = {"date": moment.date().isoformat(), "at": moment.isoformat(),
                        "status": "pending", "attempt": uuid4().hex}
            # Persist before calling the adapter: a crash must not resend a possibly delivered message.
            claimed = {**row, "next_run": tomorrow.isoformat(), "last_delivery": delivery}
            self._commit({**self._subscriptions, origin: claimed})
            return claimed

    async def _finish(self, origin, claimed, status):
        async with self._lock:
            row = self._subscriptions.get(origin)
            if row is None or row.get("last_delivery") != claimed["last_delivery"]:
                return
            updated = {**row, "last_delivery": {**row["last_delivery"], "status": status}}
            if status == "unavailable" and row["next_run"] == claimed["next_run"]:
                # False means that AstrBot found no platform; no message was sent, so retry safely.
                moment = self.clock().astimezone(TZ)
                retry = moment + timedelta(minutes=1)
                if retry.date().isoformat() == claimed["last_delivery"]["date"]:
                    updated["next_run"] = retry.isoformat()
            self._commit({**self._subscriptions, origin: updated})

    async def tick(self):
        async with self._tick_lock:
            moment = self.clock().astimezone(TZ)
            async with self._lock:
                due = [(origin, row) for origin, row in self._subscriptions.items()
                       if datetime.fromisoformat(row["next_run"]) <= moment
                       and at_time(moment.date(), row["time"]) <= moment]
            if not due:
                return
            # All sessions due in this pass share one school request, away from the event loop.
            message = await self.view(moment.date())
            for origin, expected in due:
                claimed = await self._claim(origin, expected, moment)
                if claimed is None:
                    continue
                try:
                    result = await asyncio.wait_for(self.sender(origin, message), timeout=60)
                except Exception as error:
                    # Adapter exceptions may include access tokens. Only record the type in logs.
                    await self._finish(origin, claimed, "unknown")
                    self.logger.error("课表主动消息结果未知（%s），今日不自动重发；可用 /查看课表 查询。", type(error).__name__)
                    continue
                status = "unavailable" if result is False else "sent"
                await self._finish(origin, claimed, status)
                if status == "unavailable":
                    self.logger.warning("课表推送未找到匹配的平台，将在一分钟后重试。")
