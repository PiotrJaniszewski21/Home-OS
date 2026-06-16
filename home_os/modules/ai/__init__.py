from flask import Blueprint

ai_bp = Blueprint(
    "ai",
    __name__,
    template_folder="templates",
)

from home_os.modules.ai import routes  # noqa: E402, F401
