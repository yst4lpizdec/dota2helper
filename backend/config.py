import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "dota2helper.db"

ENV_PATH = PROJECT_DIR / ".env"


def load_env(path=ENV_PATH):
    """Читает KEY=VALUE из .env, не перетирая реальные переменные окружения."""

    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)

        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


OPENDOTA_API = "https://api.opendota.com/api"

STRATZ_API = "https://api.stratz.com/graphql"
STRATZ_API_KEY = os.environ.get("STRATZ_API_KEY", "")

STEAM_API = "https://api.steampowered.com"
STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "")
