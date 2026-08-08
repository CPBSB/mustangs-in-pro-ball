#!/usr/bin/env python3
"""Generate stats.json from MLB's public statistics service.

Designed for GitHub Actions. Existing stats are preserved for any player whose
request fails, so a temporary provider outage cannot blank the website.
"""
from __future__ import annotations
import argparse, json, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SEASON = int(os.getenv("BASEBALL_SEASON", datetime.now().year))
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT
OUTPUT = ROOT / "stats.json"
SUMMARY_OUTPUT = ROOT / "nightly_summary.json"
DAILY_OUTPUT = ROOT / "daily_article.json"
SCHEDULE_OUTPUT = ROOT / "today_schedule.json"
TRANSACTIONS_OUTPUT = ROOT / "transactions.json"
PACIFIC = ZoneInfo("America/Los_Angeles")
SPORT_IDS = "1,11,12,13,14,15,16"
LEVEL_BY_SPORT_ID = {
    1: "MLB", 11: "Triple-A", 12: "Double-A", 13: "High-A",
    14: "Single-A", 15: "Rookie", 16: "Rookie"
}
PLAYERS_FILE = ROOT / "players.json"

def load_players():
    payload = json.loads(PLAYERS_FILE.read_text())
    rows = payload.get("players", payload)
    return [
        {
            "id": p.get("mlbId"),
            "name": p["name"],
            "type": p["type"],
            "teamId": p.get("teamId"),
            "status": p.get("status"),
            "recentLevel": p.get("recentLevel"),
            "team": p.get("team"),
        }
        for p in rows if p.get("mlbId")
    ]

PLAYERS = load_players()

def get_json(url, params):
    req=Request(url+"?"+urlencode(params),headers={"User-Agent":"MustangsProBall/1.0"})
    with urlopen(req,timeout=30) as r:
        return json.load(r)

def outs(ip):
    s=str(ip or "0.0"); a,b=(s.split(".")+["0"])[:2]
    return int(a)*3+int(b)

def ip_from_outs(n): return f"{n//3}.{n%3}"

def num(stat,key):
    v=stat.get(key,0)
    try: return int(v)
    except:
        try: return float(v)
        except: return 0

def relevant_splits(payload):
    result=[]
    for block in payload.get("stats",[]):
        for split in block.get("splits",[]):
            season=str(split.get("season", SEASON))
            if season != str(SEASON): continue
            result.append(split)
    return result

def assignment_from_split(split):
    team = (split.get("team") or {}).get("name")
    sport = split.get("sport") or (split.get("team") or {}).get("sport") or {}
    sport_id = sport.get("id")
    level = LEVEL_BY_SPORT_ID.get(sport_id) or sport.get("name")
    if not level:
        league = split.get("league") or {}
        level = league.get("name")
    return team, level



_TEAM_CATALOG = None

def team_catalog():
    """Cache affiliated MLB/MiLB teams so transaction text can resolve a destination."""
    global _TEAM_CATALOG
    if _TEAM_CATALOG is not None:
        return _TEAM_CATALOG
    rows = []
    try:
        data = get_json("https://statsapi.mlb.com/api/v1/teams", {
            "sportIds": SPORT_IDS,
            "season": SEASON,
            "hydrate": "sport,league"
        })
        for team in data.get("teams", []):
            sport = team.get("sport") or {}
            level = LEVEL_BY_SPORT_ID.get(sport.get("id")) or sport.get("name")
            rows.append({
                "id": team.get("id"),
                "name": team.get("name"),
                "level": level,
            })
    except Exception:
        rows = []
    _TEAM_CATALOG = rows
    return rows


def transaction_team_from_description(description):
    """Resolve a team named in an official transaction description."""
    text = str(description or "").lower()
    if not text:
        return None, None, None

    matches = []
    for team in team_catalog():
        name = str(team.get("name") or "")
        if name and name.lower() in text:
            matches.append(team)

    if not matches:
        return None, None, None

    # Official descriptions commonly read "assigned ... to DESTINATION from SOURCE".
    # When two teams are present, prefer the team named after " to ".
    to_pos = text.find(" to ")
    if to_pos >= 0:
        after = text[to_pos + 4:]
        for team in matches:
            if str(team.get("name") or "").lower() in after:
                return team.get("name"), team.get("level"), team.get("id")

    # "Activated by Portland Sea Dogs", "recalled by X", etc. normally contain
    # only the destination/current club.
    team = matches[-1]
    return team.get("name"), team.get("level"), team.get("id")


def fetch_transaction_assignment(player, days=30):
    """Return the newest official transaction-based team assignment, if identifiable."""
    end = datetime.now(PACIFIC).date()
    start = end - timedelta(days=days)
    try:
        data = get_json(
            f"https://statsapi.mlb.com/api/v1/people/{player['id']}",
            {"hydrate": f"transactions(startDate={start.isoformat()},endDate={end.isoformat()})"}
        )
        person = (data.get("people") or [{}])[0]
        txs = list(person.get("transactions") or [])
    except Exception:
        return None, None, None, None

    txs.sort(key=lambda t: str(t.get("date") or ""), reverse=True)

    for tx in txs:
        tx_date = str(tx.get("date") or "")[:10] or None
        desc = tx.get("description") or tx.get("typeDesc") or ""

        # Prefer structured destination-team metadata when the API provides it.
        to_team = tx.get("toTeam") or tx.get("team")
        if isinstance(to_team, dict) and (to_team.get("id") or to_team.get("name")):
            tid = to_team.get("id")
            tname = to_team.get("name")
            level = None
            for row in team_catalog():
                if (tid and row.get("id") == tid) or (tname and row.get("name") == tname):
                    tname = row.get("name") or tname
                    level = row.get("level")
                    tid = row.get("id") or tid
                    break
            if tname:
                return tname, level, tid, tx_date

        tname, level, tid = transaction_team_from_description(desc)
        if tname:
            return tname, level, tid, tx_date

    return None, None, None, None


def fetch_recent_assignment(player, fallback_splits=None):
    """Resolve current assignment using newest transaction before stale game logs."""
    group = "hitting" if player["type"] == "hitter" else "pitching"

    tx_team, tx_level, tx_team_id, tx_date = fetch_transaction_assignment(player)

    log_team = log_level = log_team_id = log_date = None
    url = f"https://statsapi.mlb.com/api/v1/people/{player['id']}/stats"
    try:
        data = get_json(url, {
            "stats": "gameLog", "group": group, "season": SEASON,
            "sportIds": SPORT_IDS, "hydrate": "team(sport),league,game"
        })
        logs = relevant_splits(data)
        if logs:
            latest = max(logs, key=lambda item: str(item.get("date", "")))
            log_team, log_level = assignment_from_split(latest)
            log_team_id = (latest.get("team") or {}).get("id")
            log_date = str(latest.get("date") or "")[:10] or None
    except Exception:
        pass

    # If a transaction is at least as new as the latest appearance, it represents
    # the current roster assignment even if the player has not debuted there yet.
    if tx_team and (not log_date or not tx_date or tx_date >= log_date):
        return tx_team, tx_level or log_level, tx_team_id, "transaction", tx_date

    if log_team or log_level:
        return log_team, log_level, log_team_id, "gameLog", log_date

    # Season/year-by-year splits are the next-best fallback.
    for split in reversed(fallback_splits or []):
        team, level = assignment_from_split(split)
        team_id = (split.get("team") or {}).get("id")
        if team or level:
            return team, level, team_id, "seasonSplit", None

    # A recognizable transaction is still better than an old saved assignment.
    if tx_team:
        return tx_team, tx_level, tx_team_id, "transaction", tx_date

    return None, None, None, None, None


