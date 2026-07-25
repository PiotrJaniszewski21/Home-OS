from flask import Blueprint

media_bp = Blueprint(
    "media",
    __name__,
    template_folder="templates",
)

from home_os.modules.media import routes  # noqa: E402, F401
