from flask import Blueprint

network_bp = Blueprint(
    "network",
    __name__,
    template_folder="templates",
)

from home_os.modules.network import routes  # noqa: E402, F401
