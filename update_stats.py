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


def fetch_recent_assignment(player, fallback_splits=None):
    group = "hitting" if player["type"] == "hitter" else "pitching"
    url = f"https://statsapi.mlb.com/api/v1/people/{player['id']}/stats"
    try:
        data = get_json(url, {
            "stats": "gameLog", "group": group, "season": SEASON,
            "sportIds": SPORT_IDS, "hydrate": "team(sport),league,game"
        })
        logs = relevant_splits(data)
        if logs:
            latest = max(logs, key=lambda item: str(item.get("date", "")))
            team, level = assignment_from_split(latest)
            if team or level:
                return team, level
    except Exception:
        pass
    for split in reversed(fallback_splits or []):
        team, level = assignment_from_split(split)
        if team or level:
            return team, level
    return None, None

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
    recent_team, recent_level = fetch_recent_assignment(p, splits)
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
    return {"name":p["name"],"type":p["type"],"team":team,"recentTeam":recent_team or team,"recentLevel":recent_level,"stats":stats}


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


def fetch_mlb_boxscore_appearances(player, target_date):
    """Fallback for MLB players when the player gameLog endpoint misses a date.

    The schedule + official box score is the authoritative source for whether an
    MLB player actually appeared. This catches cases where a game is present in
    the box score before (or without) a matching per-player gameLog split.
    """
    if player.get("status") != "mlb" or not player.get("teamId"):
        return []

    schedule = get_json("https://statsapi.mlb.com/api/v1/schedule", {
        "sportId": 1,
        "date": target_date,
        "teamId": player["teamId"],
    })
    matches = []
    for date_block in schedule.get("dates", []):
        for game in date_block.get("games", []):
            game_pk = game.get("gamePk")
            if not game_pk:
                continue
            box = get_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore", {})
            for side, other_side in (("away", "home"), ("home", "away")):
                team_block = (box.get("teams") or {}).get(side) or {}
                players = team_block.get("players") or {}
                entry = players.get(f"ID{player['id']}")
                if not entry:
                    # Be tolerant if the provider changes the dictionary key format.
                    entry = next((v for v in players.values()
                                  if (v.get("person") or {}).get("id") == player["id"]), None)
                if not entry:
                    continue

                stats = entry.get("stats") or {}
                stat = stats.get("batting" if player["type"] == "hitter" else "pitching") or {}
                fielding = stats.get("fielding") or {}

                if player["type"] == "hitter":
                    appeared = any(num(stat, k) for k in (
                        "plateAppearances", "atBats", "runs", "hits", "baseOnBalls",
                        "hitByPitch", "stolenBases", "caughtStealing", "rbi"
                    )) or bool(fielding)
                else:
                    appeared = outs(stat.get("inningsPitched")) > 0
                if not appeared:
                    continue

                opponent_block = (box.get("teams") or {}).get(other_side) or {}
                team_name = (team_block.get("team") or {}).get("name")
                opponent_name = (opponent_block.get("team") or {}).get("name")
                matches.append({
                    "date": target_date,
                    "stat": stat,
                    "team": {"name": team_name},
                    "opponent": {"name": opponent_name},
                    "game": {"gamePk": game_pk},
                    "gamePk": game_pk,
                    "boxscoreFallback": True,
                    "appearanceOnly": player["type"] == "hitter" and not any(
                        num(stat, k) for k in ("plateAppearances", "atBats", "runs", "hits", "baseOnBalls", "hitByPitch", "stolenBases", "caughtStealing", "rbi")
                    ),
                })
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
                # MLB game logs can lag or omit a date even when the official box
                # score already shows the player. Fall back to schedule + box score.
                splits = fetch_mlb_boxscore_appearances(player, target)
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
        "source": "MLB Stats API gameLog/byDateRange, official box scores, play-by-play and MLB/MiLB game content"
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
    """Write a natural, beat-writer-style recap from verified game data.

    This intentionally avoids a fixed "database summary" voice. One-player nights
    become focused mini game stories; busy nights become roundups led by the most
    meaningful performance. Every factual detail still comes from the official
    game log, box score or play-by-play stored in nightly_summary.json.
    """
    apps = summary.get("appearances", [])
    date = summary.get("date")
    label = datetime.fromisoformat(date).strftime("%A, %B %-d, %Y") if date else "the latest games"
    transaction_rows = (transactions or {}).get("transactions", [])
    day_transactions = [t for t in transaction_rows if not date or str(t.get("date") or "")[:10] == date]

    def transaction_sentence(t):
        name = t.get("player") or "A former Mustang"
        desc = (t.get("description") or "had a roster transaction").strip()
        clean = desc.rstrip(".")
        lower = clean.lower()

        # Prefer plain baseball language when the provider description makes
        # the move clear, but never invent a destination or level.
        if "selected" in lower and "contract" in lower:
            return f"{name}'s contract was selected, putting him on the active roster."
        if "recalled" in lower:
            return f"{name} was recalled in a roster move."
        if "promot" in lower:
            return f"{name} was promoted. {clean}."
        if "optioned" in lower:
            return f"{name} was optioned in a roster move. {clean}."
        if "assigned" in lower or "reassigned" in lower:
            return f"{name} changed assignments. {clean}."
        if "injured list" in lower or "disabled list" in lower:
            return f"{name} had an injury-list move: {clean}."
        if "activated" in lower:
            return f"{name} was activated. {clean}."
        if "released" in lower:
            return f"{name} was released. {clean}."
        if "signed" in lower or "contract" in lower:
            return f"{name} had a contract move: {clean}."
        return f"{name}: {clean}."

    if not apps:
        if day_transactions:
            tx_paragraphs = [
                f"There were no confirmed game appearances for former Mustangs on {label}, but there was roster news to follow."
            ]
            tx_paragraphs.extend(transaction_sentence(t) for t in day_transactions[:6])
            if len(day_transactions) > 6:
                tx_paragraphs.append(f"{len(day_transactions)-6} additional Mustang transaction{'s' if len(day_transactions)-6 != 1 else ''} were also recorded and are listed in the Transactions section.")
            return {
                "date": date, "dateLabel": label,
                "title": "Roster Moves Lead a Quiet Day for the Mustangs",
                "paragraphs": tx_paragraphs,
                "awards": []
            }
        return {
            "date": date, "dateLabel": label,
            "title": "A Quiet Night for the Mustangs in Pro Ball",
            "paragraphs": [
                f"No tracked Cal Poly player showed up in a completed professional box score for {label}.",
                "That can happen on an off day, during travel, or when a minor-league game has not yet posted a complete official line. The roster and season totals remain current while the tracker waits for the next confirmed appearance."
            ],
            "awards": []
        }

    hitters = [a for a in apps if a.get("type") == "hitter"]
    pitchers = [a for a in apps if a.get("type") == "pitcher"]

    def hscore(a):
        st = a.get("stats", {})
        return num(st,"hits")*3 + num(st,"homeRuns")*6 + num(st,"rbi")*2 + num(st,"runs") + num(st,"stolenBases")*2 + num(st,"baseOnBalls")*.5

    def pscore(a):
        st = a.get("stats", {})
        return num(st,"strikeOuts")*2 + outs(st.get("inningsPitched")) - num(st,"earnedRuns")*4 - num(st,"baseOnBalls")*2 - num(st,"hits")

    def base_score(a):
        return hscore(a) if a.get("type") == "hitter" else pscore(a)

    def importance_bonus(a):
        bonus = 0
        for play in (a.get("gameContext") or {}).get("keyPlays") or []:
            tags = play.get("tags") or []
            if "walk-off" in tags: bonus += 20
            if "go-ahead" in tags: bonus += 14
            if "game-tying" in tags: bonus += 9
            bonus += min(int(play.get("rbi") or 0), 4)
        return bonus

    def score(a):
        return base_score(a) + importance_bonus(a)

    def inning_name(n):
        names={1:"first",2:"second",3:"third",4:"fourth",5:"fifth",6:"sixth",7:"seventh",8:"eighth",9:"ninth",10:"10th",11:"11th",12:"12th",13:"13th",14:"14th",15:"15th"}
        try: return names.get(int(n), f"{int(n)}th")
        except: return "late"

    def best_play(a):
        plays=(a.get("gameContext") or {}).get("keyPlays") or []
        if not plays: return None
        def rank(play):
            tags=play.get("tags") or []
            leverage=4 if "walk-off" in tags else 3 if "go-ahead" in tags else 2 if "game-tying" in tags else 1 if play.get("rbi") else 0
            return (leverage, int(play.get("inning") or 0), int(play.get("rbi") or 0))
        return max(plays,key=rank)

    def play_phrase(a, play):
        if not play: return None
        name=a.get("name","He")
        event=str(play.get("event") or "hit").lower()
        inning=inning_name(play.get("inning"))
        rbi=int(play.get("rbi") or 0)
        tags=play.get("tags") or []
        before=play.get("scoreBefore") or {}; after=play.get("scoreAfter") or {}
        pitcher=play.get("pitcher")
        run_word = "two-run " if rbi==2 else f"{rbi}-run " if rbi>2 else ""
        if "walk-off" in tags:
            sentence=f"{name} ended it with a {run_word}walk-off {event} in the {inning}."
        elif "go-ahead" in tags:
            sentence=f"The biggest swing came in the {inning}, when {name} delivered a {run_word}go-ahead {event}."
        elif "game-tying" in tags:
            sentence=f"{name} pulled the game even with a {run_word}game-tying {event} in the {inning}."
        elif rbi:
            sentence=f"{name} did his damage in the {inning}, driving in {rbi} run{'s' if rbi != 1 else ''} on a {event}."
        else:
            sentence=f"One of {name}'s key moments came in the {inning} on a {event}."
        if before.get("team") is not None and before.get("opponent") is not None and after.get("team") is not None and after.get("opponent") is not None:
            if before != after:
                sentence += f" The play changed the score from {before.get('team')}-{before.get('opponent')} to {after.get('team')}-{after.get('opponent')} from his club's perspective."
        if pitcher:
            sentence += f" It came against {pitcher}."
        return sentence

    def result_sentence(a):
        ctx=a.get("gameContext") or {}
        team=a.get("team") or "his club"
        opp=a.get("opponent") or "the opposition"
        tf=ctx.get("teamFinal"); of=ctx.get("opponentFinal")
        result=ctx.get("result")
        if tf is None or of is None: return None
        if result=="win": return f"{team} made it stand up in a {tf}-{of} win over {opp}."
        if result=="loss": return f"It came in a {tf}-{of} loss to {opp}."
        return f"The game finished {tf}-{of} against {opp}."

    def hitter_line(a):
        st=a.get("stats",{}); name=a.get("name","He")
        ab=int(num(st,"atBats")); h=int(num(st,"hits")); rbi=int(num(st,"rbi")); runs=int(num(st,"runs")); bb=int(num(st,"baseOnBalls")); so=int(num(st,"strikeOuts")); sb=int(num(st,"stolenBases")); hr=int(num(st,"homeRuns")); doubles=int(num(st,"doubles")); triples=int(num(st,"triples"))
        pieces=[f"{h}-for-{ab}"] if ab or h else []
        if hr: pieces.append(f"{hr} HR" if hr>1 else "a home run")
        if triples: pieces.append(f"{triples} triples" if triples>1 else "a triple")
        if doubles: pieces.append(f"{doubles} doubles" if doubles>1 else "a double")
        if rbi: pieces.append(f"{rbi} RBI")
        if runs: pieces.append(f"{runs} runs" if runs>1 else "a run scored")
        if bb: pieces.append(f"{bb} walks" if bb>1 else "a walk")
        if sb: pieces.append(f"{sb} stolen bases" if sb>1 else "a stolen base")
        text=f"{name} finished " + ", ".join(pieces) + "." if pieces else a.get("summary","")
        if so and so >= 2: text += f" He struck out {so} times."
        return text

    def pitcher_line(a):
        st=a.get("stats",{}); name=a.get("name","He")
        ip=st.get("inningsPitched") or "0.0"; h=int(num(st,"hits")); er=int(num(st,"earnedRuns")); bb=int(num(st,"baseOnBalls")); k=int(num(st,"strikeOuts"))
        text=f"{name} worked {ip} innings, allowing {h} hit{'s' if h!=1 else ''} and {er} earned run{'s' if er!=1 else ''} with {bb} walk{'s' if bb!=1 else ''} and {k} strikeout{'s' if k!=1 else ''}."
        ctx=a.get("gameContext") or {}; entry=ctx.get("pitchingEntry") or {}
        if entry.get("inning"):
            text += f" He entered in the {inning_name(entry.get('inning'))} with the score {entry.get('teamScore')}-{entry.get('opponentScore')} from his team's perspective."
        return text

    def line_sentence(a):
        return hitter_line(a) if a.get("type")=="hitter" else pitcher_line(a)

    def lineup_sentence(a):
        ctx=a.get("gameContext") or {}; bits=[]
        if ctx.get("battingOrder"): bits.append(f"hit {ctx.get('battingOrder')} in the order")
        if ctx.get("position"): bits.append(f"played {ctx.get('position')}")
        if not bits: return None
        return a.get("name","He") + " " + " and ".join(bits) + "."

    def team_level_phrase(a):
        team=a.get("team"); level=a.get("level")
        if team and level and level!="MLB": return f"with {team} at {level}"
        if team: return f"with {team}"
        return "in pro ball"

    star=max(apps,key=score)
    star_play=best_play(star)
    tags=(star_play or {}).get("tags") or []
    name=star.get("name","A Mustang")
    if "walk-off" in tags:
        headline=f"{name} walks it off in the night's biggest Mustang moment"
    elif "go-ahead" in tags:
        headline=f"{name} comes through late with go-ahead hit"
    elif "game-tying" in tags:
        headline=f"{name} delivers in the clutch as Mustangs take the field"
    elif num(star.get("stats",{}),"homeRuns"):
        headline=f"{name} goes deep to lead the Mustangs' night in pro ball"
    elif star.get("type")=="pitcher" and num(star.get("stats",{}),"strikeOuts")>=5:
        headline=f"{name} misses bats in a strong night on the mound"
    elif len(apps)==1:
        headline=f"{name} carries the Mustang flag in Thursday's pro action" if date else f"{name} carries the Mustang flag in pro action"
    else:
        headline=f"{name} leads the way on a busy night for former Mustangs"

    paragraphs=[]
    if len(apps)==1:
        # A focused mini game story reads much more naturally than pretending one
        # appearance was a broad organizational roundup.
        if star_play and ("go-ahead" in tags or "walk-off" in tags or "game-tying" in tags):
            if "go-ahead" in tags:
                paragraphs.append(f"{name} didn't need a pile of hits to leave his mark on {label}. He came up with the one that mattered most.")
            elif "walk-off" in tags:
                paragraphs.append(f"{name} saved his biggest moment for the end on {label}, giving Cal Poly fans a late-game highlight to remember.")
            else:
                paragraphs.append(f"{name} found himself in the middle of the game's biggest moment on {label}, coming through when his club needed a run.")
        else:
            paragraphs.append(f"It was a light night for Cal Poly's pro alumni on {label}, with {name} the only tracked Mustang to appear in a completed game.")
    else:
        teams=len({a.get('team') for a in apps if a.get('team')})
        levels=len({a.get('level') for a in apps if a.get('level')})
        scope=f"{len(apps)} former Mustangs appeared"
        if teams: scope += f" for {teams} club{'s' if teams!=1 else ''}"
        if levels>1: scope += f" across {levels} levels"
        paragraphs.append(f"There was plenty to follow around pro baseball on {label}. {scope}, and {name} gave the night its defining moment.")

    key=play_phrase(star,star_play)
    if key: paragraphs.append(key)
    result=result_sentence(star)
    if result: paragraphs.append(result)
    paragraphs.append(line_sentence(star))
    lineup=lineup_sentence(star)
    if lineup: paragraphs.append(lineup)

    # Add a little context without inventing narrative. This is intentionally
    # plainspoken and varies based on what the verified data actually says.
    if star.get("team"):
        if star.get("level") and star.get("level") != "MLB":
            paragraphs.append(f"For Cal Poly followers, it was another look at {name} {team_level_phrase(star)} as the season moves deeper into August.")
        elif len(apps)==1:
            paragraphs.append(f"For Cal Poly followers, the night belonged to {name} and the {star.get('team')}. The box score may be brief, but the game context tells the better story.")

    others=sorted([a for a in apps if a is not star],key=score,reverse=True)
    for i,a in enumerate(others[:6]):
        p=best_play(a); key=play_phrase(a,p)
        if key: paragraphs.append(key)
        text=line_sentence(a)
        if text: paragraphs.append(text)
        res=result_sentence(a)
        if res and i<3: paragraphs.append(res)

    remaining=others[6:]
    if remaining:
        names=[a.get("name") for a in remaining if a.get("name")]
        if names:
            name_text=names[0] if len(names)==1 else ", ".join(names[:-1])+" and "+names[-1]
            paragraphs.append(f"Also getting into games were {name_text}.")

    if day_transactions:
        paragraphs.append("There was roster news to go with the action on the field.")
        for t in day_transactions[:6]:
            paragraphs.append(transaction_sentence(t))
        if len(day_transactions) > 6:
            paragraphs.append(f"{len(day_transactions)-6} additional Mustang transaction{'s' if len(day_transactions)-6 != 1 else ''} were recorded and are listed in the Transactions section.")

    clips=sum(len(a.get("highlights",[])) for a in apps)
    if clips:
        paragraphs.append(f"There {'was' if clips==1 else 'were'} {clips} official player-tagged highlight clip{'s' if clips!=1 else ''} available from the night's games, and they are included below when the video feed allows playback.")
    elif len(apps)>1:
        paragraphs.append("The next Mustangs Daily will pick up with the next set of completed games and any official player-tagged video that becomes available.")

    awards=[{"label":"Player of the Night","player":star["name"],"playerId":star.get("playerId"),"type":star.get("type"),"team":star.get("team"),"line":star["summary"]}]
    if hitters:
        b=max(hitters,key=hscore)
        awards.append({"label":"Top Hitter","player":b["name"],"playerId":b.get("playerId"),"type":"hitter","team":b.get("team"),"line":b["summary"]})
    if pitchers:
        b=max(pitchers,key=pscore)
        awards.append({"label":"Top Pitcher","player":b["name"],"playerId":b.get("playerId"),"type":"pitcher","team":b.get("team"),"line":b["summary"]})
    return {"date":date,"dateLabel":label,"title":headline,"paragraphs":paragraphs,"awards":awards}

