from flask import Blueprint

sharing_bp = Blueprint(
    "sharing",
    __name__,
    template_folder="templates",
)

from home_os.modules.sharing import routes  # noqa: E402, F401
