from database.database import get_connection


# Версия схемы. Увеличиваем, когда матчевые таблицы меняют формат
# и старые данные нужно пересобрать заново.
SCHEMA_VERSION = 5

# Таблицы с сырыми данными матчей. При смене SCHEMA_VERSION
# пересоздаются с нуля: перекачать их дешевле, чем мигрировать.
MATCH_TABLES = [
    "matches",
    "match_players",
    "player_purchases",
    "player_abilities",
    "player_talents",
    "player_neutrals",
]

REFERENCE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS heroes (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        localized_name TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS abilities (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        is_talent INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS hero_abilities (
        hero_id INTEGER NOT NULL,
        ability_id INTEGER NOT NULL,
        slot INTEGER NOT NULL,
        PRIMARY KEY (hero_id, ability_id)
    );

    CREATE TABLE IF NOT EXISTS hero_talents (
        hero_id INTEGER NOT NULL,
        ability_id INTEGER NOT NULL,
        slot INTEGER NOT NULL,
        PRIMARY KEY (hero_id, ability_id)
    );

    CREATE TABLE IF NOT EXISTS item_components (
        item_id INTEGER NOT NULL,
        component_id INTEGER NOT NULL,
        PRIMARY KEY (item_id, component_id)
    );

    CREATE TABLE IF NOT EXISTS guides (
        hero_id INTEGER NOT NULL,
        position TEXT NOT NULL,
        matches INTEGER NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY (hero_id, position)
    );

    CREATE TABLE IF NOT EXISTS matchup_items (
        hero_id INTEGER NOT NULL,
        enemy_hero_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        buyers INTEGER NOT NULL,
        share REAL NOT NULL,
        base_share REAL NOT NULL,
        shift REAL NOT NULL,
        PRIMARY KEY (hero_id, enemy_hero_id, item_id)
    );

    CREATE TABLE IF NOT EXISTS matchup_pairs (
        hero_id INTEGER NOT NULL,
        enemy_hero_id INTEGER NOT NULL,
        matches INTEGER NOT NULL,
        wins INTEGER NOT NULL,
        PRIMARY KEY (hero_id, enemy_hero_id)
    );

    CREATE TABLE IF NOT EXISTS accounts (
        steam_account_id INTEGER PRIMARY KEY,
        processed INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
"""

MATCH_SCHEMA = """
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY,
        game_version INTEGER,
        start_time INTEGER,
        duration INTEGER,
        radiant_win INTEGER,
        avg_rank_tier INTEGER
    );

    CREATE TABLE IF NOT EXISTS match_players (
        match_id INTEGER NOT NULL,
        player_slot INTEGER NOT NULL,
        hero_id INTEGER NOT NULL,
        steam_account_id INTEGER,
        is_radiant INTEGER NOT NULL,
        win INTEGER NOT NULL,
        position TEXT,
        lane TEXT,
        role TEXT,
        neutral_item_id INTEGER,  -- финальная нейтралка; прогрессия у STRATZ закрыта
        PRIMARY KEY (match_id, player_slot)
    );

    CREATE TABLE IF NOT EXISTS player_purchases (
        match_id INTEGER NOT NULL,
        player_slot INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        purchase_time INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS player_abilities (
        match_id INTEGER NOT NULL,
        player_slot INTEGER NOT NULL,
        ability_id INTEGER NOT NULL,
        level INTEGER NOT NULL,
        time INTEGER
    );

    CREATE TABLE IF NOT EXISTS player_talents (
        match_id INTEGER NOT NULL,
        player_slot INTEGER NOT NULL,
        talent_id INTEGER NOT NULL,
        time INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_accounts_todo
        ON accounts (processed);

    CREATE INDEX IF NOT EXISTS idx_players_hero_pos
        ON match_players (hero_id, position);

    CREATE INDEX IF NOT EXISTS idx_purchases_player
        ON player_purchases (match_id, player_slot);

    CREATE INDEX IF NOT EXISTS idx_abilities_player
        ON player_abilities (match_id, player_slot);

    CREATE INDEX IF NOT EXISTS idx_talents_player
        ON player_talents (match_id, player_slot);
"""


# Колонки справочников, добавленные после первого выпуска схемы.
# Справочники не пересоздаются, поэтому их доращиваем через ALTER.
REFERENCE_COLUMNS = [
    ("items", "display_name", "TEXT"),
    ("items", "cost", "INTEGER"),
    # 1 — предмет только входит в состав других и сам ни из чего не собран.
    ("items", "is_base_component", "INTEGER NOT NULL DEFAULT 0"),
    ("abilities", "display_name", "TEXT"),
]


def init_schema():
    connection = get_connection()

    current = connection.execute("PRAGMA user_version").fetchone()[0]

    if current < SCHEMA_VERSION:
        print(
            f"Схема устарела (v{current} -> v{SCHEMA_VERSION}), "
            f"пересоздаём таблицы матчей."
        )

        for table in MATCH_TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {table}")

    connection.executescript(REFERENCE_SCHEMA)

    for table, column, column_type in REFERENCE_COLUMNS:
        existing = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})")
        }

        if column not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
            )

    connection.executescript(MATCH_SCHEMA)

    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    connection.commit()
    connection.close()


def get_meta(key, default=None):
    connection = get_connection()

    row = connection.execute(
        "SELECT value FROM meta WHERE key = ?", (key,)
    ).fetchone()

    connection.close()

    return row["value"] if row else default


def set_meta(key, value):
    connection = get_connection()

    connection.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (key, str(value)),
    )

    connection.commit()
    connection.close()
