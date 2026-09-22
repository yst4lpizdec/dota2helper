import re

import requests

from config import OPENDOTA_API
from database.database import get_connection
from services import stratz


# Тот же справочник, что отдаёт API OpenDota, но прямо из репозитория.
# Нужен как запасной путь: API периодически отвечает ошибкой Cloudflare,
# а файлы в репозитории лежат всегда.
DOTACONSTANTS_RAW = (
    "https://raw.githubusercontent.com/odota/dotaconstants/master/build"
)


def _opendota_constants(path):
    failure = None

    for url in (
        f"{OPENDOTA_API}/constants/{path}",
        f"{DOTACONSTANTS_RAW}/{path}.json",
    ):
        try:
            response = requests.get(url, timeout=40)

            response.raise_for_status()

            return response.json()

        # ValueError — это когда вместо JSON приходит страница с ошибкой.
        except (requests.RequestException, ValueError) as error:
            failure = error

    print(f"  Справочник OpenDota недоступен ({failure}), пропускаем.")

    return None


def fill_missing_items():
    """Добивает справочник предметов из OpenDota.

    Даёт три вещи, которых нет в constants у STRATZ: имена свежих нейтралок,
    человекочитаемые названия и состав предметов. Последнее нужно, чтобы
    не показывать в рекомендациях Ogre Axe вместо Sange.
    """

    item_ids = _opendota_constants("item_ids")

    if item_ids is None:
        return 0

    items = _opendota_constants("items") or {}

    # Предмет — «сырой компонент», если он входит в состав других,
    # но сам ни из чего не собирается.
    used_in_recipes = set()

    for data in items.values():
        for component in data.get("components") or []:
            used_in_recipes.add(component)

    connection = get_connection()

    before = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    connection.executemany(
        "INSERT OR IGNORE INTO items (id, name) VALUES (?, ?)",
        [
            (int(item_id), f"item_{name}")
            for item_id, name in item_ids.items()
            if name
        ],
    )

    updates = []

    for item_id, name in item_ids.items():
        data = items.get(name)

        if not data:
            continue

        is_base = name in used_in_recipes and not data.get("components")

        updates.append(
            (
                data.get("dname") or name,
                data.get("cost"),
                int(is_base),
                int(item_id),
            )
        )

    connection.executemany(
        """
        UPDATE items
        SET display_name = ?, cost = ?, is_base_component = ?
        WHERE id = ?
        """,
        updates,
    )

    # Состав предметов: нужен, чтобы свернуть покупки игрока
    # к финальным предметам и не показывать Yasha рядом с Sange and Yasha.
    by_name = {name: int(item_id) for item_id, name in item_ids.items()}
    links = []

    for name, data in items.items():
        item_id = by_name.get(name)

        if item_id is None:
            continue

        for component in data.get("components") or []:
            component_id = by_name.get(component)

            if component_id is not None:
                links.append((item_id, component_id))

    connection.execute("DELETE FROM item_components")

    connection.executemany(
        """
        INSERT OR IGNORE INTO item_components (item_id, component_id)
        VALUES (?, ?)
        """,
        links,
    )

    connection.commit()

    after = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    connection.close()

    return after - before


def fill_missing_abilities():
    """Дополняет названия способностей и талантов из OpenDota.

    У STRATZ часть талантов без displayName. У OpenDota название есть,
    но с плейсхолдером вида "+{s:bonus_duration}s ..." — значение
    подставляем из атрибутов STRATZ, которые мы уже сохранили.
    """

    import re

    abilities = _opendota_constants("abilities")

    if abilities is None:
        return 0

    connection = get_connection()

    rows = connection.execute(
        "SELECT id, name FROM abilities WHERE display_name IS NULL"
    ).fetchall()

    updates = []

    for row in rows:
        data = abilities.get(row["name"])

        if not data or not data.get("dname"):
            continue

        # Плейсхолдеры без значений выглядят в интерфейсе мусором,
        # поэтому просто убираем их, оставляя осмысленный текст.
        title = re.sub(r"\{[^}]*\}", "", data["dname"]).replace("  ", " ")

        updates.append((title.strip(), row["id"]))

    connection.executemany(
        "UPDATE abilities SET display_name = ? WHERE id = ?",
        updates,
    )

    # Универсальный талант есть у каждого героя и берётся чаще всех,
    # но названия ему не дают ни STRATZ, ни OpenDota.
    connection.execute(
        """
        UPDATE abilities SET display_name = '+ ко всем атрибутам'
        WHERE name = 'special_bonus_attributes'
        """
    )

    # Всё, что осталось без названия, приводим к читаемому виду из
    # технического имени: лучше "unique furion 8", чем пустота.
    leftovers = connection.execute(
        "SELECT id, name FROM abilities WHERE display_name IS NULL"
    ).fetchall()

    connection.executemany(
        "UPDATE abilities SET display_name = ? WHERE id = ?",
        [
            (
                row["name"]
                .replace("special_bonus_", "")
                .replace("unique_", "")
                .replace("_", " "),
                row["id"],
            )
            for row in leftovers
        ],
    )

    connection.commit()
    connection.close()

    return len(updates)


