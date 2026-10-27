import argparse
import sys
from datetime import date

from filelock import FileLock, Timeout

from .errors import CourseError
from .storage import config, now


def main(argv=None):
    parser = argparse.ArgumentParser(description="南信大课表登录与同步助手（推送由 AstrBot 插件处理）")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)
    login_cmd = sub.add_parser("login", help="打开浏览器，人工登录后保存会话")
    login_cmd.add_argument("--channel", choices=["chrome", "msedge"])
    sub.add_parser("sync", help="实时同步整学期课表")
    preview = sub.add_parser("preview", help="仅预览课表，不发送消息")
    preview.add_argument("--date", type=date.fromisoformat)
    preview.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        cfg = config(args.config)
        with FileLock(str(cfg["state_dir"] / "run.lock"), timeout=0):
            from .runner import prepare, sync
            if args.command == "login":
                from .login import login
                login(cfg, args.channel)
            elif args.command == "sync":
                snapshot = sync(cfg, now().date())
                print(f"同步成功：{snapshot['term']}，{len(snapshot['courses'])} 条课程安排。")
            elif args.command == "preview":
                title, body, _ = prepare(cfg, args.date or now().date(), args.offline)
                print(title + "\n\n" + body)
        return 0
    except Timeout:
        print("已有程序正在使用课表状态目录，本次退出。", file=sys.stderr)
        return 3
    except CourseError as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except Exception as error:
        # Do not display raw network exceptions or school response bodies.
        print(f"运行失败（{type(error).__name__}），请检查配置、文件权限和程序版本。", file=sys.stderr)
        return 2

