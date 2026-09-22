import sys

from database.database import init_database
from database.schema import init_schema
from services import collector, stratz
from services.dota_data import get_heroes, save_heroes


def setup():
    """Разовая подготовка: схема БД и справочники."""

    init_database()
    init_schema()

    heroes = get_heroes()
    save_heroes(heroes)

    items, abilities = collector.save_constants()
    added = collector.fill_missing_items()

    version = stratz.get_current_game_version()

    print(f"Героев: {len(heroes)}")
    print(f"Предметов: {items} (+{added} из OpenDota)")
    print(f"Способностей: {abilities}")
    print(f"Текущий патч: {version['name']} (id {version['id']})")

    return version


def main():
    version = setup()

    target = int(sys.argv[1]) if len(sys.argv) > 1 else 100

    print(f"\nСобираем {target} матчей...")

    saved = collector.collect(target=target, game_versions=[version["id"]])

    print(f"\nГотово. Новых матчей сохранено: {saved}")


if __name__ == "__main__":
    main()
