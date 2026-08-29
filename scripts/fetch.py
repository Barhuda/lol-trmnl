"""Fetch League of Legends Solo/Duo stats from the Riot API and write data.json.

Reads RIOT_API_KEY (secret) and RIOT_ID (e.g. "Name#TAG") from the environment.
Region is fixed to EUW (platform euw1, regional routing europe).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict

PLATFORM = "euw1"
REGION = "europe"
MATCH_COUNT = 20
QUEUE_SOLO_DUO = 420  # RANKED_SOLO_5x5
LP_HISTORY_LENGTH = 20
CHART_WIDTH = 748
CHART_HEIGHT = 32
OPPONENT_RANK_MATCH_COUNT = 3  # only the most recent N matches get an avg-opponent-rank lookup

TIER_ORDER = [
    "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM",
    "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
]
DIVISION_ORDER = {"IV": 0, "III": 1, "II": 2, "I": 3}
DIVISION_LABELS = ["IV", "III", "II", "I"]


def api_get(url: str, api_key: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "X-Riot-Token": api_key,
            "User-Agent": "Mozilla/5.0 (compatible; lol-trmnl/1.0)",
            "Accept": "application/json",
        },
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                retry_after = int(e.headers.get("Retry-After", "1"))
                time.sleep(retry_after + 1)
                continue
            raise
    raise RuntimeError(f"Failed to fetch {url} after retries")


def get_puuid(game_name: str, tag_line: str, api_key: str) -> str:
    url = f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    return api_get(url, api_key)["puuid"]


def get_ranked_stats(puuid: str, api_key: str) -> dict | None:
    url = f"https://{PLATFORM}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    entries = api_get(url, api_key)
    for entry in entries:
        if entry.get("queueType") == "RANKED_SOLO_5x5":
            wins = entry["wins"]
            losses = entry["losses"]
            total = wins + losses
            return {
                "tier": entry.get("tier"),
                "rank": entry.get("rank"),
                "leaguePoints": entry.get("leaguePoints"),
                "wins": wins,
                "losses": losses,
                "winrate": round(100 * wins / total, 1) if total else None,
            }
    return None


def get_match_ids(puuid: str, api_key: str, count: int) -> list[str]:
    url = (
        f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
        f"?start=0&count={count}&queue={QUEUE_SOLO_DUO}"
    )
    return api_get(url, api_key)


def get_match(match_id: str, api_key: str) -> dict:
    url = f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/{match_id}"
    return api_get(url, api_key)


def get_ddragon_version() -> str:
    url = "https://ddragon.leagueoflegends.com/api/versions.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; lol-trmnl/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))[0]


def rank_score(tier: str, rank: str | None, lp: int) -> int:
    tier_idx = TIER_ORDER.index(tier)
    div_idx = DIVISION_ORDER.get(rank, 0)
    return tier_idx * 400 + div_idx * 100 + lp


def score_to_rank_label(score: float) -> str:
    tier_idx = min(int(score // 400), len(TIER_ORDER) - 1)
    remainder = score - tier_idx * 400
    div_idx = min(int(remainder // 100), 3)
    tier = TIER_ORDER[tier_idx]
    if tier_idx >= TIER_ORDER.index("MASTER"):
        return tier.capitalize()
    return f"{tier.capitalize()} {DIVISION_LABELS[div_idx]}"


def get_avg_opponent_rank(match: dict, puuid: str, api_key: str) -> str | None:
    info = match["info"]
    me = next(p for p in info["participants"] if p["puuid"] == puuid)
    opponents = [p for p in info["participants"] if p["teamId"] != me["teamId"]]

    scores = []
    for opp in opponents:
        stats = get_ranked_stats(opp["puuid"], api_key)
        if stats and stats["tier"]:
            scores.append(rank_score(stats["tier"], stats["rank"], stats["leaguePoints"] or 0))

    if not scores:
        return None
    return score_to_rank_label(sum(scores) / len(scores))


def summarize_matches(match_ids: list[str], puuid: str, api_key: str) -> tuple[list[dict], dict]:
    matches = []
    champ_stats: dict[str, dict] = defaultdict(lambda: {"games": 0, "wins": 0})

    for index, match_id in enumerate(match_ids):
        match = get_match(match_id, api_key)
        info = match["info"]
        participant = next(p for p in info["participants"] if p["puuid"] == puuid)

        duration_min = max(info["gameDuration"] / 60, 1 / 60)
        kills = participant["kills"]
        deaths = participant["deaths"]
        assists = participant["assists"]
        kda = round((kills + assists) / deaths, 2) if deaths else float(kills + assists)
        cs = participant["totalMinionsKilled"] + participant["neutralMinionsKilled"]
        cs_per_min = round(cs / duration_min, 2)
        win = participant["win"]
        champion = participant["championName"]
        items = [participant[f"item{i}"] for i in range(6) if participant[f"item{i}"]]
        damage_dealt = participant["totalDamageDealtToChampions"]
        vision_score = participant["visionScore"]
        gold_earned = participant["goldEarned"]
        avg_opponent_rank = (
            get_avg_opponent_rank(match, puuid, api_key) if index < OPPONENT_RANK_MATCH_COUNT else None
        )

        matches.append(
            {
                "matchId": match_id,
                "champion": champion,
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "kda": kda,
                "csPerMin": cs_per_min,
                "win": win,
                "gameCreation": info["gameCreation"],
                "durationMin": round(duration_min),
                "position": participant.get("individualPosition"),
                "damageDealt": damage_dealt,
                "damagePerMin": round(damage_dealt / duration_min),
                "damageTaken": participant["totalDamageTaken"],
                "goldEarned": gold_earned,
                "goldPerMin": round(gold_earned / duration_min),
                "visionScore": vision_score,
                "visionScorePerMin": round(vision_score / duration_min, 2),
                "avgOpponentRank": avg_opponent_rank,
                "items": items,
            }
        )

        champ_stats[champion]["games"] += 1
        if win:
            champ_stats[champion]["wins"] += 1

    top_champions = Counter({c: s["games"] for c, s in champ_stats.items()}).most_common(4)
    top_champion_stats = []
    for champ, games in top_champions:
        wins = champ_stats[champ]["wins"]
        top_champion_stats.append(
            {
                "champion": champ,
                "games": games,
                "wins": wins,
                "winrate": round(100 * wins / games, 1) if games else None,
            }
        )

    avg_kda = round(sum(m["kda"] for m in matches) / len(matches), 2) if matches else None
    avg_cs_per_min = round(sum(m["csPerMin"] for m in matches) / len(matches), 2) if matches else None

    return matches, {
        "topChampions": top_champion_stats,
        "avgKda": avg_kda,
        "avgCsPerMin": avg_cs_per_min,
    }


def load_previous_data() -> dict | None:
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def update_lp_history(previous: dict | None, ranked_stats: dict | None, timestamp: str) -> list[dict]:
    history = list(previous.get("lpHistory", [])) if previous else []

    # backfill entries written before rankScore existed
    for entry in history:
        if "rankScore" not in entry:
            entry["rankScore"] = rank_score(entry["tier"], entry["rank"], entry["leaguePoints"])

    if not ranked_stats:
        return history

    total_games = ranked_stats["wins"] + ranked_stats["losses"]
    last_total_games = history[-1]["totalGames"] if history else None

    if last_total_games != total_games:
        history.append(
            {
                "totalGames": total_games,
                "leaguePoints": ranked_stats["leaguePoints"],
                "tier": ranked_stats["tier"],
                "rank": ranked_stats["rank"],
                # continuous tier+division+LP scale, so promotions/demotions show as a
                # rise/drop instead of the raw LP reset (e.g. Silver I 90 -> Gold IV 0)
                "rankScore": rank_score(ranked_stats["tier"], ranked_stats["rank"], ranked_stats["leaguePoints"]),
                "timestamp": timestamp,
            }
        )

    return history[-LP_HISTORY_LENGTH:]


def build_lp_chart(history: list[dict]) -> dict:
    if not history:
        return {"points": "", "min": None, "max": None}

    scores = [h["rankScore"] for h in history]
    lo, hi = min(scores), max(scores)
    span = hi - lo or 1

    if len(history) == 1:
        xs = [CHART_WIDTH / 2]
    else:
        step = CHART_WIDTH / (len(history) - 1)
        xs = [round(i * step, 1) for i in range(len(history))]

    points = " ".join(
        f"{x},{round(CHART_HEIGHT - (score - lo) / span * CHART_HEIGHT, 1)}"
        for x, score in zip(xs, scores)
    )

    return {"points": points, "min": score_to_rank_label(lo), "max": score_to_rank_label(hi)}


def main() -> None:
    api_key = os.environ.get("RIOT_API_KEY")
    riot_id = os.environ.get("RIOT_ID")
    if not api_key or not riot_id:
        print("RIOT_API_KEY and RIOT_ID must be set", file=sys.stderr)
        sys.exit(1)
    if "#" not in riot_id:
        print("RIOT_ID must be in the form 'GameName#TagLine'", file=sys.stderr)
        sys.exit(1)

    game_name, tag_line = riot_id.split("#", 1)

    previous = load_previous_data()
    puuid = get_puuid(game_name, tag_line, api_key)
    ranked_stats = get_ranked_stats(puuid, api_key)
    match_ids = get_match_ids(puuid, api_key, MATCH_COUNT)
    matches, champion_summary = summarize_matches(match_ids, puuid, api_key)
    ddragon_version = get_ddragon_version()

    updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lp_history = update_lp_history(previous, ranked_stats, updated_at)
    lp_chart = build_lp_chart(lp_history)

    data = {
        "updatedAt": updated_at,
        "riotId": riot_id,
        "region": "EUW",
        "ddragonVersion": ddragon_version,
        "soloDuo": ranked_stats,
        "recentMatches": matches,
        "topChampions": champion_summary["topChampions"],
        "avgKda": champion_summary["avgKda"],
        "avgCsPerMin": champion_summary["avgCsPerMin"],
        "lpHistory": lp_history,
        "lpChartPoints": lp_chart["points"],
        "lpChartMin": lp_chart["min"],
        "lpChartMax": lp_chart["max"],
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Wrote data.json with {len(matches)} matches")


if __name__ == "__main__":
    main()