def fetch_last_seven(player):
    """Aggregate a player's seven most recent professional game-log appearances."""
    group = "hitting" if player["type"] == "hitter" else "pitching"
    url = f"https://statsapi.mlb.com/api/v1/people/{player['id']}/stats"
    data = get_json(url, {
        "stats": "gameLog",
        "group": group,
        "season": SEASON,
        "sportIds": SPORT_IDS,
        "hydrate": "team(sport),league,game"
    })
    logs = relevant_splits(data)
    logs = [row for row in logs if row.get("date") and row.get("stat")]
    logs.sort(
        key=lambda row: (
            str(row.get("date") or ""),
            int(((row.get("game") or {}).get("gamePk") or 0))
        ),
        reverse=True
    )
    recent = logs[:7]
    if not recent:
        return {"games": 0, "stats": {}, "startDate": None, "endDate": None}

    dates = [str(row.get("date"))[:10] for row in recent if row.get("date")]
    start_date = min(dates) if dates else None
    end_date = max(dates) if dates else None

    if player["type"] == "hitter":
        keys = [
            "atBats","runs","hits","doubles","triples","homeRuns","rbi",
            "baseOnBalls","strikeOuts","stolenBases","caughtStealing",
            "hitByPitch","sacFlies"
        ]
        totals = {key: sum(num(row.get("stat", {}), key) for row in recent) for key in keys}
        ab = totals["atBats"]
        h = totals["hits"]
        bb = totals["baseOnBalls"]
        hbp = totals["hitByPitch"]
        sf = totals["sacFlies"]
        tb = h + totals["doubles"] + 2 * totals["triples"] + 3 * totals["homeRuns"]
        avg = f"{h/ab:.3f}"[1:] if ab else "—"
        slg = f"{tb/ab:.3f}"[1:] if ab else "—"
        obp_den = ab + bb + hbp + sf
        obp = f"{(h+bb+hbp)/obp_den:.3f}"[1:] if obp_den else "—"
        try:
            ops = f"{float(obp)+float(slg):.3f}"[1:] if obp != "—" and slg != "—" else "—"
        except Exception:
            ops = "—"
        stats = {
            "G": len(recent), "AB": ab, "R": totals["runs"], "H": h,
            "2B": totals["doubles"], "3B": totals["triples"], "HR": totals["homeRuns"],
            "RBI": totals["rbi"], "BB": bb, "SO": totals["strikeOuts"],
            "SB": totals["stolenBases"], "CS": totals["caughtStealing"],
            "AVG": avg, "OBP": obp, "SLG": slg, "OPS": ops
        }
    else:
        total_outs = sum(outs(row.get("stat", {}).get("inningsPitched")) for row in recent)
        sums = {
            key: sum(num(row.get("stat", {}), key) for row in recent)
            for key in ["runs","earnedRuns","baseOnBalls","strikeOuts","hits"]
        }
        ip = ip_from_outs(total_outs)
        era = f"{sums['earnedRuns']*27/total_outs:.2f}" if total_outs else "—"
        whip = f"{(sums['baseOnBalls']+sums['hits'])*3/total_outs:.2f}" if total_outs else "—"
        stats = {
            "G": len(recent), "IP": ip, "H": sums["hits"], "R": sums["runs"],
            "ER": sums["earnedRuns"], "BB": sums["baseOnBalls"],
            "SO": sums["strikeOuts"], "ERA": era, "WHIP": whip
        }

    return {
        "games": len(recent),
        "startDate": start_date,
        "endDate": end_date,
        "stats": stats
    }


def fetch_player(p):
    group="hitting" if p["type"]=="hitter" else "pitching"
    url=f"https://statsapi.mlb.com/api/v1/people/{p['id']}/stats"
    data=get_json(url,{"stats":"season","group":group,"season":SEASON,"sportIds":SPORT_IDS,"hydrate":"team,league"})
    splits=relevant_splits(data)
    if not splits:
        data=get_json(url,{"stats":"yearByYear","group":group,"season":SEASON,"sportIds":SPORT_IDS,"hydrate":"team,league"})
        splits=relevant_splits(data)
    if not splits: return None
    # Prefer an explicit aggregate split when supplied. Otherwise combine team/level rows.
    aggregate=[s for s in splits if not s.get("team")]
    used=aggregate[:1] if aggregate else splits
    team=used[-1].get("team",{}).get("name") or splits[-1].get("team",{}).get("name")
    recent_team, recent_level, recent_team_id, assignment_source, assignment_date = fetch_recent_assignment(p, splits)
    if p["type"]=="hitter":
        totals={k:sum(num(s.get("stat",{}),k) for s in used) for k in ["atBats","runs","hits","doubles","triples","homeRuns","rbi","baseOnBalls","strikeOuts","stolenBases","caughtStealing"]}
        ab=totals["atBats"]; h=totals["hits"]
        # Rate fields should come from an aggregate row where possible. When levels are combined,
        # AVG and SLG can be calculated exactly; OBP uses the provider value unless full denominator fields exist.
        st=used[0].get("stat",{}) if len(used)==1 else {}
        avg=f"{h/ab:.3f}"[1:] if ab else "—"
        tb=h+totals["doubles"]+2*totals["triples"]+3*totals["homeRuns"]
        slg=f"{tb/ab:.3f}"[1:] if ab else "—"
        obp=str(st.get("obp","—")); ops=str(st.get("ops","—"))
        if obp not in ("—","") and slg!="—":
            try: ops=f"{float(obp)+float(slg):.3f}"[1:]
            except: pass
        stats={"AB":totals["atBats"],"R":totals["runs"],"H":h,"2B":totals["doubles"],"3B":totals["triples"],"HR":totals["homeRuns"],"RBI":totals["rbi"],"BB":totals["baseOnBalls"],"SO":totals["strikeOuts"],"SB":totals["stolenBases"],"CS":totals["caughtStealing"],"AVG":avg,"OBP":obp,"SLG":slg,"OPS":ops}
        # Reject abbreviated provider summaries: a professional hitter line must include
        # every counting field used by the card. The previous stats.json entry is preserved
        # by main() when this function raises, preventing complete lines from being replaced
        # by headline-only MLB/MiLB summaries.
        required_provider_keys = ["atBats","runs","hits","doubles","triples","homeRuns","rbi","baseOnBalls","strikeOuts","stolenBases","caughtStealing"]
        if len(used) == 1 and any(key not in used[0].get("stat", {}) for key in required_provider_keys):
            raise ValueError(f"Incomplete hitting response for {p['name']}")
    else:
        total_outs=sum(outs(s.get("stat",{}).get("inningsPitched")) for s in used)
        sums={k:sum(num(s.get("stat",{}),k) for s in used) for k in ["runs","earnedRuns","baseOnBalls","strikeOuts","hits"]}
        ip=ip_from_outs(total_outs)
        era=f"{sums['earnedRuns']*27/total_outs:.2f}" if total_outs else "—"
        whip=f"{(sums['baseOnBalls']+sums['hits'])*3/total_outs:.2f}" if total_outs else "—"
        stats={"IP":ip,"R":sums["runs"],"ER":sums["earnedRuns"],"BB":sums["baseOnBalls"],"SO":sums["strikeOuts"],"H":sums["hits"],"ERA":era,"WHIP":whip}
    last7 = fetch_last_seven(p)
    return {"name":p["name"],"type":p["type"],"team":team,"recentTeam":recent_team or team,"recentLevel":recent_level,"recentTeamId":recent_team_id,"assignmentSource":assignment_source,"assignmentDate":assignment_date,"stats":stats,"last7":last7}


