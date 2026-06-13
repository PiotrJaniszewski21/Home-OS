from flask import Blueprint

settings_bp = Blueprint(
    "settings",
    __name__,
    template_folder="templates",
)

from home_os.modules.settings import routes  # noqa: E402, F401