def build_today_schedule():
    """Build today's MLB slate and, when available, current box-score lines.

    This feed is refreshed every 30 minutes. The box score gives the homepage a
    genuinely useful in-game snapshot instead of waiting for season totals to
    settle after the final out.
    """
    today = datetime.now(PACIFIC).date().isoformat()
    games = []
    try:
        data = get_json("https://statsapi.mlb.com/api/v1/schedule", {
            "sportId": 1, "date": today, "hydrate": "team,probablePitcher"
        })
        by_team = {}
        game_cache = {}
        for d in data.get("dates", []):
            for g in d.get("games", []):
                away_block = g.get("teams", {}).get("away", {})
                home_block = g.get("teams", {}).get("home", {})
                away = away_block.get("team", {})
                home = home_block.get("team", {})
                item = {
                    "gamePk": g.get("gamePk"),
                    "gameDate": g.get("gameDate"),
                    "status": g.get("status", {}).get("detailedState"),
                    "away": away.get("name"), "home": home.get("name"),
                    "awayTeamId": away.get("id"), "homeTeamId": home.get("id"),
                    "awayScore": away_block.get("score"), "homeScore": home_block.get("score"),
                    "awayProbablePitcher": away_block.get("probablePitcher"),
                    "homeProbablePitcher": home_block.get("probablePitcher"),
                    "venue": (g.get("venue") or {}).get("name"),
                }
                game_cache[g.get("gamePk")] = item
                by_team[away.get("id")] = (away.get("name"), home.get("name"), item)
                by_team[home.get("id")] = (home.get("name"), away.get("name"), item)

        box_cache = {}
        catalog = json.loads(PLAYERS_FILE.read_text()).get("players", [])
        for p in catalog:
            team_id = p.get("teamId")
            if p.get("status") != "mlb" or team_id not in by_team:
                continue
            team, opponent, game = by_team[team_id]
            live_line = None
            game_pk = game.get("gamePk")
            if game_pk and game.get("status") not in ("Scheduled", "Pre-Game", "Warmup"):
                try:
                    if game_pk not in box_cache:
                        box_cache[game_pk] = get_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore", {})
                    box = box_cache[game_pk]
                    person_key = f"ID{p.get('mlbId')}"
                    for side in ("away", "home"):
                        player_row = ((box.get("teams") or {}).get(side) or {}).get("players", {}).get(person_key)
                        if not player_row:
                            continue
                        if p.get("type") == "hitter":
                            st = (player_row.get("stats") or {}).get("batting") or {}
                            ab = int(num(st, "atBats")); h = int(num(st, "hits")); hr = int(num(st, "homeRuns")); rbi = int(num(st, "rbi")); runs = int(num(st, "runs"))
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
            games.append({
                "player": p["name"], "team": team, "opponent": opponent,
                "gamePk": game_pk, "gameDate": game.get("gameDate"),
                "status": game.get("status"), "timeLabel": game.get("gameDate"),
                "away": game.get("away"), "home": game.get("home"),
                "awayTeamId": game.get("awayTeamId"), "homeTeamId": game.get("homeTeamId"),
                "awayProbablePitcher": game.get("awayProbablePitcher"),
                "homeProbablePitcher": game.get("homeProbablePitcher"),
                "venue": game.get("venue"),
                "awayScore": game.get("awayScore"), "homeScore": game.get("homeScore"),
                "liveLine": live_line,
            })
    except Exception:
        pass
    return {"date": today, "generatedAt": datetime.now(timezone.utc).isoformat(), "games": games}

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
            if fresh: players[str(p["id"])]=fresh
            else: errors.append(f"{p['name']}: no {SEASON} professional split returned")
        except Exception as e:
            errors.append(f"{p['name']}: {e}")
        time.sleep(.15)
    payload={"season":SEASON,"updatedAt":datetime.now(timezone.utc).isoformat(),"source":"MLB Stats API season totals and latest game-log assignment; multiple professional levels combined","players":players,"warnings":errors}
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
