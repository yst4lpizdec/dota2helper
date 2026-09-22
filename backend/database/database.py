import sqlite3

from config import DATABASE_PATH


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA busy_timeout = 30000")

    return connection


def init_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # WAL позволяет читать во время записи: без него анализ падает с
    # "database is locked", пока в фоне идёт сбор матчей. Режим хранится
    # в самом файле БД, поэтому включаем его один раз при старте —
    # переключить его на ходу всё равно нельзя, нужна свободная база.
    connection = get_connection()

    try:
        connection.execute("PRAGMA journal_mode = WAL")

    except sqlite3.OperationalError:
        print("  База занята, WAL включим при следующем запуске.")

    connection.close()
