import os
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).parent.parent


def get_config_path(config_path=None):
    if config_path is None:
        config_path = os.environ.get(
            "HOME_OS_CONFIG",
            Path(__file__).parent.parent / "config.yaml",
        )
    return Path(config_path)


def load_config(config_path=None):
    with open(get_config_path(config_path)) as f:
        return yaml.safe_load(f)


class Config:
    def __init__(self, config_dict=None):
        if config_dict is None:
            config_dict = load_config()
        self._config = config_dict

    @property
    def SECRET_KEY(self):
        key_file = self._config["server"].get("secret_key_file")
        if key_file:
            key_path = Path(key_file)
            if key_path.exists():
                return key_path.read_text().strip()
        return self._config["server"].get("secret_key", "change-me-in-production")

    @property
    def SQLALCHEMY_DATABASE_URI(self):
        db_path = Path(self._config["database"]["path"])
        if not db_path.is_absolute():
            db_path = Path(__file__).parent.parent / db_path
        return f"sqlite:///{db_path}"

    @property
    def SQLALCHEMY_TRACK_MODIFICATIONS(self):
        return False

    @property
    def DEBUG(self):
        return self._config["server"].get("debug", False)


def create_flask_config(config_dict=None):
    cfg = Config(config_dict)
    return {
        "SECRET_KEY": cfg.SECRET_KEY,
        "SQLALCHEMY_DATABASE_URI": cfg.SQLALCHEMY_DATABASE_URI,
        "SQLALCHEMY_TRACK_MODIFICATIONS": cfg.SQLALCHEMY_TRACK_MODIFICATIONS,
        "DEBUG": cfg.DEBUG,
    }
