from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()


APP_NAME = os.getenv("APP_NAME", "Planning Web")
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8002"))

FRONT_BUILD_DIR = Path(os.getenv("FRONT_BUILD_DIR", r"T:\planning_front\build"))
FRONT_STATIC_DIR = Path(os.getenv("FRONT_STATIC_DIR", r"T:\planning_front\build\static"))

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME", "planning_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
}