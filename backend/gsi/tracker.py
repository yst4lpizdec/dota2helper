"""Состояние текущего матча, собранное из потока GSI.

GSI показывает только то, что игрок видит прямо сейчас, и уважает туман
войны. Поэтому героев нужно накапливать: увидел один раз — помним
до конца матча, даже когда они ушли из поля зрения.
"""

import time


# Команды в minimap: 2 — Radiant, 3 — Dire.
RADIANT = 2
DIRE = 3

HERO_PREFIX = "npc_dota_hero_"


class MatchTracker:
    def __init__(self):
        self.reset()

    def reset(self, match_id=None):
        self.match_id = match_id
        self.my_hero = None
        self.player_team = None
        # герой -> {команда: сколько раз видели его за этой командой}.
        # Храним сырые наблюдения, а не готовые списки «свои/чужие»:
        # тогда ошибка в одном пакете не закрепляется до конца матча.
        self.sightings = {}
        self.game_state = None
        self.clock = None
        self.items = []
        self.gold = None
        self.updated_at = 0

    def update(self, state):
        """Обновляет состояние по очередному пакету GSI."""

        game_map = state.get("map") or {}
        match_id = game_map.get("matchid")

        # Новый матч — забываем всё, иначе притащим пик из прошлой игры.
        if match_id and match_id != self.match_id:
            self.reset(match_id)

        self.game_state = game_map.get("game_state")
        self.clock = game_map.get("clock_time")

        hero = state.get("hero") or {}

        if hero.get("name"):
            self.my_hero = hero["name"].replace(HERO_PREFIX, "")

        player = state.get("player") or {}

        if isinstance(player.get("gold"), int):
            self.gold = player["gold"]

        if player.get("team_name") == "radiant":
            self.player_team = RADIANT
        elif player.get("team_name") == "dire":
            self.player_team = DIRE

        for unit in (state.get("minimap") or {}).values():
            name = unit.get("unitname") or ""
            team = unit.get("team")

            if not name.startswith(HERO_PREFIX) or team not in (RADIANT, DIRE):
                continue

            short = name.replace(HERO_PREFIX, "")
            counts = self.sightings.setdefault(short, {})
            counts[team] = counts.get(team, 0) + 1

        items = state.get("items") or {}

        # Рюкзак учитываем тоже: предмет там уже куплен,
        # советовать его второй раз не нужно.
        self.items = [
            value.get("name")
            for key, value in sorted(items.items())
            if key.startswith(("slot", "backpack"))
            and value.get("name")
            and value.get("name") != "empty"
        ]

        self.updated_at = time.time()

    def team_of(self, hero):
        """Команда героя по большинству наблюдений.

        Большинство, а не последнее наблюдение: иллюзии и копии
        (Морфлинг, Рубик) иногда показывают героя за чужой командой.
        """

        counts = self.sightings.get(hero)

        if not counts:
            return None

        return max(counts, key=counts.get)

    def my_team(self):
        """Своя команда — в первую очередь по своему же герою на карте.

        Блок player в части пакетов приходит пустым, а сравнение
        с миникартой по одной и той же нумерации надёжнее.
        """

        if self.my_hero:
            team = self.team_of(self.my_hero)

            if team is not None:
                return team

        return self.player_team

    def sides(self):
        mine = self.my_team()

        if mine is None:
            # Пока своя команда неизвестна, делить героев нельзя вообще:
            # иначе тиммейты окажутся во врагах.
            return [], []

        allies = []
        enemies = []

        for hero in sorted(self.sightings):
            if self.team_of(hero) == mine:
                allies.append(hero)
            elif hero != self.my_hero:
                enemies.append(hero)

        return allies, enemies

    def snapshot(self):
        allies, enemies = self.sides()

        return {
            "match_id": self.match_id,
            "game_state": self.game_state,
            "clock": self.clock,
            "my_hero": self.my_hero,
            "my_team": self.my_team(),
            "allies": allies,
            "enemies": enemies,
            "enemies_known": len(enemies),
            "items": self.items,
            "gold": self.gold,
            "updated_at": self.updated_at,
        }
