"""启动、停止和检查本目录的 AgentScope 服务。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
URL = "http://127.0.0.1:8000"
API_URL = "http://127.0.0.1:8001"
DESKTOP = RUNTIME / "desktop/node_modules/electron/dist/electron.exe"
INSTANCE_ID = hashlib.sha256(os.path.normcase(str(ROOT)).encode()).hexdigest()
HTTP = build_opener(ProxyHandler({}))


def request(path, *, token=None, api=False):
    headers = {"X-User-ID": "local"}
    if token:
        headers["X-Local-Token"] = token
    req = Request(
        (API_URL if api else URL) + path,
        headers=headers, method="POST" if token else "GET",
    )
    with HTTP.open(req, timeout=3) as response:
        return json.load(response)


def running():
    try:
        return request("/_local/status").get("instance_id") == INSTANCE_ID
    except (OSError, ValueError):
        return False


def ready():
    try:
        return running() and request("/health", api=True).get("status") == "ok"
    except (OSError, ValueError):
        return False


def open_desktop():
    """使用无边框桌面窗口打开本地工作台。"""
    if not DESKTOP.is_file():
        raise RuntimeError("桌面窗口运行时缺失，请检查 .runtime/desktop 安装目录。")
    env = os.environ.copy()
    env.pop("ELECTRON_RUN_AS_NODE", None)
    with (RUNTIME / "desktop-window.log").open("a", encoding="utf-8") as stream:
        subprocess.Popen(
            [str(DESKTOP), str(ROOT / "desktop/main.cjs")],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    print("AgentScope 桌面应用窗口已打开。")


def start(open_browser, open_app=False):
    if not running():
        for port in (8000, 8001):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    raise RuntimeError(f"端口 {port} 已被其他程序占用，请先处理端口冲突。")
            except OSError:
                pass
        if not (ROOT / "examples/web_ui/frontend/dist/index.html").is_file():
            raise RuntimeError("网页界面尚未构建，请查看本地使用说明。")
        RUNTIME.mkdir(exist_ok=True)
        log = RUNTIME / "service.log"
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PATH"] = str(ROOT / ".venv/Scripts") + os.pathsep + env["PATH"]
        with log.open("a", encoding="utf-8") as stream:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "local_server.py")],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        print("正在启动 AgentScope……", flush=True)
        for _ in range(90):
            if ready():
                break
            if process.poll() is not None:
                raise RuntimeError(f"启动失败，请查看日志：{log}")
            time.sleep(0.5)
        else:
            raise RuntimeError(f"启动仍未就绪，请查看日志：{log}")
    health = request("/health", api=True)
    if health.get("status") != "ok":
        raise RuntimeError("服务尚未就绪，请查看 .runtime/service.log。")
    print(f"AgentScope 已就绪：{URL}")
    print(f"版本：{health['version']}；本地用户名：local")
    if open_browser:
        webbrowser.open(URL)
    if open_app:
        open_desktop()


def stop():
    if not running():
        print("本目录的 AgentScope 当前未运行。")
        return
    record = json.loads((RUNTIME / "service.json").read_text(encoding="utf-8"))
    if record.get("instance_id") != INSTANCE_ID:
        raise RuntimeError("实例信息不匹配，未执行停止操作。")
    request("/_local/shutdown", token=record["token"])
    for _ in range(40):
        if not running() and not (RUNTIME / "service.json").exists():
            print("AgentScope 已停止，本地数据已保留。")
            return
        time.sleep(0.5)
    raise RuntimeError("服务仍在结束任务，请稍后再检查状态。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--no-browser", action="store_true", help="启动时不打开浏览器")
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="在独立的桌面应用窗口中打开",
    )
    args = parser.parse_args()
    try:
        if args.action == "start":
            start(not args.no_browser and not args.desktop, open_app=args.desktop)
        elif args.action == "stop":
            stop()
        elif running():
            print(json.dumps(request("/health", api=True), ensure_ascii=False, indent=2))
            print(f"网页地址：{URL}")
        else:
            print("本目录的 AgentScope 当前未运行。")
            return 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"操作未完成：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
