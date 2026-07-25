from flask import Blueprint

music_bp = Blueprint("music", __name__, template_folder="templates")

from home_os.modules.music import routes  # noqa: E402, F401
