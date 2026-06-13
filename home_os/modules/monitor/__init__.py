from flask import Blueprint

monitor_bp = Blueprint(
    "monitor",
    __name__,
    template_folder="templates",
)

from home_os.modules.monitor import routes  # noqa: E402, F401
