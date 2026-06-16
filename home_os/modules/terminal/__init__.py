from flask import Blueprint

terminal_bp = Blueprint(
    "terminal",
    __name__,
    template_folder="templates",
)

from home_os.modules.terminal import routes  # noqa: E402, F401
