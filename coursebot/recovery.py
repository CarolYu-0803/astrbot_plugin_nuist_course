"""Single-flight login recovery and periodic session checks."""

import asyncio
import logging
import tempfile
import threading
from pathlib import Path

from filelock import FileLock, Timeout

from .auto_login import BrowserLogin
from .errors import CourseError, CredentialsRejected, LoginExpired
from .runner import sync
from .storage import now, read_json, write_json


class LoginRecovery:
    def __init__(self, cfg, interaction, *, logger=None, browser_factory=BrowserLogin, syncer=sync):
        self.cfg = cfg
        self.interaction = interaction
        self.logger = logger or logging.getLogger(__name__)
        self.browser_factory = browser_factory
        self.syncer = syncer
        self.task = None
        self.last_attempt = None
        self.last_check = None
        self.blocked = False
        self.closed = False
        self.last_status = "尚未检查学校登录状态"
        self._cancel_save = None

    @property
    def enabled(self):
        return self.cfg.get("auto_login_enabled", False)

    def _check_ready(self):
        if self.closed:
            raise CourseError("插件正在卸载，请稍后再试。")
        if not self.enabled:
            raise CourseError("尚未开启自动登录，请在插件配置中启用并填写学校账号密码。")
        if not self.interaction.contact:
            raise CourseError("请 AstrBot 管理员先在私聊中发送 /绑定课表登录。")

    def start(self, *, manual=False):
        self._check_ready()
        if self.task is not None and not self.task.done():
            return self.task
        loop = asyncio.get_running_loop()
        if not manual:
            if self.blocked:
                raise CourseError("账号登录已被学校拒绝，自动重试已暂停；请检查配置后由管理员发送 /课表登录。")
            if (self.last_attempt is not None
                    and loop.time() - self.last_attempt < self.cfg["login_retry_minutes"] * 60):
                raise CourseError("自动登录暂处于重试间隔，请稍后查询或由管理员发送 /课表登录。")
        self.last_attempt = loop.time()
        self.blocked = False
        self.last_status = "正在登录学校系统"
        self._cancel_save = threading.Event()
        self.task = asyncio.create_task(self._run(), name="nuist:login")
        # Commands start this task without waiting, so always retrieve its exception.
        self.task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return self.task

    async def refresh(self):
        task = self.start()
        waiter = asyncio.get_running_loop().create_future()

        def finished(completed):
            if waiter.done():
                return
            if completed.cancelled():
                waiter.set_exception(CourseError("本次学校登录已取消，暂使用可用的备用课表。"))
            elif completed.exception() is not None:
                waiter.set_exception(completed.exception())
            else:
                waiter.set_result(completed.result())

        task.add_done_callback(finished)
        try:
            # Cancelling a query cancels its waiter, while the shared login continues.
            # A separately cancelled login becomes a regular error for other queries.
            return await waiter
        finally:
            task.remove_done_callback(finished)

    def _validate_and_save(self, session, browser_state, cancelled=None):
        root = self.cfg["state_dir"]
        # Verify all timetable endpoints before replacing a usable session or cache.
        with tempfile.TemporaryDirectory(prefix=".login-", dir=root) as folder:
            staging = Path(folder)
            write_json(staging / "session.json", session)
            snapshot = self.syncer({**self.cfg, "state_dir": staging}, now().date())
            session = read_json(staging / "session.json")
            if cancelled is not None and cancelled.is_set():
                raise CourseError("本次学校登录已取消。")
            try:
                with FileLock(str(root / "run.lock"), timeout=0):
                    write_json(root / "session.json", session)
                    write_json(root / "snapshot.json", snapshot)
                    write_json(root / "browser_state.json", browser_state)
            except Timeout:
                raise CourseError("课表状态目录正在被其他程序更新，请稍后重试登录。") from None
        return snapshot

    async def _run(self):
        try:
            session, browser_state = await self.browser_factory(self.cfg, self.interaction.request).run()
            snapshot = await asyncio.to_thread(self._validate_and_save, session, browser_state, self._cancel_save)
            self.last_status = f"学校登录已更新，已同步 {snapshot['term']} 学期课表"
            try:
                await self.interaction.notify(self.last_status + "。")
            except CourseError:
                self.logger.warning("学校登录成功，但结果未能发送至绑定私聊。")
            return snapshot
        except asyncio.CancelledError:
            self.last_status = "本次学校登录已取消"
            raise
        except Exception as error:
            self.blocked = isinstance(error, CredentialsRejected)
            self.last_status = str(error) if isinstance(error, CourseError) else "自动登录失败，请检查服务器环境和文件权限。"
            self.logger.warning("学校自动登录未完成（%s）。", type(error).__name__)
            try:
                await self.interaction.notify(self.last_status)
            except CourseError:
                self.logger.warning("学校登录结果未能发送至绑定私聊。")
            raise CourseError(self.last_status) from None

    def _check_session(self):
        try:
            with FileLock(str(self.cfg["state_dir"] / "run.lock"), timeout=0):
                return self.syncer(self.cfg, now().date())
        except Timeout:
            raise CourseError("课表正在被其他程序更新。") from None

    async def maintain(self):
        if not self.enabled or not self.interaction.contact or self.closed:
            return
        moment = asyncio.get_running_loop().time()
        if self.last_check is not None and moment - self.last_check < self.cfg["session_check_minutes"] * 60:
            return
        self.last_check = moment
        if self.task is not None and not self.task.done():
            return
        try:
            await asyncio.to_thread(self._check_session)
        except LoginExpired:
            await self.refresh()
        except CourseError as error:
            self.logger.warning("学校会话检查：%s", error)
        else:
            self.last_status = "学校会话有效，课表已同步"

    async def cancel(self):
        active = self.task is not None and not self.task.done()
        if active:
            self._cancel_save.set()
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.interaction.cancel()
        return active

    async def close(self):
        self.closed = True
        await self.cancel()
