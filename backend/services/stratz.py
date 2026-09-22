import time

import requests

from config import STRATZ_API, STRATZ_API_KEY


# Реальные лимиты из заголовков x-ratelimit-*: 8/сек, 150/мин, 1500/час.
#
# Связывает именно часовой: 1500 вызовов в час — это один в 2.4 секунды,
# и пауза в 0.4 секунды его проедала за семь минут. Пока за вызов
# приезжала сотня матчей, это было незаметно; при досборе свежего патча
# матчей на вызов всего несколько, и сбор упирается в число вызовов.
MIN_INTERVAL = 2.5

# Сколько матчей забираем за один вызов. STRATZ отдаёт 100 матчей
# с полными деталями примерно за полторы секунды, списывая один вызов.
MATCHES_PER_CALL = 100

MATCH_FIELDS = """{
    id
    gameVersionId
    startDateTime
    durationSeconds
    didRadiantWin
    players {
        playerSlot
        steamAccountId
        heroId
        isRadiant
        isVictory
        position
        lane
        role
        neutral0Id
        stats { itemPurchases { itemId time } }
        abilities { abilityId time level }
    }
}"""

_last_call = 0.0


def _throttle():
    global _last_call

    wait = MIN_INTERVAL - (time.monotonic() - _last_call)

    if wait > 0:
        time.sleep(wait)

    _last_call = time.monotonic()


def query(graphql, retries=5):
    if not STRATZ_API_KEY:
        raise RuntimeError(
            "Не задан STRATZ_API_KEY. Впиши токен в файл .env в корне проекта."
        )

    headers = {
        "Authorization": f"Bearer {STRATZ_API_KEY}",
        # Внимание: User-Agent "STRATZ_API" включает проверку
        # "100 public games" и даёт 403. Используем своё имя.
        "User-Agent": "Dota2Helper/0.1",
    }

    for attempt in range(retries):
        _throttle()

        try:
            response = requests.post(
                STRATZ_API,
                json={"query": graphql},
                headers=headers,
                timeout=60,
            )

        except requests.RequestException as error:
            # Обрыв связи или таймаут: до кода ответа дело не дошло,
            # поэтому ловим отдельно, иначе долгий сбор умирает целиком.
            pause = 5 * (attempt + 1)

            print(f"  Сеть недоступна ({error}), повтор через {pause} сек...")
            time.sleep(pause)

            continue

        if response.status_code == 429:
            # Часовой лимит коротким ожиданием не пересидеть, а STRATZ
            # обычно сам говорит, сколько ждать.
            pause = int(response.headers.get("Retry-After") or 0) or 60 * (
                attempt + 1
            )

            print(f"  Лимит запросов, ждём {pause} сек...")
            time.sleep(pause)

            continue

        if response.status_code >= 500:
            pause = 3 * (attempt + 1)

            print(
                f"  STRATZ недоступен ({response.status_code}), "
                f"повтор через {pause} сек..."
            )
            time.sleep(pause)

            continue

        response.raise_for_status()

        payload = response.json()

        if "errors" in payload:
            raise RuntimeError(payload["errors"])

        return payload["data"]

    raise RuntimeError("STRATZ не ответил после нескольких попыток.")


def get_constants():
    """Справочники предметов и способностей (с флагом таланта)."""

    data = query(
        """{
            constants {
                items { id name }
                abilities {
                    id
                    name
                    isTalent
                    language { displayName }
                    attributes { name value }
                }
            }
        }"""
    )

    return data["constants"]


def get_current_game_version():
    data = query("{ constants { gameVersions { id name } } }")

    versions = data["constants"]["gameVersions"]

    return versions[0]


def get_leaderboard_accounts():
    """Аккаунты из сезонных лидербордов — стартовая точка обхода."""

    divisions = ["EUROPE", "AMERICAS", "CHINA", "SE_ASIA"]

    aliases = " ".join(
        f"""d{index}: season(request:{{leaderBoardDivision:{division}}}) {{
            players {{ steamAccountId }}
        }}"""
        for index, division in enumerate(divisions)
    )

    data = query("{ leaderboard { " + aliases + " } }")

    accounts = set()

    for board in data["leaderboard"].values():
        for player in (board or {}).get("players") or []:
            if player.get("steamAccountId"):
                accounts.add(player["steamAccountId"])

    return accounts


def get_player_matches(
    steam_account_id, game_versions=None, take=MATCHES_PER_CALL, after=None
):
    """Матчи одного игрока сразу с предметами, способностями и позициями.

    Берём только распарсенные матчи — у них есть purchase/ability данные.

    `after` — не брать матчи раньше этого времени (секунды эпохи). Это
    единственный надёжный способ отобрать матчи нужного патча: номера
    версий у STRATZ застряли на 7.40b, и `gameVersionIds` ничего не
    отсекает. Патч определяется датой выхода, дата матча — этим полем.
    """

    filters = [f"take:{take}", "isParsed:true"]

    if after:
        filters.append(f"startDateTime:{int(after)}")

    if game_versions:
        filters.append(
            "gameVersionIds:[" + ",".join(str(v) for v in game_versions) + "]"
        )

    data = query(
        f"""{{
            player(steamAccountId: {steam_account_id}) {{
                matches(request:{{{", ".join(filters)}}}) {MATCH_FIELDS}
            }}
        }}"""
    )

    player = data.get("player")

    if not player:
        return []

    return [match for match in (player.get("matches") or []) if match]
