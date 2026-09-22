import sys

from database.database import init_database
from database.schema import init_schema
from services import collector
from services.dota_data import get_heroes, save_heroes
from services.patches import Weights


def setup():
    """Разовая подготовка: схема БД и справочники."""

    init_database()
    init_schema()

    heroes = get_heroes()
    save_heroes(heroes)

    items, abilities = collector.save_constants()
    added = collector.fill_missing_items()

    # Справочник STRATZ отстаёт от патча: слоты способностей, дерево
    # талантов и переименованных героев чиним поверх него.
    layout = collector.save_hero_layout()

    patch = Weights().current

    print(f"Героев: {len(heroes)}")
    print(f"Предметов: {items} (+{added} из OpenDota)")
    print(f"Способностей: {abilities}")
    print(f"Дерево талантов: {layout['talent_tree']}")
    print(f"Переименовано героев: {len(layout['renamed'])}")
    print(f"Текущий патч: {patch}")

    return patch


def main():
    version = setup()

    target = int(sys.argv[1]) if len(sys.argv) > 1 else 100

    print(f"\nСобираем {target} матчей...")

    saved = collector.collect(target=target, game_versions=[version["id"]])

    print(f"\nГотово. Новых матчей сохранено: {saved}")


if __name__ == "__main__":
    main()