def fmt_ip(value):
    value = str(value or "0.0")
    return value if value not in ("0", "0.0") else "0.0"


def hitter_sentence(name, stat):
    ab = int(num(stat, "atBats")); hits = int(num(stat, "hits"))
    parts = [f"{hits}-for-{ab}"]
    extras = []
    for key, label in (("homeRuns", "HR"), ("triples", "3B"), ("doubles", "2B")):
        value = int(num(stat, key))
        if value: extras.append(f"{value} {label}" if value > 1 else label)
    rbi = int(num(stat, "rbi")); runs = int(num(stat, "runs")); walks = int(num(stat, "baseOnBalls")); steals = int(num(stat, "stolenBases"))
    if extras: parts.append(", ".join(extras))
    if rbi: parts.append(f"{rbi} RBI")
    if runs: parts.append(f"{runs} run" + ("s" if runs != 1 else ""))
    if walks: parts.append(f"{walks} walk" + ("s" if walks != 1 else ""))
    if steals: parts.append(f"{steals} SB")
    return f"{name} went " + ", ".join(parts) + "."


def pitcher_sentence(name, stat):
    ip = fmt_ip(stat.get("inningsPitched"))
    hits = int(num(stat, "hits")); runs = int(num(stat, "runs")); er = int(num(stat, "earnedRuns")); walks = int(num(stat, "baseOnBalls")); strikeouts = int(num(stat, "strikeOuts"))
    decision = ""
    if stat.get("wins"): decision = " and earned the win"
    elif stat.get("losses"): decision = " and took the loss"
    elif stat.get("saves"): decision = " and recorded the save"
    return f"{name} worked {ip} IP, allowing {hits} H, {runs} R ({er} ER) and {walks} BB with {strikeouts} K{decision}."


def fetch_game_log(player, target_date):
    group = "hitting" if player["type"] == "hitter" else "pitching"
    url = f"https://statsapi.mlb.com/api/v1/people/{player['id']}/stats"
    data = get_json(url, {
        "stats": "gameLog", "group": group, "season": SEASON,
        "sportIds": SPORT_IDS, "hydrate": "team,game"
    })
    matches = []
    for block in data.get("stats", []):
        for split in block.get("splits", []):
            if str(split.get("date", ""))[:10] == target_date:
                matches.append(split)
    return matches


def fetch_date_range(player, target_date):
    """Direct one-day stats lookup across MLB and every tracked MiLB level.

    The regular gameLog feed occasionally lags for minor-league players. A
    byDateRange request is a second independent path and is especially useful
    for same-day/next-morning recaps.
    """
    group = "hitting" if player["type"] == "hitter" else "pitching"
    url = f"https://statsapi.mlb.com/api/v1/people/{player['id']}/stats"
    data = get_json(url, {
        "stats": "byDateRange", "group": group,
        "startDate": target_date, "endDate": target_date,
        "sportIds": SPORT_IDS, "hydrate": "team(sport),league,game"
    })
    matches = []
    for split in relevant_splits(data):
        split_date = str(split.get("date") or split.get("game", {}).get("officialDate") or "")[:10]
        if split_date and split_date != target_date:
            continue
        stat = split.get("stat") or {}
        if player["type"] == "hitter":
            appeared = any(num(stat, k) for k in (
                "plateAppearances", "atBats", "runs", "hits", "baseOnBalls",
                "hitByPitch", "stolenBases", "caughtStealing", "rbi"
            ))
        else:
            appeared = outs(stat.get("inningsPitched")) > 0
        if appeared:
            matches.append(split)
    return matches


def fetch_official_boxscore_appearances(player, target_date):
    """Authoritative fallback across MLB and every tracked MiLB level.

    Player gameLog/byDateRange endpoints can lag, especially in the minors.
    When that happens, use the player's latest saved assignment to find the
    club's official game and inspect the actual box score for the player ID.
    """
    # The full stats refresh is written before the morning recap, so it normally
    # contains a fresher team ID/level than the static players.json catalog.
    fresh = {}
    try:
        fresh = (json.loads(OUTPUT.read_text()).get("players") or {}).get(str(player["id"])) or {}
    except Exception:
        fresh = {}

    team_id = fresh.get("recentTeamId") or player.get("teamId")
    level = fresh.get("recentLevel") or player.get("recentLevel")

    level_to_sport = {
        "MLB": 1,
        "Triple-A": 11,
        "Double-A": 12,
        "High-A": 13,
        "Single-A": 14,
        "Rookie": 15,
        "Rookie Ball": 15,
    }
    sport_id = level_to_sport.get(level)

    # If level metadata is missing, try every affiliated level, but only as a
    # last resort. This remains bounded to the tracked professional structure.
    sport_ids = [sport_id] if sport_id else [1, 11, 12, 13, 14, 15, 16]
    matches = []
    seen_games = set()

    for sid in sport_ids:
        params = {"sportId": sid, "date": target_date}
        if team_id:
            params["teamId"] = team_id

        try:
            schedule = get_json("https://statsapi.mlb.com/api/v1/schedule", params)
        except Exception:
            continue

        for date_block in schedule.get("dates", []):
            for game in date_block.get("games", []):
                game_pk = game.get("gamePk")
                if not game_pk or game_pk in seen_games:
                    continue
                seen_games.add(game_pk)

                # If we had no reliable team id, don't scan unrelated games at
                # every level blindly unless the box score actually contains
                # this player.
                try:
                    box = get_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore", {})
                except Exception:
                    continue

                for side, other_side in (("away", "home"), ("home", "away")):
                    team_block = (box.get("teams") or {}).get(side) or {}
                    players = team_block.get("players") or {}
                    entry = players.get(f"ID{player['id']}")
                    if not entry:
                        entry = next(
                            (v for v in players.values()
                             if (v.get("person") or {}).get("id") == player["id"]),
                            None
                        )
                    if not entry:
                        continue

                    stats = entry.get("stats") or {}
                    stat = stats.get("batting" if player["type"] == "hitter" else "pitching") or {}
                    fielding = stats.get("fielding") or {}

                    if player["type"] == "hitter":
                        appeared = (
                            num(stat, "plateAppearances") > 0
                            or num(stat, "atBats") > 0
                            or any(num(stat, k) for k in (
                                "runs", "hits", "baseOnBalls", "hitByPitch",
                                "stolenBases", "caughtStealing", "rbi"
                            ))
                            or bool(fielding)
                        )
                    else:
                        appeared = outs(stat.get("inningsPitched")) > 0

                    if not appeared:
                        continue

                    opponent_block = (box.get("teams") or {}).get(other_side) or {}
                    own_team = team_block.get("team") or {}
                    opp_team = opponent_block.get("team") or {}

                    matches.append({
                        "date": target_date,
                        "stat": stat,
                        "team": {
                            "id": own_team.get("id"),
                            "name": own_team.get("name")
                        },
                        "opponent": {
                            "id": opp_team.get("id"),
                            "name": opp_team.get("name")
                        },
                        "sport": {"id": sid, "name": LEVEL_BY_SPORT_ID.get(sid)},
                        "game": {"gamePk": game_pk, "officialDate": target_date},
                        "gamePk": game_pk,
                        "boxscoreFallback": True,
                        "appearanceOnly": (
                            player["type"] == "hitter"
                            and num(stat, "plateAppearances") == 0
                            and num(stat, "atBats") == 0
                            and not any(num(stat, k) for k in (
                                "runs", "hits", "baseOnBalls", "hitByPitch",
                                "stolenBases", "caughtStealing", "rbi"
                            ))
                        ),
                    })
                    break

    return matches



