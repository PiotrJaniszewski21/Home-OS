import fcntl
import logging
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from flask_sock import Sock

from home_os.modules.auth.routes import admin_required
from home_os.modules.terminal import terminal_bp

logger = logging.getLogger("home_os.terminal")

sock = Sock()

BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "chmod -R 777 /",
    ":(){ :|:",
]


@terminal_bp.route("/terminal")
@admin_required
def terminal_view():
    return render_template("terminal/terminal.html")


@terminal_bp.route("/api/terminal/exec", methods=["POST"])
@admin_required
def execute():
    data = request.get_json()
    command = data.get("command", "").strip()

    if not command:
        return jsonify({"ok": False, "error": "No command provided"}), 400

    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            logger.warning(f"BLOCKED command from {current_user.username}: {command}")
            return jsonify({"ok": False, "error": "Command blocked for safety"}), 403

    logger.info(f"Terminal [{current_user.username}]: {command}")

    try:
        result = subprocess.run(
            ["sudo", "bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=30,
            cwd="/",
        )
        return jsonify({
            "ok": True,
            "data": {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Command timed out (30s limit)"}), 408
    except Exception:
        return jsonify({"ok": False, "error": "Execution failed"}), 500


def init_sock(app):
    """Initialize WebSocket support on the Flask app."""
    sock.init_app(app)


@sock.route("/ws/terminal")
def terminal_ws(ws):
    from flask_login import current_user as cu
    if not cu.is_authenticated or not cu.is_admin:
        ws.close(1008, "Unauthorized")
        return

    logger.info(f"PTY session opened by {cu.username}")

    pid, fd = pty.fork()
    if pid == 0:
        # Child process
        os.execvp("bash", ["bash", "--login"])
        return

    # Parent process
    _set_winsize(fd, 24, 80)
    alive = True

    def read_pty():
        nonlocal alive
        while alive:
            try:
                rlist, _, _ = select.select([fd], [], [], 0.05)
                if rlist:
                    data = os.read(fd, 4096)
                    if not data:
                        alive = False
                        break
                    ws.send(data)
            except OSError:
                alive = False
                break
            except Exception:
                alive = False
                break

    reader = threading.Thread(target=read_pty, daemon=True)
    reader.start()

    try:
        while alive:
            msg = ws.receive()
            if msg is None:
                break
            if isinstance(msg, str):
                if msg.startswith("\x01resize:"):
                    parts = msg[8:].split(",")
                    if len(parts) == 2:
                        try:
                            rows, cols = int(parts[0]), int(parts[1])
                            _set_winsize(fd, rows, cols)
                            os.kill(pid, signal.SIGWINCH)
                        except (ValueError, OSError):
                            pass
                else:
                    os.write(fd, msg.encode())
            elif isinstance(msg, bytes):
                os.write(fd, msg)
    except Exception:
        pass
    finally:
        alive = False
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        except (OSError, ChildProcessError):
            pass
        logger.info(f"PTY session closed for {cu.username}")


def _set_winsize(fd, rows, cols):
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
