from flask import Blueprint

dns_bp = Blueprint(
    "dns",
    __name__,
    template_folder="templates",
)

from home_os.modules.dns import routes  # noqa: E402, F401
