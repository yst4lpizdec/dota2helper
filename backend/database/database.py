import sqlite3

from config import DATABASE_PATH


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA busy_timeout = 30000")

    # Сбор матчей упирается не в сеть, а в диск: база на два гигабайта,
    # и каждая вставка тянет за собой страницы индексов.
    #
    # synchronous = NORMAL в режиме WAL означает «не ждать диск на каждой
    # записи». Потерять при внезапном выключении можно только последние
    # транзакции — для собранных матчей это не потеря, их доберут заново.
    connection.execute("PRAGMA synchronous = NORMAL")

    # Кэш страниц на 128 МБ вместо двух: с ним индексы не перечитываются
    # с диска на каждой вставке.
    connection.execute("PRAGMA cache_size = -131072")

    # Реже переносим журнал в саму базу — это самая дорогая операция.
    connection.execute("PRAGMA wal_autocheckpoint = 4000")

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
