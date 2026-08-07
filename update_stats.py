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
                appearances.append({
                    "playerId": player["id"], "name": player["name"], "type": player["type"],
                    "team": team, "level": level, "opponent": opponent, "gamePk": game_pk,
                    "summary": recap, "stats": stat, "highlights": highlights
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
        "source": "MLB Stats API gameLog/byDateRange, MLB box scores and official MLB/MiLB game content"
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



def build_daily_article(summary):
    """Turn verified nightly game logs into a fuller newspaper-style recap.

    The prose deliberately stays inside facts present in the official game-log
    payload. It does not infer scores, streaks, standings or milestones that the
    updater did not retrieve.
    """
    apps = summary.get("appearances", [])
    date = summary.get("date")
    label = datetime.fromisoformat(date).strftime("%A, %B %-d, %Y") if date else "Latest report"
    if not apps:
        return {
            "date": date, "dateLabel": label,
            "title": "A Quiet Night Across Pro Ball",
            "paragraphs": [
                summary.get("intro", "No tracked Mustangs appeared in official game logs."),
                "No individual performance was added to the nightly ledger, but the professional roster, current assignments and season leaderboards remain available throughout the site.",
                "Mustangs Daily will return with a full recap after the next slate of games, including official player-tagged MLB or MiLB video whenever it is published."
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
    def score(a):
        return hscore(a) if a.get("type") == "hitter" else pscore(a)

    def context(a):
        team = a.get("team")
        opponent = a.get("opponent")
        level = a.get("level")
        bits = []
        if team: bits.append(f"for {team}")
        if opponent: bits.append(f"against {opponent}")
        if level and level != "MLB": bits.append(f"at the {level} level")
        return " ".join(bits)

    def detail_sentence(a):
        st = a.get("stats", {})
        name = a.get("name", "The Mustang")
        ctx = context(a)
        if a.get("type") == "hitter":
            ab = num(st,"atBats"); h = num(st,"hits"); hr = num(st,"homeRuns")
            doubles = num(st,"doubles"); triples = num(st,"triples")
            rbi = num(st,"rbi"); runs = num(st,"runs"); bb = num(st,"baseOnBalls")
            so = num(st,"strikeOuts"); sb = num(st,"stolenBases")
            parts = []
            if ab or h: parts.append(f"{h} hit{'s' if h != 1 else ''} in {int(ab)} at-bat{'s' if ab != 1 else ''}")
            if hr: parts.append(f"{int(hr)} home run{'s' if hr != 1 else ''}")
            if doubles: parts.append(f"{int(doubles)} double{'s' if doubles != 1 else ''}")
            if triples: parts.append(f"{int(triples)} triple{'s' if triples != 1 else ''}")
            if rbi: parts.append(f"{int(rbi)} RBI")
            if runs: parts.append(f"{int(runs)} run{'s' if runs != 1 else ''} scored")
            if bb: parts.append(f"{int(bb)} walk{'s' if bb != 1 else ''}")
            if sb: parts.append(f"{int(sb)} stolen base{'s' if sb != 1 else ''}")
            if so: parts.append(f"{int(so)} strikeout{'s' if so != 1 else ''}")
            if parts:
                return f"{name} finished with " + ", ".join(parts) + (f" {ctx}" if ctx else "") + "."
        else:
            ip = st.get("inningsPitched")
            h = num(st,"hits"); er = num(st,"earnedRuns"); r = num(st,"runs")
            bb = num(st,"baseOnBalls"); so = num(st,"strikeOuts")
            pieces = []
            if ip is not None: pieces.append(f"{ip} innings")
            pieces.append(f"{int(h)} hit{'s' if h != 1 else ''} allowed")
            pieces.append(f"{int(er)} earned run{'s' if er != 1 else ''}")
            if r != er: pieces.append(f"{int(r)} total run{'s' if r != 1 else ''}")
            pieces.append(f"{int(bb)} walk{'s' if bb != 1 else ''}")
            pieces.append(f"{int(so)} strikeout{'s' if so != 1 else ''}")
            return f"{name} worked " + ", ".join(pieces) + (f" {ctx}" if ctx else "") + "."
        return a.get("summary", "")

    star = max(apps, key=score)
    headline = f"{star['name']} Sets the Pace for Mustangs in Pro Ball"
    if num(star.get("stats", {}), "homeRuns"):
        headline = f"{star['name']} Powers the Mustangs' Pro Ball Roundup"
    elif star.get("type") == "pitcher" and num(star.get("stats", {}), "strikeOuts") >= 5:
        headline = f"{star['name']} Headlines the Night on the Mound"
    elif len(apps) >= 4:
        headline = f"{star['name']} Leads a Busy Night for Mustangs in Pro Ball"

    # Opening: establish the night's scope, then identify the headline performance.
    team_count = len({a.get("team") for a in apps if a.get("team")})
    level_count = len({a.get("level") for a in apps if a.get("level")})
    opening = f"Cal Poly alumni were active across professional baseball on {label}, with {len(apps)} tracked Mustang{'s' if len(apps) != 1 else ''} recording an appearance"
    if team_count:
        opening += f" for {team_count} professional club{'s' if team_count != 1 else ''}"
    if level_count > 1:
        opening += f" across {level_count} levels"
    opening += f". {star['name']} supplied the headline performance."

    paragraphs = [opening, detail_sentence(star)]

    # Give the next most notable players their own paragraphs instead of one
    # compressed sentence. This makes the article read like a real roundup.
    others = sorted([a for a in apps if a is not star], key=score, reverse=True)
    for a in others[:5]:
        text = detail_sentence(a)
        if text:
            paragraphs.append(text)

    # If there are more than six appearances, summarize the remaining names so
    # everyone is acknowledged without turning the lead story into a box score.
    remaining = others[5:]
    if remaining:
        names = [a.get("name") for a in remaining if a.get("name")]
        if names:
            if len(names) == 1:
                name_text = names[0]
            else:
                name_text = ", ".join(names[:-1]) + " and " + names[-1]
            paragraphs.append(f"Also appearing during the night were {name_text}, giving the Mustangs representation throughout the professional ladder.")

    levels = sorted({a.get("level") for a in apps if a.get("level")})
    closing = "The nightly roundup is compiled from official MLB and MiLB game logs and box scores."
    if levels:
        closing += " The night's appearances came from " + ", ".join(levels) + "."
    clips = sum(len(a.get("highlights", [])) for a in apps)
    if clips:
        closing += f" {clips} official player-tagged highlight clip{'s were' if clips != 1 else ' was'} available with the report."
    else:
        closing += " Official player-tagged video is added when MLB or MiLB publishes it."
    paragraphs.append(closing)

    awards = [{
        "label": "Player of the Night", "player": star["name"], "playerId": star.get("playerId"),
        "type": star.get("type"), "team": star.get("team"), "line": star["summary"]
    }]
    if hitters:
        b = max(hitters, key=hscore)
        awards.append({"label":"Top Hitter","player":b["name"],"playerId":b.get("playerId"),"type":"hitter","team":b.get("team"),"line":b["summary"]})
    if pitchers:
        b = max(pitchers, key=pscore)
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
        DAILY_OUTPUT.write_text(json.dumps(build_daily_article(summary or {}), indent=2, sort_keys=True) + "\n")
        TRANSACTIONS_OUTPUT.write_text(json.dumps(build_transactions(), indent=2, sort_keys=True) + "\n")
        print(f"Wrote full morning edition: {DAILY_OUTPUT}, {SCHEDULE_OUTPUT}, {TRANSACTIONS_OUTPUT}")
    else:
        print(f"Wrote live refresh: {OUTPUT}, {SCHEDULE_OUTPUT}")
    for e in errors: print("WARNING",e,file=sys.stderr)

if __name__=="__main__": main()
