"""One pending login challenge, scoped to an administrator's private chat."""

import asyncio
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from .errors import CourseError
from .storage import read_json, write_json


@dataclass
class Challenge:
    token: str
    kind: str
    future: asyncio.Future
    expires: float


class LoginInteraction:
    def __init__(self, data_dir, sender, timeout=180):
        self.path = Path(data_dir) / "login_contact.json"
        self.sender = sender
        self.timeout = timeout
        self.pending = None
        self.contact = read_json(self.path) if self.path.exists() else None
        if self.contact is not None and (
            not isinstance(self.contact, dict)
            or not isinstance(self.contact.get("origin"), str)
            or not self.contact["origin"]
            or not isinstance(self.contact.get("sender_id"), str)
            or not self.contact["sender_id"]
        ):
            raise CourseError("登录交互绑定记录损坏，请检查 login_contact.json。")

    def bind(self, origin, sender_id):
        if self.pending:
            raise CourseError("正在等待登录验证码，请先完成或取消当前登录。")
        if not origin or not sender_id:
            raise CourseError("无法识别当前私聊，请检查平台适配器。")
        contact = {"origin": str(origin), "sender_id": str(sender_id)}
        write_json(self.path, contact)
        self.contact = contact

    def owns(self, origin, sender_id):
        return self.contact == {"origin": str(origin), "sender_id": str(sender_id)}

    async def notify(self, text, image=None):
        if not self.contact:
            raise CourseError("请 AstrBot 管理员先在私聊中发送 /绑定课表登录。")
        try:
            result = await asyncio.wait_for(
                self.sender(self.contact["origin"], text, image), timeout=60
            )
        except Exception:
            raise CourseError("登录交互消息发送失败，请检查绑定私聊的平台连接。") from None
        if result is False:
            raise CourseError("登录交互未找到可用平台，请重新绑定登录私聊。")

    async def request(self, kind, image=None):
        if self.pending:
            raise CourseError("已有登录验证等待回复。")
        loop = asyncio.get_running_loop()
        challenge = Challenge(secrets.token_hex(3), kind, loop.create_future(), loop.time() + self.timeout)
        self.pending = challenge
        label = "图片验证码" if kind == "image" else "手机短信验证码"
        text = (f"学校登录需要{label}。请在 {self.timeout} 秒内回复：\n"
                f"/课表验证码 {challenge.token} 验证码\n"
                "发送 /取消课表登录 可结束本次登录。")
        try:
            await self.notify(text, image)
            remaining = max(0, challenge.expires - loop.time())
            return await asyncio.wait_for(asyncio.shield(challenge.future), timeout=remaining)
        except asyncio.TimeoutError:
            raise CourseError("等待登录验证码超时，请发送 /课表登录 重新开始。") from None
        finally:
            if not challenge.future.done():
                challenge.future.cancel()
            if self.pending is challenge:
                self.pending = None

    def submit(self, origin, sender_id, token, code):
        if not self.owns(origin, sender_id):
            raise CourseError("请在已绑定的管理员私聊中回复验证码。")
        challenge = self.pending
        if (challenge is None or challenge.future.done()
                or asyncio.get_running_loop().time() >= challenge.expires):
            raise CourseError("当前没有等待中的验证，或验证码已超时。")
        if (not re.fullmatch(r"[a-f0-9]{6}", str(token))
                or not secrets.compare_digest(str(token), challenge.token)):
            raise CourseError("验证编号不匹配，请使用最新提示中的编号。")
        code = str(code).strip()
        pattern = r"[A-Za-z0-9]{1,10}" if challenge.kind == "image" else r"[0-9]{4,10}"
        if not re.fullmatch(pattern, code):
            raise CourseError("验证码格式不正确，请只输入图片字符或短信数字。")
        challenge.future.set_result(code)

    def cancel(self):
        if self.pending and not self.pending.future.done():
            self.pending.future.cancel()
