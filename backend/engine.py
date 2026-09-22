"""Движок рекомендаций, работающий только на snapshot'е.

Ни базы, ни сети: snapshot загружается один раз при старте и живёт
в памяти. Это то, что будет крутиться у игрока локально во время матча.
"""

from services.aggregator import CONSUMABLES as SHOP_CONSUMABLES
from services.item_roles import role_of
from services.snapshot import load_snapshot


# Позиции, в порядке от кэрри к хардсаппорту.
# Во сколько раз сильнее учитывается матчап с тем, кто стоит
# против нас на линии. Ранняя закупка решается именно там.
LANE_WEIGHT = 2.5

# Слот ультимейта в раскладке способностей STRATZ.
ULTIMATE_SLOT = 6

# Восемь талантов в дереве: по паре на уровни 10, 15, 20 и 25. Если
# столько пришло, дереву можно верить и делать по нему выводы.
TALENT_SLOTS = 8

# Стартовое золото в обычном матче. Используется, только если GSI
# ещё не прислал реальное золото игрока.
DEFAULT_START_GOLD = 600

# Расходники: в ранней игре их покупают почти все и почти всегда, поэтому
# по доле они вытесняют из списка собственно предметы. Показываем их, но
# отдельной строкой — это разные вопросы: «что расходовать» и «что собирать».
#
# Список один на весь проект: своя копия здесь уже разъезжалась с той, по
# которой считаются гайды, и лотосы с каплями лезли в ряд предметов.
CONSUMABLES = SHOP_CONSUMABLES

# Доля игр, с которой предмет в ранней считается обязательным, а не
# ситуативным. Обязательное идёт первым: сначала ботинки, потом уже то,
# что берут в одной игре из двадцати.
STAPLE_SHARE = 15.0

# Насколько сильнее обычного должен подняться предмет против вражеского
# пика, чтобы о нём стоило говорить отдельно. Считается в процентных
# пунктах доли покупок, сложенных по всем известным врагам.
SITUATIONAL_SHIFT = 2.0

# С какой доли игр деталь считается настоящей ступенью сборки, по которой
# предметы можно считать роднёй. Ниже — это просто общая мелочь: Eul's и
# Kaya оба собираются через Staff of Wizardry, но развилкой не являются.
BRANCH_SHARE = 15.0

POSITIONS = [
    "POSITION_1",
    "POSITION_2",
    "POSITION_3",
    "POSITION_4",
    "POSITION_5",
]


