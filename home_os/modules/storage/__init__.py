from flask import Blueprint

storage_bp = Blueprint(
    "storage",
    __name__,
    template_folder="templates",
)

from home_os.modules.storage import routes  # noqa: E402, F401
