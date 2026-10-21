"""RBX Family Manager admin app launcher.

Run as a standalone app with::

    python -m admin_app

It starts the bot if needed, waits for the admin dashboard to come up, then
opens it in your default browser.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

log = logging.getLogger("rbx.admin_app")


def _resolve_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _freeport(port: int = 8080, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _wait_for_url(url: str, timeout: float = 90.0, interval: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    project_root = _resolve_project_root()
    python_exe = project_root / ".venv" / "Scripts" / "python.exe"
    bot_py = project_root / "bot.py"
    url = "http://localhost:8080/"

    if not python_exe.exists():
        log.error("Missing .venv/Scripts/python.exe - run start-bot.bat once first.")
        input("Press Enter to close...")
        sys.exit(1)

    if not bot_py.exists():
        log.error("Missing bot.py next to this app.")
        input("Press Enter to close...")
        sys.exit(1)

    port_free = _freeport(8080)
    if not port_free:
        log.info("Port 8080 already in use - assuming the bot is already running.")
        already_running = True
    else:
        already_running = False

    proc = None
    if not already_running:
        log.info("Starting bot: %s", bot_py)
        proc = subprocess.Popen(
            [str(python_exe), str(bot_py)],
            cwd=str(project_root),
            creationflags=0x08000000,
        )
        time.sleep(3)

    try:
        log.info("Waiting for admin app at %s", url)
        if not _wait_for_url(url, timeout=90.0):
            log.error("Bot admin app did not become ready in time.")
            input("Press Enter to close...")
            sys.exit(1)

        log.info("Opening admin app in browser...")
        webbrowser.open(url)

        if proc is not None:
            log.info("Bot is running in the background. Close the browser to exit this app.")
            try:
                proc.wait()
            except KeyboardInterrupt:
                log.info("Shutting down bot...")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    main()
