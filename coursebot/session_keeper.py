"""Periodic session renewal without fetching or re-dating the timetable cache."""

import asyncio
import logging
import time
from datetime import datetime

from .errors import CourseError, DataError, LoginExpired, StateBusy
from .storage import TZ, now, read_json, write_json


class SessionKeeper:
    def __init__(self, cfg, check, *, clock=now, monotonic=time.monotonic, logger=None):
        self.cfg = cfg
        self.check = check
        self.clock = clock
        self.monotonic = monotonic
        self.logger = logger or logging.getLogger(__name__)
        self.interval = cfg.get("keepalive_interval_minutes", 5) * 60
        self.path = cfg["state_dir"] / "session-health.json"
        self._lock = asyncio.Lock()
        self._next_check = 0
        self.status = {"status": "pending", "message": "等待本次启动后的首次检查。"}
        if self.path.exists():
            try:
                previous = read_json(self.path)
                timestamp = previous.get("last_success_at")
                if timestamp and datetime.fromisoformat(timestamp).tzinfo is not None:
                    self.status["last_success_at"] = timestamp
            except (CourseError, AttributeError, TypeError, ValueError):
                pass

    async def tick(self):
        async with self._lock:
            if not self.interval or self.monotonic() < self._next_check:
                return
            # No immediate retry storm on network failure or a broken login.
            self._next_check = self.monotonic() + self.interval
            recovered = False
            try:
                result = await self.check()
                recovered = bool(result.get("recovered"))
                status = "alive"
                message = "已通过门户登录态自动恢复教务会话，学校接口验证成功。" if recovered else "学校接口验证成功，已尝试续期门户和教务会话。"
                if result.get("portal_warning"):
                    message = "学校接口验证成功；门户续期未完成：" + result["portal_warning"]
            except StateBusy:
                # Manual import/login owns the same file lock; do not disturb its status.
                self._next_check = self.monotonic() + 20
                return
            except LoginExpired as error:
                status, message = "login_required", str(error)
            except DataError as error:
                status, message = "invalid_response", str(error)
            except CourseError as error:
                status, message = "unavailable", str(error)
            except Exception as error:
                status, message = "error", f"保活失败（{type(error).__name__}），请检查网络、文件权限和插件配置。"
            moment = self.clock().astimezone(TZ).isoformat()
            previous_status = self.status.get("status")
            previous_message = self.status.get("message")
            self.status = {**self.status, "status": status, "checked_at": moment,
                           "message": message, "recovered": recovered}
            if status == "alive":
                self.status["last_success_at"] = moment
            write_json(self.path, self.status)
            if status != previous_status or message != previous_message or recovered:
                log = self.logger.info if status == "alive" else self.logger.warning
                log("学校登录保活：%s", message)

    def describe(self):
        if not self.interval:
            lines = ["自动保活已关闭。"]
        else:
            lines = [f"自动保活间隔：{self.interval // 60} 分钟。", self.status["message"]]
        for key, label in (("checked_at", "最近检查"), ("last_success_at", "最近接口验证成功")):
            if self.status.get(key):
                stamp = datetime.fromisoformat(self.status[key]).astimezone(TZ)
                lines.append(f"{label}：{stamp:%Y-%m-%d %H:%M:%S}（北京时间）。")
        if self.status.get("status") == "login_required":
            lines.append("请运行 login 重新完成学校认证，再更新 state_dir 中的 session.json；更新后会自动重新检查。")
        lines.append("自动恢复门户登录态：" + ("开启。" if self.cfg.get("auto_sso_recovery", True) else "关闭。"))
        return "\n".join(lines)
