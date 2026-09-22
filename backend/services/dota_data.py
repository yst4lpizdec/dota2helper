import requests

from config import OPENDOTA_API
from database.database import get_connection


def get_heroes():
    response = requests.get(
        f"{OPENDOTA_API}/heroStats",
        timeout=10,
    )

    response.raise_for_status()

    return response.json()


def save_heroes(heroes):
    connection = get_connection()

    connection.executemany(
        """
        INSERT OR REPLACE INTO heroes (
            id,
            name,
            localized_name
        )
        VALUES (?, ?, ?)
        """,
        [
            (
                hero["id"],
                hero["name"],
                hero["localized_name"],
            )
            for hero in heroes
        ],
    )

    connection.commit()
    connection.close()


def get_pro_matches():
    response = requests.get(
        f"{OPENDOTA_API}/proMatches",
        timeout=15,
    )

    response.raise_for_status()

    return response.json()


def get_match(match_id: int):
    response = requests.get(
        f"{OPENDOTA_API}/matches/{match_id}",
        timeout=15,
    )

    response.raise_for_status()

    return response.json()


def save_match(match):
    connection = get_connection()

    connection.execute(
        """
        INSERT OR REPLACE INTO matches (
            id,
            patch,
            start_time,
            duration,
            radiant_win
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            match["match_id"],
            match.get("patch"),
            match.get("start_time"),
            match.get("duration"),
            int(match.get("radiant_win", False)),
        ),
    )

    for player in match.get("players", []):
        player_slot = player["player_slot"]
        team = "Radiant" if player["isRadiant"] else "Dire"

        connection.execute(
            """
            INSERT OR REPLACE INTO match_players (
                match_id,
                player_slot,
                hero_id,
                team,
                win
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                match["match_id"],
                player_slot,
                player["hero_id"],
                team,
                int(player.get("win", False)),
            ),
        )

        connection.execute(
            """
            DELETE FROM player_purchases
            WHERE match_id = ? AND player_slot = ?
            """,
            (match["match_id"], player_slot),
        )

        for purchase in player.get("purchase_log") or []:
            if "key" not in purchase:
                continue

            connection.execute(
                """
                INSERT INTO player_purchases (
                    match_id,
                    player_slot,
                    item_name,
                    purchase_time
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    match["match_id"],
                    player_slot,
                    purchase["key"],
                    purchase.get("time", 0),
                ),
            )

        connection.execute(
            """
            DELETE FROM player_abilities
            WHERE match_id = ? AND player_slot = ?
            """,
            (match["match_id"], player_slot),
        )

        for level, ability_id in enumerate(
            player.get("ability_upgrades_arr") or [],
            start=1,
        ):
            connection.execute(
                """
                INSERT INTO player_abilities (
                    match_id,
                    player_slot,
                    ability_id,
                    level
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    match["match_id"],
                    player_slot,
                    ability_id,
                    level,
                ),
            )

    connection.commit()
    connection.close()
