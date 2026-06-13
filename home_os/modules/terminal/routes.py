import logging
import subprocess

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from home_os.modules.auth.routes import admin_required
from home_os.modules.terminal import terminal_bp

logger = logging.getLogger("home_os.terminal")

# Commands that are never allowed (even for admin)
BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "chmod -R 777 /",
    ":(){ :|:",  # fork bomb
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

    # Block destructive patterns
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            logger.warning(f"BLOCKED command from {current_user.username}: {command}")
            return jsonify({"ok": False, "error": "Command blocked for safety"}), 403

    # Log all commands
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