def save_constants():
    constants = stratz.get_constants()

    connection = get_connection()

    # Именно UPSERT, а не INSERT OR REPLACE: последний пересоздаёт строку
    # и обнуляет display_name/cost, которые мы берём из OpenDota.
    connection.executemany(
        """
        INSERT INTO items (id, name) VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE SET name = excluded.name
        """,
        [
            (item["id"], item["name"] or "")
            for item in constants["items"]
            if item.get("id") is not None
        ],
    )

    connection.executemany(
        """
        INSERT INTO abilities (id, name, is_talent, display_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            is_talent = excluded.is_talent,
            display_name = COALESCE(excluded.display_name, abilities.display_name)
        """,
        [
            (
                ability["id"],
                ability["name"] or "",
                int(bool(ability["isTalent"])),
                # В названиях STRATZ попадается экранированный процент.
                (
                    (ability.get("language") or {})
                    .get("displayName", "")
                    .replace("%%", "%")
                    or None
                ),
            )
            for ability in constants["abilities"]
            if ability.get("id") is not None
        ],
    )

    connection.commit()
    connection.close()

    return len(constants["items"]), len(constants["abilities"])


def load_talent_ids():
    connection = get_connection()

    rows = connection.execute(
        "SELECT id FROM abilities WHERE is_talent = 1"
    ).fetchall()

    connection.close()

    return {row["id"] for row in rows}


def add_accounts(account_ids):
    """Кладёт новые аккаунты в очередь обхода, не трогая уже обработанные."""

    if not account_ids:
        return

    connection = get_connection()

    connection.executemany(
        "INSERT OR IGNORE INTO accounts (steam_account_id) VALUES (?)",
        [(account_id,) for account_id in account_ids],
    )

    connection.commit()
    connection.close()


def take_accounts(limit):
    connection = get_connection()

    rows = connection.execute(
        "SELECT steam_account_id FROM accounts WHERE processed = 0 LIMIT ?",
        (limit,),
    ).fetchall()

    connection.close()

    return [row["steam_account_id"] for row in rows]


def mark_processed(account_id):
    connection = get_connection()

    connection.execute(
        "UPDATE accounts SET processed = 1 WHERE steam_account_id = ?",
        (account_id,),
    )

    connection.commit()
    connection.close()


def seed_accounts():
    """Заполняет очередь стартовыми аккаунтами из лидербордов."""

    accounts = stratz.get_leaderboard_accounts()

    add_accounts(accounts)

    return len(accounts)


def match_count():
    connection = get_connection()

    total = connection.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    connection.close()

    return total


