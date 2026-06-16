from flask import Blueprint

files_bp = Blueprint(
    "files",
    __name__,
    template_folder="templates",
)

from home_os.modules.files import routes  # noqa: E402, F401