class Engine:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot or load_snapshot()

        self.hero_by_name = {
            value["name"].replace("npc_dota_hero_", ""): key
            for key, value in self.snapshot["heroes"].items()
        }

        # Человеческое имя героя по техническому. В GSI и в наших данных
        # герои зовутся так, как их звали при выходе: nevermore, zuus,
        # obsidian_destroyer. Игрок этих имён не знает.
        self.hero_display = {
            value["name"].replace("npc_dota_hero_", ""): value["localized_name"]
            for value in self.snapshot["heroes"].values()
        }

        self.item_id_by_name = {
            value["name"]: key for key, value in self.snapshot["items"].items()
        }

        # Гайды хранят технические имена способностей; читаемые подписи
        # подставляем здесь, чтобы не пересчитывать гайды ради одних названий.
        self.ability_display = {
            value["name"]: value.get("display") or value["name"]
            for value in self.snapshot["abilities"].values()
        }

        self._slot_cache = {}
        self._icon_cache = {}
        self._parts_cache = {}
        self._guide_items = None

        self.costs = {
            value["name"]: value.get("cost") or 0
            for value in self.snapshot["items"].values()
        }

        self.display = {
            value["name"]: value.get("display") or value["name"]
            for value in self.snapshot["items"].values()
        }

        self.components = self.snapshot.get("components", {})

    def _without_parts(self, entries, components):
        """Выбрасывает из списка полуфабрикаты.

        Если игроки в ранней покупают и Tranquil Boots, и отдельно Boots
        с Wind Lace, то в списке это один совет, а не три: показываем
        собранный предмет, а его части убираем.
        """

        names = {entry["item"] for entry in entries}
        parts = set()

        for name in names:
            stack = list(components.get(name, []))

            while stack:
                part = stack.pop()

                if part in parts:
                    continue

                parts.add(part)
                stack.extend(components.get(part, []))

        return [entry for entry in entries if entry["item"] not in parts]

    def hero_id(self, short_name):
        return self.hero_by_name.get(short_name)

    def position_weights(self, hero_short):
        """Насколько типична каждая позиция для героя, по нашим матчам."""

        hero = self.hero_id(hero_short)
        weights = {}

        if hero is None:
            return weights

        for position in POSITIONS:
            guide = self.snapshot["guides"].get(f"{hero}:{position}")

            weights[position] = guide["matches"] if guide else 0

        total = sum(weights.values())

        if not total:
            return {}

        return {key: value / total for key, value in weights.items()}

    def assign_positions(self, hero_shorts):
        """Раскидывает пять позиций по пятёрке героев целиком.

        По одному герою позиция угадывается плохо: два кэрри в команде
        оба получат «поз. 1». Поэтому перебираем все 120 расстановок
        и берём ту, что в сумме правдоподобнее всего.
        """

        from itertools import permutations
        from math import log

        heroes = list(hero_shorts)[:5]

        if not heroes:
            return {}

        weights = {hero: self.position_weights(hero) for hero in heroes}

        best = None
        best_score = None

        for arrangement in permutations(POSITIONS, len(heroes)):
            score = 0.0

            for hero, position in zip(heroes, arrangement):
                # Логарифм, чтобы складывать, а не перемножать доли,
                # и чтобы нулевая вероятность не обнуляла всю расстановку.
                score += log(weights[hero].get(position, 0) or 1e-6)

            if best_score is None or score > best_score:
                best_score = score
                best = dict(zip(heroes, arrangement))

        return best or {}

    def lane_opponents(self, my_position, enemy_positions):
        """Кто стоит против меня на линии.

        В Доте лёгкая линия одной команды стоит против сложной линии
        другой, а мид — против мида.
        """

        opposite = {
            "POSITION_1": ("POSITION_3", "POSITION_4"),
            "POSITION_5": ("POSITION_3", "POSITION_4"),
            "POSITION_3": ("POSITION_1", "POSITION_5"),
            "POSITION_4": ("POSITION_1", "POSITION_5"),
            "POSITION_2": ("POSITION_2",),
        }

        wanted = opposite.get(my_position, ())

        return [
            hero
            for hero, position in enemy_positions.items()
            if position in wanted
        ]

    def guess_position(self, hero_short):
        """Угадывает позицию по тому, на какой её чаще всего играют."""

        hero = self.hero_id(hero_short)

        if hero is None:
            return None

        best = None
        best_matches = 0

        for position in POSITIONS:
            guide = self.snapshot["guides"].get(f"{hero}:{position}")

            if guide and guide["matches"] > best_matches:
                best = position
                best_matches = guide["matches"]

        return best

    def slot_map(self, hero, kind):
        """Слоты героя, которые находятся и по техническому имени,
        и по отображаемому.

        Гайды считаются по отображаемым именам («Mortal Strike»), а
        раскладка приходит с техническими («skeleton_king_mortal_strike»).
        Без перевода не находилось ничего: у способностей пропадал номер,
        у талантов — сторона в дереве.
        """

        cached = self._slot_cache.get((hero, kind))

        if cached is not None:
            return cached

        slots = {}
        layout = (self.snapshot.get("layout", {}).get(hero) or {}).get(kind, {})

        for name, slot in layout.items():
            slots[name] = slot

            display = self.ability_display.get(name)

            if display:
                slots.setdefault(display, slot)

        self._slot_cache[(hero, kind)] = slots

        return slots

    def icon_names(self, hero):
        """Отображаемое имя способности -> техническое.

        Гайды знают только «Bone Guard», а картинка на диске лежит под
        именем skeleton_king_bone_guard.
        """

        cached = self._icon_cache.get(hero)

        if cached is not None:
            return cached

        names = {}

        for name in (self.snapshot.get("layout", {}).get(hero) or {}).get(
            "abilities", {}
        ):
            names[name] = name

            display = self.ability_display.get(name)

            if display:
                names.setdefault(display, name)

        self._icon_cache[hero] = names

        return names

    def _skills(self, hero, entries):
        """Добавляет номер способности: «1-я», «2-я», «ульт»."""

        slots = self.slot_map(hero, "abilities")
        icons = self.icon_names(hero)

        result = []

        for entry in entries:
            slot = slots.get(entry["ability"])

            if slot == ULTIMATE_SLOT:
                mark = "ульт"
            elif slot:
                mark = f"{slot}-я"
            else:
                mark = ""

            result.append(
                {
                    **entry,
                    "display": self.ability_display.get(
                        entry["ability"], entry["ability"]
                    ),
                    "slot": slot,
                    "mark": mark,
                    "icon": icons.get(entry["ability"]),
                    # Без пометки: таблица способностей героя неполная
                    # (у Wraith King, например, нет слота 2 с Bone Guard),
                    # поэтому отсутствие слота ничего не доказывает —
                    # номер просто не показываем.
                    "stale": False,
                }
            )

        return result

    def _talents(self, hero, entries):
        """Ставит талант на его место в дереве: уровень и сторона.

        Уровень берём из дерева, а не из того, каким по счёту талант
        взяли в матче. Это разные вещи: дерево меняется от патча к патчу,
        и талант 25-го уровня, который в старых матчах стоял на 20-м,
        показывался на двадцатом до сих пор.

        Сторона — место в паре: первый талант пары в игре справа, второй
        слева. В русском интерфейсе английское название само по себе мало
        помогает, а «левый на 15-м» находится сразу.
        """

        slots = self.slot_map(hero, "talents")

        # Слоты, а не ключи: в карте у каждого таланта их два —
        # техническое имя и отображаемое.
        full_tree = len(set(slots.values())) >= TALENT_SLOTS

        result = []

        for entry in entries:
            slot = slots.get(entry["talent"])

            side = None
            level = entry.get("level")

            if slot is not None:
                side = "правый" if slot % 2 == 0 else "левый"
                level = 10 + 5 * (slot // 2)

            result.append(
                {
                    **entry,
                    "level": level,
                    "display": self.ability_display.get(
                        entry["talent"], entry["talent"]
                    ),
                    "slot": slot,
                    "side": side,
                    # У талантов дерево приходит целиком, все восемь
                    # слотов. Значит, если таланта в нём нет — его убрали
                    # или переделали, а в наших матчах он ещё живёт.
                    "stale": slot is None and full_tree,
                }
            )

        # Порядок берётся из дерева, а не из того, как легли записи гайда:
        # после переноса по уровням они иначе идут вразнобой. Если на один
        # уровень попали два таланта из разных патчей, оставляем тот, что
        # берут чаще: пара в дереве одна, и совет тоже один.
        best = {}
        stale = []

        for entry in sorted(result, key=lambda item: -(item.get("share") or 0)):
            if entry["slot"] is None:
                stale.append(entry)

                continue

            best.setdefault(entry["level"], entry)

        # Талант из старого патча оставляем на том уровне, на котором его
        # брали в матчах, и ставим после живого: строки идут по уровням,
        # а не «сначала настоящие, потом остальные».
        return sorted(
            list(best.values()) + stale,
            key=lambda item: (item.get("level") or 99, item["slot"] is None),
        )

    def start_budget(self, gold, owned):
        """Бюджет старта: золото на руках плюс то, что уже куплено.

        Без второго слагаемого рекомендация перестраивалась бы прямо
        посреди закупки: купил Tango — золото уменьшилось — «влезает»
        уже другой набор.
        """

        if not gold:
            return DEFAULT_START_GOLD

        return gold + sum(self.costs.get(name, 0) for name in owned)

    def guide_items(self):
        """В скольких сборках встречается каждый предмет.

        Сборка — это герой на позиции. Предмет часто попадает сразу в две
        фазы одной сборки (кто-то собрал к седьмой минуте, кто-то к
        семнадцатой), и считать это за две значило бы разойтись с
        отчётом по предмету, где такие случаи сведены в одну строку.
        """

        if self._guide_items is None:
            counts = {}

            for key, guide in self.snapshot["guides"].items():
                seen = {
                    entry["item"]
                    for phase in ("early", "core", "late")
                    for entry in guide[phase]
                }

                for name in seen:
                    counts[name] = counts.get(name, 0) + 1

            self._guide_items = counts

        return self._guide_items

    def item_report(self, name):
        """Кто берёт этот предмет: герой, позиция, доля, тайминг, винрейт.

        Гайды хранят только то, что берут не реже чем в 2% игр, и герои
        с хотя бы 30 матчами на позиции. Поэтому пустой ответ значит не
        «никогда», а «нигде не дотянул до порога» — так и говорим.
        """

        item = None

        for value in self.snapshot["items"].values():
            if value["name"] == name:
                item = value

                break

        if item is None:
            return {"error": f"нет такого предмета: {name}"}

        heroes = {
            key: value["localized_name"]
            for key, value in self.snapshot["heroes"].items()
        }

        rows = []

        for key, guide in self.snapshot["guides"].items():
            hero_id, position = key.split(":", 1)

            for phase in ("early", "core", "late"):
                for entry in guide[phase]:
                    if entry["item"] != name:
                        continue

                    rows.append(
                        {
                            "hero": heroes.get(hero_id, hero_id),
                            "short": self.snapshot["heroes"][hero_id][
                                "name"
                            ].replace("npc_dota_hero_", ""),
                            "position": position,
                            "phase": phase,
                            "share": entry["share"],
                            "winrate": entry["winrate"],
                            "lift": entry["lift"],
                            "median_time": entry["median_time"],
                            "buyers": entry["matches"],
                            "of": guide["matches"],
                        }
                    )

        # Один и тот же предмет попадает в две фазы у одного героя: кто-то
        # собрал его к седьмой минуте, кто-то к семнадцатой. В отчёте это
        # одна строка — берём ту фазу, где предмет берут чаще.
        best = {}

        for row in rows:
            slot = (row["short"], row["position"])
            known = best.get(slot)

            if known is None or row["share"] > known["share"]:
                best[slot] = row

        rows = sorted(best.values(), key=lambda row: -row["share"])

        return {
            "item": item["name"],
            "display": item["display"],
            "cost": item.get("cost") or 0,
            "rows": rows,
            "guides": len(self.snapshot["guides"]),
        }

    def _mark_alternatives(self, result):
        """Помечает предметы, которые собираются вместо уже показанного.

        Yasha and Kaya и Kaya and Sange — это не два совета подряд, а
        развилка: в обоих лежит Kaya, и собирают ровно один из двух.
        В ряду они стояли рядом и читались как «возьми оба».

        Роднёй считаем только те, что делят деталь, которую этот же гайд
        советует отдельно, — здесь Kaya. Делить просто любую деталь
        нельзя: Octarine Core и Refresher Orb оба собираются через Tiara
        of Selemene, но их спокойно берут вместе, и разводить их по
        разным рядам было бы враньём.
        """

        phases = ("early", "core", "late")

        # Насколько часто гайд советует сам этот предмет.
        share_of = {
            entry["item"]: entry["share"]
            for phase in phases
            for entry in result.get(phase) or []
        }

        for phase in phases:
            entries = result.get(phase) or []

            # Предмет -> общие детали, по которым его вообще можно с чем-то
            # спутать: только те, что гайд советует и сам по себе, и не
            # мелочь вроде Staff of Wizardry. Без этого порога Eul's и Kaya
            # оказывались «одним и тем же»: у них общий посох за 900.
            shared = {
                entry["item"]: {
                    part
                    for part in self.components.get(entry["item"]) or []
                    if share_of.get(part, 0) >= BRANCH_SHARE
                }
                for entry in entries
            }

            taken = {}

            # По убыванию доли: первым идёт тот, кого собирают чаще, он и
            # остаётся в ряду, остальные ветки уходят в ситуативные.
            for entry in sorted(entries, key=lambda item: -item["share"]):
                name = entry["item"]
                marked = False

                for part in shared[name]:
                    other = taken.get(part)

                    # Апгрейд — не развилка: Kaya и Yasha and Kaya берут
                    # один за другим, а не вместо друг друга.
                    if not other or self._related(name, other["item"]):
                        continue

                    entry["alt_to"] = other["display"]
                    marked = True

                    break

                if marked:
                    continue

                for part in shared[name]:
                    taken.setdefault(part, {
                        "item": name,
                        "display": entry.get("display", name),
                    })

    def _blamed(self, sources, lane_ids, top=3):
        """Кто из врагов поднял этот предмет и насколько.

        Сдвиг — это «на столько процентов чаще предмет берут против него».
        Показываем двух-трёх главных виновников: список из пяти читается
        уже как шум.
        """

        if not sources:
            return []

        heroes = self.snapshot["heroes"]

        ranked = sorted(sources, key=lambda pair: -pair[1])[:top]

        return [
            {
                "hero": heroes[str(enemy)]["name"],
                "display": heroes[str(enemy)]["localized_name"],
                "shift": round(shift, 1),
                "lane": enemy in lane_ids,
            }
            for enemy, shift in ranked
            if str(enemy) in heroes
        ]

    def _related(self, first, second):
        """Один из предметов собирается из другого."""

        return second in self._parts_of(first) or first in self._parts_of(second)

    def _parts_of(self, name):
        """Все детали предмета, включая детали деталей."""

        cached = self._parts_cache.get(name)

        if cached is not None:
            return cached

        parts = set()
        stack = list(self.components.get(name) or [])

        while stack:
            part = stack.pop()

            if part in parts:
                continue

            parts.add(part)
            stack.extend(self.components.get(part) or [])

        self._parts_cache[name] = parts

        return parts

    def _recipe_of(self, name):
        """Рецепт предмета, если он у него есть и стоит денег."""

        recipe = "item_recipe_" + name[len("item_"):]

        return recipe if self.costs.get(recipe) else None

    def _build_from(self, name, counts):
        """Сколько каких частей уйдёт на сборку, если она вообще выходит.

        Справочник частей хранит их без повторов: у Magic Wand там стоят
        Magic Stick и Iron Branch, хотя веток нужно две. Поэтому кратность
        подбираем по цене — сумма частей вместе с рецептом обязана в точности
        совпасть с ценой готового предмета. Совпала — значит игрок правда
        собирал именно его, а не держал похожий набор мелочи.
        """

        from itertools import product

        parts = self.components.get(name) or []

        if not parts or any(part not in counts for part in parts):
            return None

        recipe = self._recipe_of(name)
        spend = self.costs.get(name, 0)

        if recipe:
            if not counts.get(recipe):
                return None

            spend -= self.costs[recipe]

        ranges = [range(1, min(counts[part], 4) + 1) for part in parts]

        for combination in product(*ranges):
            total = sum(
                self.costs.get(part, 0) * times
                for part, times in zip(parts, combination)
            )

            if total == spend:
                used = dict(zip(parts, combination))

                if recipe:
                    used[recipe] = 1

                return used

        return None

    def assemble(self, entries):
        """Собирает купленные части в то, что из них получается.

        В закупке игрок берёт Circlet, Mantle и рецепт — а в инвентаре у
        него Null Talisman. Панель показывала три позиции вместо одной,
        причём рецепт ещё и без картинки: своих иконок у рецептов нет.
        """

        counts = {}
        order = []

        for entry in entries:
            name = entry["item"]

            if name not in counts:
                order.append(name)

            counts[name] = counts.get(name, 0) + entry.get("count", 1)

        changed = True

        while changed:
            changed = False

            # Сначала то, что собирается из большего числа частей: иначе
            # Magic Stick уйдёт в мелочь раньше, чем в Magic Wand.
            candidates = sorted(
                self.components,
                key=lambda name: (
                    -len(self.components[name]),
                    -self.costs.get(name, 0),
                ),
            )

            for name in candidates:
                used = self._build_from(name, counts)

                if not used:
                    continue

                for part, times in used.items():
                    counts[part] -= times

                    if counts[part] <= 0:
                        counts.pop(part)
                        order.remove(part)

                if name not in counts:
                    order.append(name)

                counts[name] = counts.get(name, 0) + 1
                changed = True

                break

        return [
            {
                "item": name,
                "display": self.display.get(name, name),
                "count": counts[name],
                "cost": self.costs.get(name, 0),
            }
            for name in order
        ]

    def starting_purchase(self, guide, budget):
        """Стартовая закупка, которая влезает в бюджет.

        Берём самый частый целый набор, который реально собирали игроки
        и который помещается в золото. Средние по предметам тут врут:
        «Tango 0.8, Iron Branch 0.7» — это смесь разных закупок,
        и в сумме она легко выходит дороже стартовых 600.
        """

        for option in guide.get("starting_sets") or []:
            if option["total"] <= budget:
                return {
                    **option,
                    "items": self.assemble(option["items"]),
                    "budget": budget,
                    "fitted": True,
                }

        # Ни один набор не влез (или гайд старый, без наборов):
        # набираем по популярности, пока хватает денег.
        return self._starting_fallback(guide, budget)

    def _starting_fallback(self, guide, budget):
        """Закупка, собранная по частоте предметов, а не целым набором."""

        costs = self.costs

        chosen = []
        total = 0

        for entry in guide.get("starting", []):
            price = costs.get(entry["item"], 0)
            count = max(1, round(entry.get("avg_count", 1)))

            while count and total + price <= budget:
                total += price
                count -= 1

                if chosen and chosen[-1]["item"] == entry["item"]:
                    chosen[-1]["count"] += 1
                else:
                    chosen.append(
                        {
                            "item": entry["item"],
                            "display": entry.get("display", entry["item"]),
                            "count": 1,
                            "cost": price,
                        }
                    )

        return {
            "items": self.assemble(chosen),
            "total": total,
            "share": None,
            "budget": budget,
            "fitted": False,
        }

    def recommend(
        self,
        hero_short,
        position=None,
        enemy_shorts=(),
        owned=(),
        ally_shorts=(),
        lane_against=(),
        gold=None,
    ):
        hero = self.hero_id(hero_short)

        if hero is None:
            return {"error": f"неизвестный герой: {hero_short}"}

        # По каким позициям на этом герое у нас вообще есть матчи. Панель
        # рисует ими переключатель: выбрать позицию, для которой данных
        # нет, нельзя, и тупика «нет данных» не возникает.
        known = [
            {
                "position": key,
                "matches": self.snapshot["guides"][f"{hero}:{key}"]["matches"],
            }
            for key in POSITIONS
            if f"{hero}:{key}" in self.snapshot["guides"]
        ]

        chosen = position

        # Позицию точнее видно по всей своей пятёрке, чем по одному герою.
        my_roles = self.assign_positions(ally_shorts) if ally_shorts else {}

        position = (
            position
            or my_roles.get(hero_short)
            or self.guess_position(hero_short)
        )
        guide = self.snapshot["guides"].get(f"{hero}:{position}")

        if not guide:
            return {
                "error": f"нет данных: {hero_short} на {position}",
                "hero": hero_short,
                "positions": known,
            }

        enemies = [
            self.hero_id(name)
            for name in enemy_shorts
            if self.hero_id(name) is not None
        ]

        # Кто стоит против нас на линии: либо указано вручную, либо
        # выводится из расстановки позиций.
        enemy_roles = (
            self.assign_positions(enemy_shorts) if len(enemy_shorts) >= 4 else {}
        )

        if lane_against:
            lane = [name for name in lane_against if name in enemy_shorts]
        else:
            lane = (
                self.lane_opponents(position, enemy_roles) if enemy_roles else []
            )

        lane_ids = {self.hero_id(name) for name in lane}

        # Складываем сдвиги по всем известным врагам.
        # Ранняя игра решается на линии, поэтому вклад лайн-оппонента
        # там весит больше, чем вклад врага с другой линии.
        boost = {}
        early_boost = {}
        matchups = self.snapshot["matchups"].get(hero, {})

        # Кто именно поднял предмет. Без этого «ситуативно» — просто
        # список без ответа на «почему он тут».
        blame = {}

        for enemy in enemies:
            weight = LANE_WEIGHT if enemy in lane_ids else 1.0

            for item_id, shift in matchups.get(enemy, {}).items():
                boost[item_id] = boost.get(item_id, 0) + shift
                early_boost[item_id] = (
                    early_boost.get(item_id, 0) + shift * weight
                )

                if shift > 0:
                    blame.setdefault(item_id, []).append((enemy, shift))

        result = {
            "hero": hero_short,
            "hero_display": self.hero_display.get(hero_short, hero_short),
            "position": position,
            "positions": known,
            # Позиция выбрана игроком или угадана нами — панель помечает
            # угаданную, чтобы её было понятно поправить.
            "position_manual": bool(chosen),
            "matches": guide["matches"],
            "winrate": guide["winrate"],
            "enemies_known": len(enemies),
            "starting": guide["starting"][:8],
            "starting_purchase": self.starting_purchase(
                guide, self.start_budget(gold, owned)
            ),
            "skills": self._skills(hero, guide["skills"]),
            "talents": self._talents(hero, guide["talents"]),
        }

        # Что уже лежит в инвентаре, советовать незачем — показываем,
        # что брать дальше.
        in_bag = set(owned)

        components = self.snapshot.get("components", {})

        # Один предмет часто попадает сразу в две фазы: кто-то собрал
        # Glimmer к седьмой минуте, большинство — к семнадцатой. Это один
        # совет, а не два, поэтому оставляем предмет там, где его берут
        # чаще всего. Расходников это не касается: они живут своей строкой.
        home = {}

        for phase in ("early", "core", "late"):
            for entry in guide[phase]:
                if entry["item"] in CONSUMABLES:
                    continue

                known = home.get(entry["item"])

                if known is None or entry["share"] > known[1]:
                    home[entry["item"]] = (phase, entry["share"])

        for phase in ("early", "core", "late"):
            items = []

            source = early_boost if phase == "early" else boost

            for entry in guide[phase]:
                if entry["item"] in in_bag:
                    continue

                if home.get(entry["item"], (phase,))[0] != phase:
                    continue

                item_id = self.item_id_by_name.get(entry["item"])
                gain = source.get(item_id, 0) if item_id else 0

                items.append(
                    {
                        **entry,
                        "boost": round(gain, 1),
                        "cost": self.costs.get(entry["item"], 0),
                        "role": role_of(entry["item"]),
                        "because": self._blamed(
                            blame.get(item_id), lane_ids
                        ) if gain >= 2 else [],
                    }
                )

            if phase == "early":
                result["consumables"] = sorted(
                    [entry for entry in items if entry["item"] in CONSUMABLES],
                    key=lambda entry: -entry["share"],
                )[:8]

                items = self._without_parts(
                    [
                        entry
                        for entry in items
                        if entry["item"] not in CONSUMABLES
                    ],
                    components,
                )

                # В ранней игре порядок задаёт не сила против пика, а
                # деньги: сначала обязательное, в том порядке, в каком его
                # успевают купить. Иначе наверх всплывал предмет за 3000,
                # который по факту берут к двадцатой минуте, и он закрывал
                # собой ботинки.
                items.sort(
                    key=lambda entry: (
                        0 if entry["share"] >= STAPLE_SHARE else 1,
                        entry.get("median_time") or 10 ** 6,
                        entry.get("cost") or 0,
                        -entry["share"],
                    )
                )
            else:
                items = [
                    entry
                    for entry in items
                    if entry["item"] not in CONSUMABLES
                ]

                # Порядок — по тому, как часто предмет реально собирают.
                # Сортировать сам билд по силе против пика нельзя: наверх
                # лезет предмет из 2% игр, а привычный Glimmer уезжает вниз.
                # Про пик говорят зелёная рамка на иконке и отдельная
                # секция «редкое, но сильное».
                items.sort(key=lambda entry: (-entry["share"], -entry["boost"]))

            items = items[:8]

            # Отбор в ряд делает популярность (сортировка выше), а
            # показываем по времени: ряд читается слева направо как
            # порядок покупок, без прыжков «1м, 3м, 4м, 6м, 4м, 6м».
            items.sort(key=lambda entry: entry.get("median_time") or 10 ** 6)

            result[phase] = items

        self._mark_alternatives(result)

        result["owned"] = sorted(in_bag)

        # Имена всех, кто попал в этот совет: панель подписывает ими
        # портреты врагов и союзников, а гадать по техническому имени
        # («Zuus») игроку неоткуда.
        result["names"] = {
            name: self.hero_display.get(name, name)
            for name in set(
                [hero_short, *(enemy_shorts or []), *(ally_shorts or []), *lane]
            )
        }

        result["enemy_positions"] = enemy_roles
        result["lane_against"] = lane
        result["lane_manual"] = bool(lane_against)

        in_build = {
            entry["item"]
            for phase in ("early", "core", "late")
            for entry in result[phase]
        }

        situational = []

        for item_id, score in sorted(boost.items(), key=lambda kv: -kv[1]):
            item = self.snapshot["items"].get(item_id)

            if not item or score < SITUATIONAL_SHIFT:
                continue

            # Предмет из основной сборки отсюда раньше выбрасывался, и
            # блок «против этого пика» чаще всего оказывался пустым: почти
            # всё, что поднимает вражеский пик, и так стоит в билде. Но
            # ответ «возьми Glimmer раньше обычного, потому что у них Nyx»
            # — это и есть то, зачем сюда смотрят. Поэтому оставляем и
            # помечаем, что предмет уже в плане.
            if item["name"] in in_bag:
                continue

            # Расходники сюда попадают из старых матчапных таблиц. Совет
            # «возьми лотос, потому что у них Nyx» — не совет.
            if item["name"] in CONSUMABLES:
                continue

            situational.append(
                {
                    "item": item["name"],
                    "display": item["display"],
                    "score": round(score, 1),
                    "in_build": item["name"] in in_build,
                    # Против кого — из матчей, зачем — из словаря ролей.
                    "because": self._blamed(blame.get(item_id), lane_ids),
                    "role": role_of(item["name"]),
                }
            )

        # Сначала то, чего в плане ещё нет: это новый совет, а не
        # напоминание про предмет, который и так собираешься брать.
        situational.sort(key=lambda entry: (entry["in_build"], -entry["score"]))

        # Boots of Travel и Boots of Travel 2 — это один совет, а не два;
        # то же с Dagon разных уровней. Оставляем старшую ступень.
        situational = self._without_parts(situational, components)

        result["situational"] = situational[:6]

        neutral = guide.get("neutral")

        if neutral:
            item = self.snapshot["items"].get(str(neutral["item_id"]))

            result["neutral"] = {
                "display": item["display"] if item else neutral["item_id"],
                "share": neutral["share"],
            }
        else:
            result["neutral"] = None

        return result