def save_matches(matches, talent_ids):
    """Раскладывает матчи по таблицам. Возвращает встреченные аккаунты."""

    connection = get_connection()

    new_accounts = set()

    for match in matches:
        match_id = match["id"]

        connection.execute(
            """
            INSERT OR REPLACE INTO matches (
                id, game_version, start_time, duration, radiant_win
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                match_id,
                match.get("gameVersionId"),
                match.get("startDateTime"),
                match.get("durationSeconds"),
                int(bool(match.get("didRadiantWin"))),
            ),
        )

        for player in match.get("players") or []:
            slot = player["playerSlot"]
            key = (match_id, slot)

            if player.get("steamAccountId"):
                new_accounts.add(player["steamAccountId"])

            connection.execute(
                """
                INSERT OR REPLACE INTO match_players (
                    match_id, player_slot, hero_id, steam_account_id,
                    is_radiant, win, position, lane, role, neutral_item_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    slot,
                    player["heroId"],
                    player.get("steamAccountId"),
                    int(bool(player.get("isRadiant"))),
                    int(bool(player.get("isVictory"))),
                    player.get("position"),
                    player.get("lane"),
                    player.get("role"),
                    player.get("neutral0Id"),
                ),
            )

            for table in (
                "player_purchases",
                "player_abilities",
                "player_talents",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE match_id = ? AND player_slot = ?",
                    key,
                )

            stats = player.get("stats") or {}

            connection.executemany(
                """
                INSERT INTO player_purchases (
                    match_id, player_slot, item_id, purchase_time
                )
                VALUES (?, ?, ?, ?)
                """,
                [
                    (match_id, slot, buy["itemId"], buy.get("time") or 0)
                    for buy in (stats.get("itemPurchases") or [])
                    if buy.get("itemId")
                ],
            )

            # Таланты приходят вперемешку с обычными способностями,
            # разделяем их по справочнику abilities.
            skills = []
            talents = []

            for upgrade in player.get("abilities") or []:
                ability_id = upgrade.get("abilityId")

                if ability_id is None:
                    continue

                if ability_id in talent_ids:
                    talents.append(
                        (match_id, slot, ability_id, upgrade.get("time"))
                    )
                else:
                    skills.append(
                        (
                            match_id,
                            slot,
                            ability_id,
                            upgrade.get("level") or 0,
                            upgrade.get("time"),
                        )
                    )

            connection.executemany(
                """
                INSERT INTO player_abilities (
                    match_id, player_slot, ability_id, level, time
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                skills,
            )

            connection.executemany(
                """
                INSERT INTO player_talents (
                    match_id, player_slot, talent_id, time
                )
                VALUES (?, ?, ?, ?)
                """,
                talents,
            )

    connection.commit()
    connection.close()

    return new_accounts


def collect(target=1000, game_versions=None):
    """Собирает матчи снежным комом.

    Берёт аккаунт из очереди, качает его последние матчи со всеми деталями,
    а всех встреченных соигроков и противников кладёт в очередь на потом.
    Так выборка остаётся примерно в том же диапазоне рейтинга,
    что и стартовые игроки из лидербордов.
    """

    talent_ids = load_talent_ids()

    if not talent_ids:
        raise RuntimeError(
            "Справочник способностей пуст — сначала запусти save_constants()."
        )

    if not take_accounts(1):
        found = seed_accounts()

        print(f"Очередь пуста, взяли {found} аккаунтов из лидербордов.")

    start_total = match_count()
    empty = 0

    while match_count() - start_total < target:
        accounts = take_accounts(20)

        if not accounts:
            print("Очередь аккаунтов закончилась.")
            break

        for account_id in accounts:
            try:
                matches = stratz.get_player_matches(
                    account_id,
                    game_versions=game_versions,
                )

            except Exception as error:
                # Один проблемный аккаунт не должен ронять весь сбор.
                print(f"  аккаунт {account_id}: пропущен ({error})")

                mark_processed(account_id)

                continue

            mark_processed(account_id)

            if not matches:
                empty += 1
                continue

            add_accounts(save_matches(matches, talent_ids))

            total = match_count()

            print(
                f"  аккаунт {account_id}: +{len(matches)} матчей, "
                f"в базе {total} (цель +{target}, пустых аккаунтов {empty})"
            )

            if total - start_total >= target:
                break

    return match_count() - start_total


def save_hero_layout():
    """Сохраняет порядок способностей героя и раскладку дерева талантов.

    Нужно, чтобы показывать «1-я способность» вместо технического имени
    и «ур.15 левый» вместо просто уровня: в игре таланты выбираются
    парами, и без стороны подсказка бесполезна при русском интерфейсе.
    """

    data = stratz.query(
        """{
            constants {
                heroes {
                    id
                    abilities { slot abilityId }
                    talents { abilityId slot }
                }
            }
        }"""
    )

    connection = get_connection()

    abilities = []
    talents = []

    for hero in data["constants"]["heroes"]:
        for entry in hero.get("abilities") or []:
            if entry.get("abilityId"):
                abilities.append((hero["id"], entry["abilityId"], entry["slot"]))

        for entry in hero.get("talents") or []:
            if entry.get("abilityId"):
                talents.append((hero["id"], entry["abilityId"], entry["slot"]))

    connection.execute("DELETE FROM hero_abilities")
    connection.execute("DELETE FROM hero_talents")

    connection.executemany(
        "INSERT OR REPLACE INTO hero_abilities (hero_id, ability_id, slot)"
        " VALUES (?, ?, ?)",
        abilities,
    )

    connection.executemany(
        "INSERT OR REPLACE INTO hero_talents (hero_id, ability_id, slot)"
        " VALUES (?, ?, ?)",
        talents,
    )

    connection.commit()
    connection.close()

    # Справочник STRATZ отстаёт от патча, поэтому поверх него всегда
    # проходим тремя починками: слоты способностей, дерево талантов и
    # имена героев. Порядок важен — имена трогают ту же таблицу героев.
    return {
        "abilities": len(abilities),
        "talents": len(talents),
        "filled_abilities": fill_hero_abilities(),
        "talent_tree": fill_hero_talents(),
        "renamed": fill_hero_names(),
    }


def fill_hero_abilities():
    """Закрывает дыры в раскладке способностей справочником OpenDota.

    STRATZ отдаёт слоты фасетных способностей заглушкой generic_hidden:
    у Wraith King на втором слоте стоит она вместо Bone Guard. Хуже того,
    заглушка приходит на двух слотах сразу, а ключ таблицы — герой плюс
    способность, поэтому вторая запись затирает первую и слот пропадает
    совсем. В справочнике OpenDota способности идут по порядку слотов,
    и такие дыры из него закрываются.
    """

    catalogue = _opendota_constants("hero_abilities")

    if not catalogue:
        return 0

    connection = get_connection()

    ability_ids = {
        row["name"]: row["id"]
        for row in connection.execute("SELECT id, name FROM abilities")
    }

    hero_ids = {
        row["name"]: row["id"]
        for row in connection.execute("SELECT id, name FROM heroes")
    }

    hidden = ability_ids.get("generic_hidden")

    known = {
        (row["hero_id"], row["slot"]): row["ability_id"]
        for row in connection.execute(
            "SELECT hero_id, ability_id, slot FROM hero_abilities"
        )
    }

    filled = []

    for hero_name, entry in catalogue.items():
        hero_id = hero_ids.get(hero_name)

        if hero_id is None:
            continue

        for slot, ability_name in enumerate(
            entry.get("abilities") or [], start=1
        ):
            # У Monkey King последним элементом идёт вложенный список
            # (превращение в дерево). Слот у него всё равно за пределами
            # обычных шести, так что просто пропускаем.
            if not isinstance(ability_name, str):
                continue

            ability_id = ability_ids.get(ability_name)

            if ability_id is None or ability_id == hidden:
                continue

            occupant = known.get((hero_id, slot))

            # Трогаем только пустые слоты и заглушки: там, где STRATZ
            # назвал настоящую способность, он и остаётся главным.
            if occupant is not None and occupant != hidden:
                continue

            filled.append((hero_id, ability_id, slot))

    connection.executemany(
        "INSERT OR REPLACE INTO hero_abilities (hero_id, ability_id, slot)"
        " VALUES (?, ?, ?)",
        filled,
    )

    connection.commit()
    connection.close()

    return len(filled)


def _clean_dname(text):
    """Подставляет «N» вместо шаблонных вставок справочника.

    OpenDota отдаёт названия прямо из игры, вместе с местами для чисел:
    «deal {s:bonus_spread_pct}% damage». Оставлять фигурные скобки в
    интерфейсе нельзя, а числа нам взять неоткуда.
    """

    if not text:
        return text

    return re.sub(r"\s+", " ", re.sub(r"\{[^}]*\}", "N", text)).strip()


def fill_hero_talents():
    """Перестраивает дерево талантов по справочнику OpenDota.

    Дерево у STRATZ отстаёт от патча: у Witch Doctor на 20-м уровне уже
    стоит maledict_spread, которого там нет вовсе. Хуже того, слоты STRATZ
    не совпадают с уровнями, и талант 25-го уровня показывался на 20-м.

    В справочнике OpenDota у каждого таланта уровень указан явно, а пара
    идёт по порядку. Отсюда слот = (уровень - 1) * 2 + место в паре, и
    место в паре — это сторона в дереве: первый талант пары в игре справа,
    второй слева (проверено на Witch Doctor).
    """

    catalogue = _opendota_constants("hero_abilities")
    names = _opendota_constants("abilities") or {}

    if not catalogue:
        return 0

    connection = get_connection()

    ability_ids = {
        row["name"]: row["id"]
        for row in connection.execute("SELECT id, name FROM abilities")
    }

    hero_ids = {
        row["name"]: row["id"]
        for row in connection.execute("SELECT id, name FROM heroes")
    }

    # Талантов нового патча в нашей таблице ещё нет: матчей с ними тоже
    # нет, а вот дерево показать надо. Заводим свои номера, начиная выше
    # всех настоящих, чтобы не столкнуться с идентификаторами STRATZ.
    next_id = (
        connection.execute("SELECT COALESCE(MAX(id), 0) FROM abilities").fetchone()[0]
        + 1
    )

    added = []
    rows = []
    touched = []

    for hero_name, entry in catalogue.items():
        hero_id = hero_ids.get(hero_name)

        if hero_id is None:
            continue

        # Место в паре считаем по порядку внутри уровня, а не по индексу
        # во всём списке: справочник не обещает, что уровни идут подряд.
        seen = {}

        for talent in entry.get("talents") or []:
            name = talent.get("name")
            level = talent.get("level")

            # Уровни 5 и выше — не таланты, а скрытые способности вроде
            # rubick_hidden3: справочник кладёт их в тот же список.
            if not name or level not in (1, 2, 3, 4):
                continue

            place = seen.get(level, 0)
            seen[level] = place + 1

            if place > 1:
                continue

            ability_id = ability_ids.get(name)

            if ability_id is None:
                ability_id = next_id
                next_id += 1

                ability_ids[name] = ability_id

                added.append(
                    (
                        ability_id,
                        name,
                        1,
                        _clean_dname((names.get(name) or {}).get("dname")) or name,
                    )
                )

            rows.append((hero_id, ability_id, (level - 1) * 2 + place))

        touched.append(hero_id)

    if added:
        connection.executemany(
            "INSERT OR REPLACE INTO abilities (id, name, is_talent, display_name)"
            " VALUES (?, ?, ?, ?)",
            added,
        )

    # Дерево перезаписываем целиком по каждому герою: талант, уехавший на
    # другой уровень, иначе остался бы и на старом месте.
    connection.executemany(
        "DELETE FROM hero_talents WHERE hero_id = ?",
        [(hero_id,) for hero_id in touched],
    )

    connection.executemany(
        "INSERT OR REPLACE INTO hero_talents (hero_id, ability_id, slot)"
        " VALUES (?, ?, ?)",
        rows,
    )

    connection.commit()
    connection.close()

    return {"heroes": len(touched), "talents": len(rows), "new_abilities": len(added)}


# Список героев от самой Valve. Справочники (и STRATZ, и OpenDota) отстают
# с переименованиями: Outworld Devourer в игре давно Destroyer.
VALVE_HEROLIST = "https://www.dota2.com/datafeed/herolist?language=english"


def fill_hero_names():
    """Обновляет имена героев по датафиду Valve.

    Игрок видит имя из игры, а не то, под которым героя выпускали.
    Технические имена (npc_dota_hero_nevermore) при этом не трогаем:
    на них завязаны и GSI, и иконки.
    """

    try:
        answer = requests.get(
            VALVE_HEROLIST,
            headers={"User-Agent": "Dota2Helper/1.0"},
            timeout=20,
        )
        answer.raise_for_status()

        heroes = answer.json()["result"]["data"]["heroes"]

    except (requests.RequestException, ValueError, KeyError):
        return []

    connection = get_connection()

    known = {
        row["name"]: row["localized_name"]
        for row in connection.execute("SELECT name, localized_name FROM heroes")
    }

    renamed = []

    for hero in heroes:
        name = hero.get("name")
        loc = hero.get("name_loc")

        if not name or not loc or name not in known or known[name] == loc:
            continue

        renamed.append((loc, name))

    connection.executemany(
        "UPDATE heroes SET localized_name = ? WHERE name = ?", renamed
    )

    connection.commit()
    connection.close()

    return [(known[name], loc) for loc, name in renamed]