def _score_pair(result):
    try:
        return int(result.get("awayScore", 0)), int(result.get("homeScore", 0))
    except Exception:
        return 0, 0


def fetch_game_context(game_pk, player):
    """Return verified box-score and play-by-play context for one appearance.

    This is intentionally descriptive rather than speculative.  It recognizes
    score-changing plays (go-ahead, tying and walk-off), final score/result,
    lineup slot/position and pitching entry inning when those facts are present
    in MLB/MiLB's official game feeds.
    """
    context = {"keyPlays": []}
    if not game_pk:
        return context
    try:
        box = get_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore", {})
        teams = box.get("teams") or {}
        player_side = None
        player_row = None
        for side in ("away", "home"):
            rows = ((teams.get(side) or {}).get("players") or {})
            row = rows.get(f"ID{player['id']}")
            if not row:
                row = next((v for v in rows.values() if (v.get("person") or {}).get("id") == player["id"]), None)
            if row:
                player_side, player_row = side, row
                break
        if player_side:
            other = "home" if player_side == "away" else "away"
            own_team = ((teams.get(player_side) or {}).get("team") or {}).get("name")
            opp_team = ((teams.get(other) or {}).get("team") or {}).get("name")
            own_score = (teams.get(player_side) or {}).get("teamStats", {}).get("batting", {}).get("runs")
            opp_score = (teams.get(other) or {}).get("teamStats", {}).get("batting", {}).get("runs")
            # Some boxscore responses expose totals directly under teamStats; if
            # they do not, game linescore below will supply the final score.
            context.update({"side": player_side, "team": own_team, "opponent": opp_team})
            if player_row:
                order = player_row.get("battingOrder")
                if order:
                    try: context["battingOrder"] = max(1, int(order) // 100)
                    except Exception: pass
                pos = (player_row.get("position") or {}).get("abbreviation")
                if pos: context["position"] = pos
    except Exception:
        box = None

    try:
        pbp = get_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/playByPlay", {})
    except Exception:
        return context

    plays = pbp.get("allPlays") or []
    prev_away = prev_home = 0
    first_pitching_play = None
    for play in plays:
        about = play.get("about") or {}
        result = play.get("result") or {}
        matchup = play.get("matchup") or {}
        inning = about.get("inning")
        half = about.get("halfInning") or ""
        away_after, home_after = _score_pair(result)
        batter_id = ((matchup.get("batter") or {}).get("id"))
        pitcher_id = ((matchup.get("pitcher") or {}).get("id"))

        if player.get("type") == "hitter" and batter_id == player["id"]:
            batting_side = "away" if str(half).lower() == "top" else "home"
            before_us = prev_away if batting_side == "away" else prev_home
            before_them = prev_home if batting_side == "away" else prev_away
            after_us = away_after if batting_side == "away" else home_after
            after_them = home_after if batting_side == "away" else away_after
            event = result.get("event") or result.get("eventType") or "Plate appearance"
            desc = result.get("description") or ""
            rbi = int(num(result, "rbi"))
            score_changed = after_us != before_us
            tags = []
            if score_changed:
                if before_us <= before_them and after_us > after_them:
                    tags.append("go-ahead")
                elif before_us < before_them and after_us == after_them:
                    tags.append("game-tying")
                if str(half).lower() == "bottom" and int(inning or 0) >= 9 and after_us > after_them:
                    tags.append("walk-off")
            etype = str(result.get("eventType") or "").lower()
            important = score_changed or rbi or etype in {"home_run", "triple", "double"} or tags
            if important:
                context["keyPlays"].append({
                    "inning": inning, "half": half, "event": event,
                    "description": desc, "rbi": rbi, "tags": tags,
                    "scoreBefore": {"team": before_us, "opponent": before_them},
                    "scoreAfter": {"team": after_us, "opponent": after_them},
                    "pitcher": (matchup.get("pitcher") or {}).get("fullName"),
                })
        elif player.get("type") == "pitcher" and pitcher_id == player["id"] and first_pitching_play is None:
            # Score at the moment the pitcher's first batter comes to the plate.
            pitching_side = "home" if str(half).lower() == "top" else "away"
            first_pitching_play = {
                "inning": inning, "half": half,
                "teamScore": prev_home if pitching_side == "home" else prev_away,
                "opponentScore": prev_away if pitching_side == "home" else prev_home,
            }
        prev_away, prev_home = away_after, home_after

    if plays:
        final = plays[-1].get("result") or {}
        away_final, home_final = _score_pair(final)
        context["awayFinal"] = away_final
        context["homeFinal"] = home_final
        side = context.get("side")
        if side:
            team_final = away_final if side == "away" else home_final
            opp_final = home_final if side == "away" else away_final
            context["teamFinal"] = team_final
            context["opponentFinal"] = opp_final
            context["result"] = "win" if team_final > opp_final else "loss" if team_final < opp_final else "tie"
    if first_pitching_play:
        context["pitchingEntry"] = first_pitching_play
    return context


def key_play_sentence(appearance):
    ctx = appearance.get("gameContext") or {}
    plays = ctx.get("keyPlays") or []
    if not plays:
        return None
    # Prioritize walk-off, then go-ahead, then tying, then other run-producing/XBH plays.
    def rank(play):
        tags = play.get("tags") or []
        return (3 if "walk-off" in tags else 2 if "go-ahead" in tags else 1 if "game-tying" in tags else 0,
                int(play.get("inning") or 0), int(play.get("rbi") or 0))
    play = max(plays, key=rank)
    name = appearance.get("name") or "The Mustang"
    inning = int(play.get("inning") or 0)
    ords = {1:"first",2:"second",3:"third",4:"fourth",5:"fifth",6:"sixth",7:"seventh",8:"eighth",9:"ninth",10:"10th",11:"11th",12:"12th"}
    inning_word = ords.get(inning, f"{inning}th") if inning else "late"
    tags = play.get("tags") or []
    event = str(play.get("event") or "hit").lower()
    rbi = int(play.get("rbi") or 0)
    before = play.get("scoreBefore") or {}; after = play.get("scoreAfter") or {}
    if "walk-off" in tags:
        lead = f"{name} delivered the walk-off {event} in the {inning_word} inning"
    elif "go-ahead" in tags:
        lead = f"{name} delivered a {('two-run ' if rbi == 2 else str(rbi)+'-run ' if rbi > 2 else '')}go-ahead {event} in the {inning_word} inning"
    elif "game-tying" in tags:
        lead = f"{name} delivered a {('two-run ' if rbi == 2 else str(rbi)+'-run ' if rbi > 2 else '')}game-tying {event} in the {inning_word} inning"
    elif rbi:
        lead = f"{name}'s {event} in the {inning_word} inning drove in {rbi} run{'s' if rbi != 1 else ''}"
    else:
        lead = f"{name} produced a {event} in the {inning_word} inning"
    if before and after and (before.get("team") != after.get("team") or before.get("opponent") != after.get("opponent")):
        lead += f", moving his team from {before.get('team')}-{before.get('opponent')} to {after.get('team')}-{after.get('opponent')}"
    pitcher = play.get("pitcher")
    if pitcher:
        lead += f" against {pitcher}"
    return lead + "."

def best_video_url(item):
    playbacks = item.get("playbacks") or []
    preferred = ("1280x720", "HTTP_CLOUD_WIRED_60", "mp4Avc")
    for marker in preferred:
        for playback in playbacks:
            if marker.lower() in str(playback.get("name", "")).lower() and playback.get("url"):
                return playback["url"]
    for playback in playbacks:
        if playback.get("url"):
            return playback["url"]
    return None


def find_highlights(game_pk, player):
    try:
        content = get_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/content", {})
    except Exception:
        return []
    found = []
    seen = set()
    def walk(node):
        if isinstance(node, dict):
            if node.get("playbacks"):
                haystack = json.dumps(node, ensure_ascii=False).lower()
                tokens = [str(player["id"]), player["name"].lower()]
                if any(token in haystack for token in tokens):
                    url = best_video_url(node)
                    if url and url not in seen:
                        seen.add(url)
                        image = None
                        cuts = ((node.get("image") or {}).get("cuts") or [])
                        if cuts: image = cuts[-1].get("src") or cuts[0].get("src")
                        found.append({
                            "title": node.get("title") or node.get("headline") or f"{player['name']} highlight",
                            "description": node.get("description") or node.get("blurb") or "",
                            "url": url,
                            "image": image,
                            "source": "MLB/MiLB official game content"
                        })
            for value in node.values(): walk(value)
        elif isinstance(node, list):
            for value in node: walk(value)
    walk(content)
    return found[:3]


def build_nightly_summary():
    now = datetime.now(PACIFIC)
    target = (now.date() - timedelta(days=1)).isoformat()
    appearances = []
    warnings = []
    game_highlight_cache = {}
    for player in PLAYERS:
        try:
            splits = fetch_game_log(player, target)
            if not splits:
                # A direct one-day lookup catches many MiLB appearances that have
                # not yet propagated into the season gameLog feed.
                splits = fetch_date_range(player, target)
            if not splits:
                # Player-level feeds can lag at both MLB and MiLB levels. The
                # official scheduled game + box score is the authoritative final
                # appearance check, including plate appearances in minor-league games.
                splits = fetch_official_boxscore_appearances(player, target)
            for split in splits:
                stat = split.get("stat", {})
                game = split.get("game", {}) or {}
                team = (split.get("team") or {}).get("name")
                level = assignment_from_split(split)[1] or ("MLB" if player.get("status") == "mlb" else player.get("recentLevel"))
                opponent = (split.get("opponent") or {}).get("name")
                game_pk = game.get("gamePk") or split.get("gamePk")
                if split.get("appearanceOnly"):
                    recap = f"{player['name']} appeared defensively for {team or 'his club'} against {opponent or 'the opponent'}."
                else:
                    recap = hitter_sentence(player["name"], stat) if player["type"] == "hitter" else pitcher_sentence(player["name"], stat)
                highlights = []
                if game_pk:
                    cache_key = (game_pk, player["id"])
                    if cache_key not in game_highlight_cache:
                        game_highlight_cache[cache_key] = find_highlights(game_pk, player)
                    highlights = game_highlight_cache[cache_key]
                game_context = fetch_game_context(game_pk, player) if game_pk else {}
                if not team and game_context.get("team"):
                    team = game_context.get("team")
                if not opponent and game_context.get("opponent"):
                    opponent = game_context.get("opponent")
                appearances.append({
                    "playerId": player["id"], "name": player["name"], "type": player["type"],
                    "team": team, "level": level, "opponent": opponent, "gamePk": game_pk,
                    "summary": recap, "stats": stat, "highlights": highlights,
                    "gameContext": game_context
                })
        except Exception as exc:
            warnings.append(f"{player['name']}: {exc}")
        time.sleep(.1)
    if appearances:
        names = [a["name"] for a in appearances]
        if len(names) == 1: intro = f"One former Mustang appeared in a professional game on {target}."
        else: intro = f"{len(names)} former Mustangs appeared in professional games on {target}."
    else:
        intro = f"No tracked Mustangs were found in professional game logs for {target}."
    payload = {
        "date": target,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "title": "Last Night's Mustangs",
        "intro": intro,
        "appearances": appearances,
        "warnings": warnings,
        "source": "MLB/MiLB gameLog/byDateRange plus authoritative official team box-score audit, play-by-play and game content"
    }
    # A total provider/network outage must not replace the last good recap with a false
    # "nobody played" report. Partial success is still written normally.
    if not appearances and len(warnings) == len(PLAYERS) and SUMMARY_OUTPUT.exists():
        print("All game-log requests failed; preserving previous nightly_summary.json", file=sys.stderr)
        try:
            return json.loads(SUMMARY_OUTPUT.read_text())
        except Exception:
            return payload
    SUMMARY_OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {SUMMARY_OUTPUT} with {len(appearances)} appearances")
    for warning in warnings: print("WARNING", warning, file=sys.stderr)
    return payload



def build_daily_article(summary, transactions=None):
    """Write Mustangs Daily like a concise local baseball beat report.

    Principles:
      * Lead with what mattered, not tracker/database language.
      * Match the headline to the actual performance; don't oversell quiet nights.
      * Combine box score, game result, lineup role and key play into paragraphs.
      * Fold same-player transactions into one clean roster-note paragraph.
      * Avoid repeated names, canned closers and mechanical source language.
    """
    apps = summary.get("appearances", [])
    date = summary.get("date")
    dt = datetime.fromisoformat(date) if date else None
    label = dt.strftime("%A, %B %-d, %Y") if dt else "the latest games"
    weekday = dt.strftime("%A") if dt else "the night"

    transaction_rows = (transactions or {}).get("transactions", [])
    day_transactions = [
        t for t in transaction_rows
        if not date or str(t.get("date") or "")[:10] == date
    ]

    hitters = [a for a in apps if a.get("type") == "hitter"]
    pitchers = [a for a in apps if a.get("type") == "pitcher"]

    def hscore(a):
        st = a.get("stats", {})
        return (
            num(st, "hits") * 3
            + num(st, "homeRuns") * 6
            + num(st, "rbi") * 2
            + num(st, "runs")
            + num(st, "stolenBases") * 2
            + num(st, "baseOnBalls") * .5
        )

    def pscore(a):
        st = a.get("stats", {})
        return (
            num(st, "strikeOuts") * 2
            + outs(st.get("inningsPitched"))
            - num(st, "earnedRuns") * 4
            - num(st, "baseOnBalls") * 2
            - num(st, "hits")
        )

    def best_play(a):
        plays = (a.get("gameContext") or {}).get("keyPlays") or []
        if not plays:
            return None
        def rank(play):
            tags = play.get("tags") or []
            leverage = (
                5 if "walk-off" in tags else
                4 if "go-ahead" in tags else
                3 if "game-tying" in tags else
                1 if play.get("rbi") else 0
            )
            return leverage, int(play.get("inning") or 0), int(play.get("rbi") or 0)
        return max(plays, key=rank)

    def importance(a):
        bonus = 0
        play = best_play(a)
        if play:
            tags = play.get("tags") or []
            if "walk-off" in tags: bonus += 20
            if "go-ahead" in tags: bonus += 14
            if "game-tying" in tags: bonus += 9
            bonus += int(play.get("rbi") or 0)
        return (hscore(a) if a.get("type") == "hitter" else pscore(a)) + bonus

    def inning_name(n):
        names = {
            1:"first",2:"second",3:"third",4:"fourth",5:"fifth",
            6:"sixth",7:"seventh",8:"eighth",9:"ninth"
        }
        try:
            n = int(n)
            return names.get(n, f"{n}th")
        except Exception:
            return "late"

    def result_text(a):
        ctx = a.get("gameContext") or {}
        team = a.get("team") or "his club"
        opp = a.get("opponent") or "the opponent"
        tf = ctx.get("teamFinal")
        of = ctx.get("opponentFinal")
        result = ctx.get("result")
        if tf is None or of is None:
            return None
        if result == "win":
            return f"{team} beat {opp}, {tf}-{of}."
        if result == "loss":
            return f"{team} fell to {opp}, {tf}-{of}."
        return f"{team} and {opp} finished {tf}-{of}."

    def hitter_line(a, use_name=True):
        st = a.get("stats", {})
        name = a.get("name") if use_name else "He"
        ab = int(num(st, "atBats"))
        h = int(num(st, "hits"))
        pa = int(num(st, "plateAppearances"))
        r = int(num(st, "runs"))
        rbi = int(num(st, "rbi"))
        bb = int(num(st, "baseOnBalls"))
        hr = int(num(st, "homeRuns"))
        doubles = int(num(st, "doubles"))
        triples = int(num(st, "triples"))
        sb = int(num(st, "stolenBases"))

        pieces = []
        if ab or h:
            pieces.append(f"{h}-for-{ab}")
        elif pa:
            pieces.append(f"{pa} plate appearances")
        if hr:
            pieces.append(f"{hr} home run{'s' if hr != 1 else ''}")
        if doubles:
            pieces.append(f"{doubles} double{'s' if doubles != 1 else ''}")
        if triples:
            pieces.append(f"{triples} triple{'s' if triples != 1 else ''}")
        if rbi:
            pieces.append(f"{rbi} RBI")
        if r:
            pieces.append(f"{r} run{'s' if r != 1 else ''} scored")
        if bb:
            pieces.append(f"{bb} walk{'s' if bb != 1 else ''}")
        if sb:
            pieces.append(f"{sb} stolen base{'s' if sb != 1 else ''}")

        if not pieces:
            return a.get("summary") or ""
        return f"{name} went " + ", ".join(pieces) + "."

    def pitcher_line(a, use_name=True):
        st = a.get("stats", {})
        name = a.get("name") if use_name else "He"
        ip = st.get("inningsPitched") or "0.0"
        h = int(num(st, "hits"))
        er = int(num(st, "earnedRuns"))
        bb = int(num(st, "baseOnBalls"))
        k = int(num(st, "strikeOuts"))
        return (
            f"{name} worked {ip} innings, allowing {h} hit{'s' if h != 1 else ''} "
            f"and {er} earned run{'s' if er != 1 else ''}, with "
            f"{bb} walk{'s' if bb != 1 else ''} and {k} strikeout{'s' if k != 1 else ''}."
        )

    def stat_line(a, use_name=True):
        return hitter_line(a, use_name) if a.get("type") == "hitter" else pitcher_line(a, use_name)

    def role_phrase(a):
        ctx = a.get("gameContext") or {}
        order = ctx.get("battingOrder")
        pos = ctx.get("position")
        bits = []
        if order:
            suffix = "th"
            if order == 1: suffix = "st"
            elif order == 2: suffix = "nd"
            elif order == 3: suffix = "rd"
            bits.append(f"batted {order}{suffix}")
        if pos:
            bits.append(f"started at {pos}")
        if not bits:
            return None
        return " and ".join(bits)

    def key_play_sentence(a):
        play = best_play(a)
        if not play:
            return None
        tags = play.get("tags") or []
        event = str(play.get("event") or "hit").lower()
        inning = inning_name(play.get("inning"))
        rbi = int(play.get("rbi") or 0)
        name = a.get("name")
        run_prefix = "two-run " if rbi == 2 else f"{rbi}-run " if rbi > 2 else ""

        if "walk-off" in tags:
            return f"{name}'s biggest moment came in the {inning}, when he delivered a {run_prefix}walk-off {event}."
        if "go-ahead" in tags:
            return f"The game's turning point came in the {inning}, when {name} delivered a {run_prefix}go-ahead {event}."
        if "game-tying" in tags:
            return f"{name} tied the game in the {inning} with a {run_prefix}{event}."
        if rbi:
            return f"{name} drove in {rbi} run{'s' if rbi != 1 else ''} with a {event} in the {inning}."
        return None

    def transaction_paragraphs(rows):
        """Turn same-day provider transactions into one readable roster-news item."""
        if not rows:
            return []

        grouped = {}
        for t in rows:
            grouped.setdefault(t.get("player") or "A former Mustang", []).append(t)

        # Resolve team names to levels using the same official affiliated-team
        # catalog used by the assignment updater.
        level_by_team = {
            str(row.get("name") or "").lower(): row.get("level")
            for row in team_catalog()
            if row.get("name")
        }

        paragraphs = []
        for name, items in grouped.items():
            descriptions = [
                str(t.get("description") or "").strip().rstrip(".")
                for t in items if t.get("description")
            ]
            if not descriptions:
                continue

            assignment = next(
                (d for d in descriptions if " assigned to " in d.lower() and " from " in d.lower()),
                None
            )

            if assignment:
                # Extract destination/source from the official sentence:
                # "RHP Steven Brooks assigned to Portland Sea Dogs from Greenville Drive"
                m = re.search(r"\bassigned to (.+?) from (.+)$", assignment, flags=re.I)
                if m:
                    destination = m.group(1).strip()
                    source = m.group(2).strip()
                    dest_level = level_by_team.get(destination.lower())
                    source_level = level_by_team.get(source.lower())

                    # A move to a higher affiliate is best described as a call-up.
                    rank = {
                        "Rookie": 1, "Rookie Ball": 1, "Single-A": 2,
                        "High-A": 3, "Double-A": 4, "Triple-A": 5, "MLB": 6
                    }
                    promoted = (
                        dest_level and source_level
                        and rank.get(dest_level, 0) > rank.get(source_level, 0)
                    )

                    tx_date = str(items[0].get("date") or "")[:10]
                    day_word = ""
                    if tx_date:
                        try:
                            day_word = datetime.fromisoformat(tx_date).strftime("%A")
                        except Exception:
                            pass

                    if promoted:
                        lead = f"{name} was called up"
                        if day_word:
                            lead += f" on {day_word}"
                        lead += ","
                        paragraphs.append(
                            f"{lead} moving from the {source_level} {source} to the "
                            f"{dest_level} {destination}."
                        )
                    else:
                        paragraphs.append(
                            f"{name} changed affiliates, moving from {source} to {destination}"
                            + (f" at {dest_level}." if dest_level else ".")
                        )
                    continue

            # Other transaction types remain concise and non-repetitive.
            lower = " ".join(descriptions).lower()
            if "activated" in lower:
                paragraphs.append(f"{name} was activated in a roster move.")
            elif "injured list" in lower or "disabled list" in lower:
                paragraphs.append(f"{name} was placed on the injured list.")
            elif "recalled" in lower:
                paragraphs.append(f"{name} was recalled in a roster move.")
            elif "optioned" in lower:
                paragraphs.append(f"{name} was optioned in a roster move.")
            elif "released" in lower:
                paragraphs.append(f"{name} was released.")
            else:
                d = descriptions[0]
                paragraphs.append(d + "." if name.lower() in d.lower() else f"{name}: {d}.")

        return paragraphs

    # No games: let actual roster news carry the edition when there is some.
    if not apps:
        txp = transaction_paragraphs(day_transactions)
        if txp:
            return {
                "date": date,
                "dateLabel": label,
                "title": "Roster Moves Highlight a Quiet Day for Former Mustangs",
                "paragraphs": [
                    f"No former Mustang recorded a confirmed game appearance on {label}, but there was movement on the transaction wire."
                ] + txp,
                "awards": []
            }
        return {
            "date": date,
            "dateLabel": label,
            "title": "A Quiet Night for Former Mustangs",
            "paragraphs": [
                f"No former Mustang recorded a confirmed appearance in a completed professional game on {label}."
            ],
            "awards": []
        }

    star = max(apps, key=importance)
    star_play = best_play(star)
    tags = (star_play or {}).get("tags") or []
    name = star.get("name")
    st = star.get("stats") or {}

    # Headlines should describe the actual story, not manufacture one.
    if "walk-off" in tags:
        headline = f"{name} delivers a walk-off in the night's biggest Mustang moment"
    elif "go-ahead" in tags:
        headline = f"{name} comes through late with a go-ahead hit"
    elif "game-tying" in tags:
        headline = f"{name} delivers in a key spot"
    elif star.get("type") == "hitter" and num(st, "homeRuns"):
        headline = f"{name} homers to highlight {weekday}'s Mustang action"
    elif star.get("type") == "pitcher" and num(st, "strikeOuts") >= 5:
        headline = f"{name} turns in a strong outing on the mound"
    elif len(apps) == 1:
        result = (star.get("gameContext") or {}).get("result")
        if result == "win" and int(num(st, "runs")):
            headline = f"{name} scores as {star.get('team') or 'his club'} picks up a win"
        elif result == "win":
            headline = f"{name} in the lineup as {star.get('team') or 'his club'} wins"
        else:
            headline = f"{name} represents Cal Poly in {weekday}'s pro action"
    else:
        headline = f"{name} highlights a busy {weekday} for former Mustangs"

    paragraphs = []

    # Opening paragraph: game story first.
    if len(apps) == 1:
        team = star.get("team") or "his club"
        opp = star.get("opponent") or "the opponent"
        result = result_text(star)
        role = role_phrase(star)

        opener = f"{name} was the lone former Mustang to appear in a confirmed professional game on {weekday},"
        if role:
            opener += f" {role}"
        opener += f" for {team} against {opp}."
        if result:
            opener += " " + result
        paragraphs.append(opener)
    else:
        clubs = len({a.get("team") for a in apps if a.get("team")})
        levels = len({a.get("level") for a in apps if a.get("level")})
        opener = f"{len(apps)} former Mustangs appeared in professional games on {weekday}"
        if clubs:
            opener += f" for {clubs} club{'s' if clubs != 1 else ''}"
        if levels > 1:
            opener += f" across {levels} levels"
        opener += f", with {name} producing the night's most notable performance."
        paragraphs.append(opener)

    key = key_play_sentence(star)
    if key:
        paragraphs.append(key)

    # Merge stat line and role into prose instead of separate one-line fragments.
    line = stat_line(star)
    if line:
        # On one-player nights the opening paragraph already establishes batting
        # order/position, so don't repeat that information in the stat paragraph.
        paragraphs.append(line)

    if len(apps) > 1:
        res = result_text(star)
        if res:
            paragraphs.append(res)

    # Other appearances get compact, natural paragraphs rather than three lines apiece.
    others = sorted([a for a in apps if a is not star], key=importance, reverse=True)
    for a in others[:7]:
        name2 = a.get("name")
        team2 = a.get("team") or "his club"
        level2 = a.get("level")
        line2 = stat_line(a, use_name=False)
        res2 = result_text(a)
        role2 = role_phrase(a)

        sentence = f"{name2} also appeared for {team2}"
        if level2 and level2 != "MLB":
            sentence += f" at {level2}"
        sentence += "."
        if line2:
            sentence += " " + line2
        if role2:
            sentence += f" He {role2}."
        if res2:
            sentence += " " + res2
        paragraphs.append(sentence)

    # Transactions are part of the report, but not a raw transaction dump.
    txp = transaction_paragraphs(day_transactions)
    if txp:
        paragraphs.append("Away from the box scores, there was also roster movement involving former Mustangs.")
        paragraphs.extend(txp)

    clips = sum(len(a.get("highlights", [])) for a in apps)
    if clips:
        paragraphs.append(
            f"{clips} official player-tagged highlight clip{'s were' if clips != 1 else ' was'} available from the night's games and can be viewed below."
        )

    awards = [{
        "label": "Player of the Night",
        "player": star["name"],
        "playerId": star.get("playerId"),
        "type": star.get("type"),
        "team": star.get("team"),
        "line": star["summary"]
    }]
    if hitters:
        b = max(hitters, key=hscore)
        awards.append({
            "label": "Top Hitter", "player": b["name"],
            "playerId": b.get("playerId"), "type": "hitter",
            "team": b.get("team"), "line": b["summary"]
        })
    if pitchers:
        b = max(pitchers, key=pscore)
        awards.append({
            "label": "Top Pitcher", "player": b["name"],
            "playerId": b.get("playerId"), "type": "pitcher",
            "team": b.get("team"), "line": b["summary"]
        })

    return {
        "date": date,
        "dateLabel": label,
        "title": headline,
        "paragraphs": paragraphs,
        "awards": awards
    }


def build_today_schedule():
    """Build today's tracked MLB + MiLB slate.

    The official MLB Stats API also carries affiliated minor-league schedules.
    We query each tracked professional level, match games against each player's
    most recent official assignment, and include probable starters when the
    league has published them.
    """
    today = datetime.now(PACIFIC).date().isoformat()
    games = []
    sport_levels = {
        1: "MLB",
        11: "Triple-A",
        12: "Double-A",
        13: "High-A",
        14: "Single-A",
        15: "Rookie",
        16: "Rookie",
    }

    # Use the just-refreshed stats feed because it contains the player's latest
    # team/level, which is more reliable than a static preseason assignment.
    fresh_players = {}
    try:
        payload = json.loads(OUTPUT.read_text())
        fresh_players = payload.get("players", {})
    except Exception:
        fresh_players = {}

    schedule_games = []
    for sport_id, level in sport_levels.items():
        try:
            data = get_json("https://statsapi.mlb.com/api/v1/schedule", {
                "sportId": sport_id,
                "date": today,
                "hydrate": "team,probablePitcher,venue"
            })
        except Exception:
            continue

        for d in data.get("dates", []):
            for g in d.get("games", []):
                away_block = (g.get("teams") or {}).get("away") or {}
                home_block = (g.get("teams") or {}).get("home") or {}
                away = away_block.get("team") or {}
                home = home_block.get("team") or {}
                schedule_games.append({
                    "gamePk": g.get("gamePk"),
                    "gameDate": g.get("gameDate"),
                    "status": (g.get("status") or {}).get("detailedState"),
                    "level": level,
                    "sportId": sport_id,
                    "away": away.get("name"),
                    "home": home.get("name"),
                    "awayTeamId": away.get("id"),
                    "homeTeamId": home.get("id"),
                    "awayScore": away_block.get("score"),
                    "homeScore": home_block.get("score"),
                    "awayProbablePitcher": away_block.get("probablePitcher"),
                    "homeProbablePitcher": home_block.get("probablePitcher"),
                    "venue": (g.get("venue") or {}).get("name"),
                })

    box_cache = {}
    catalog = json.loads(PLAYERS_FILE.read_text()).get("players", [])
    for p in catalog:
        if p.get("status") == "fa":
            continue

        fresh = fresh_players.get(str(p.get("mlbId"))) or {}
        recent_team = fresh.get("recentTeam") or p.get("team")
        recent_team_id = fresh.get("recentTeamId")
        recent_level = fresh.get("recentLevel") or p.get("recentLevel")

        # Find today's game using team id first, then exact official team name.
        matches = []
        for game in schedule_games:
            id_match = recent_team_id and recent_team_id in (game.get("awayTeamId"), game.get("homeTeamId"))
            name_match = recent_team and recent_team in (game.get("away"), game.get("home"))
            if id_match or name_match:
                matches.append(game)

        if not matches:
            continue

        # A club can very occasionally appear twice (doubleheader), so preserve
        # every distinct game instead of picking only the first one.
        for game in matches:
            live_line = None
            game_pk = game.get("gamePk")
            status = game.get("status") or ""
            if game_pk and status not in ("Scheduled", "Pre-Game", "Warmup"):
                try:
                    if game_pk not in box_cache:
                        box_cache[game_pk] = get_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore", {})
                    box = box_cache[game_pk]
                    person_key = f"ID{p.get('mlbId')}"
                    for side in ("away", "home"):
                        player_row = (((box.get("teams") or {}).get(side) or {}).get("players") or {}).get(person_key)
                        if not player_row:
                            continue
                        if p.get("type") == "hitter":
                            st = (player_row.get("stats") or {}).get("batting") or {}
                            ab = int(num(st, "atBats")); h = int(num(st, "hits"))
                            hr = int(num(st, "homeRuns")); rbi = int(num(st, "rbi")); runs = int(num(st, "runs"))
                            if ab or h or st.get("plateAppearances"):
                                extras = []
                                if hr: extras.append(f"{hr} HR")
                                if rbi: extras.append(f"{rbi} RBI")
                                if runs: extras.append(f"{runs} R")
                                live_line = f"{h}-for-{ab}" + ((", " + ", ".join(extras)) if extras else "")
                        else:
                            st = (player_row.get("stats") or {}).get("pitching") or {}
                            ip = st.get("inningsPitched")
                            if ip and str(ip) != "0.0":
                                live_line = f"{ip} IP, {int(num(st,'strikeOuts'))} K, {int(num(st,'earnedRuns'))} ER"
                        break
                except Exception:
                    pass

            # Determine the tracked player's club/opponent from the matched game.
            if recent_team_id == game.get("awayTeamId") or recent_team == game.get("away"):
                team = game.get("away")
                opponent = game.get("home")
            else:
                team = game.get("home")
                opponent = game.get("away")

            games.append({
                "player": p["name"],
                "playerId": p.get("mlbId"),
                "team": team,
                "opponent": opponent,
                "level": game.get("level") or recent_level,
                "sportId": game.get("sportId"),
                "gamePk": game_pk,
                "gameDate": game.get("gameDate"),
                "status": game.get("status"),
                "timeLabel": game.get("gameDate"),
                "away": game.get("away"),
                "home": game.get("home"),
                "awayTeamId": game.get("awayTeamId"),
                "homeTeamId": game.get("homeTeamId"),
                "awayProbablePitcher": game.get("awayProbablePitcher"),
                "homeProbablePitcher": game.get("homeProbablePitcher"),
                "venue": game.get("venue"),
                "awayScore": game.get("awayScore"),
                "homeScore": game.get("homeScore"),
                "liveLine": live_line,
            })

    return {
        "date": today,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "Official MLB/MiLB schedules and probable-pitcher feeds",
        "games": games
    }


def build_transactions():
    rows=[]; since=(datetime.now(PACIFIC).date()-timedelta(days=14)).isoformat(); until=datetime.now(PACIFIC).date().isoformat()
    for p in PLAYERS:
        try:
            data=get_json(f"https://statsapi.mlb.com/api/v1/people/{p['id']}",{"hydrate":f"transactions(startDate={since},endDate={until})"})
            person=(data.get("people") or [{}])[0]
            for t in person.get("transactions",[])[:5]: rows.append({"player":p["name"],"date":t.get("date"),"description":t.get("description") or t.get("typeDesc") or "Roster transaction"})
        except Exception: continue
    rows.sort(key=lambda x:x.get("date") or "",reverse=True)
    return {"generatedAt":datetime.now(timezone.utc).isoformat(),"transactions":rows[:20]}


def patch_saved_assignment(players, player):
    """Update team/level on the saved record without disturbing existing stats."""
    key = str(player["id"])
    existing = players.get(key)
    if not isinstance(existing, dict):
        return False
    try:
        team, level, team_id, source, assigned_date = fetch_recent_assignment(player)
    except Exception:
        return False
    if not (team or level):
        return False

    changed = False
    updates = {
        "recentTeam": team,
        "recentLevel": level,
        "recentTeamId": team_id,
        "assignmentSource": source,
        "assignmentDate": assigned_date,
    }
    for field, value in updates.items():
        if value is not None and existing.get(field) != value:
            existing[field] = value
            changed = True
    players[key] = existing
    return changed


def main():
    parser = argparse.ArgumentParser(description="Refresh Mustangs in Pro Ball data feeds")
    parser.add_argument("--mode", choices=["full", "live"], default="full", help="full = morning edition; live = current stats + today schedule")
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    old={}
    if OUTPUT.exists():
        try: old=json.loads(OUTPUT.read_text())
        except: pass
    players=dict(old.get("players",{}))
    errors=[]
    for p in PLAYERS:
        try:
            fresh=fetch_player(p)
            if fresh:
                players[str(p["id"])]=fresh
            else:
                patched = patch_saved_assignment(players, p)
                suffix = "; assignment updated from official transaction/game log" if patched else ""
                errors.append(f"{p['name']}: no {SEASON} professional split returned{suffix}")
        except Exception as e:
            patched = patch_saved_assignment(players, p)
            suffix = "; assignment updated while preserving saved stats" if patched else ""
            errors.append(f"{p['name']}: {e}{suffix}")
        time.sleep(.15)
    payload={"season":SEASON,"updatedAt":datetime.now(timezone.utc).isoformat(),"source":"MLB Stats API season totals with current assignment resolved from newest official transaction, then game log; multiple professional levels combined","players":players,"warnings":errors}
    OUTPUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print(f"Wrote {OUTPUT} with {len(players)} player records")
    # Today's schedule is refreshed in both modes so the live site stays useful during games.
    SCHEDULE_OUTPUT.write_text(json.dumps(build_today_schedule(), indent=2, sort_keys=True) + "\n")
    if args.mode == "full":
        summary = build_nightly_summary()
        transactions = build_transactions()
        TRANSACTIONS_OUTPUT.write_text(json.dumps(transactions, indent=2, sort_keys=True) + "\n")
        DAILY_OUTPUT.write_text(json.dumps(build_daily_article(summary or {}, transactions), indent=2, sort_keys=True) + "\n")
        print(f"Wrote full morning edition: {DAILY_OUTPUT}, {SCHEDULE_OUTPUT}, {TRANSACTIONS_OUTPUT}")
    else:
        print(f"Wrote live refresh: {OUTPUT}, {SCHEDULE_OUTPUT}")
    for e in errors: print("WARNING",e,file=sys.stderr)

if __name__=="__main__": main()
