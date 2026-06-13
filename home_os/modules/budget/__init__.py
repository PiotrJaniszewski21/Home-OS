from flask import Blueprint

budget_bp = Blueprint(
    "budget",
    __name__,
    template_folder="templates",
)

from home_os.modules.budget import routes  # noqa: E402, F401
