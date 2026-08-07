# Mustangs in Pro Ball Hub — Version 4.0

A deployable, data-driven professional alumni publication for Cal Poly Baseball.

## Included

- Mustangs Daily editorial-style nightly article
- Player/Pitcher of the Night awards
- Official MLB/MiLB highlights when available
- Today's MLB games for tracked players
- Recent roster transactions
- Season leaderboards
- Searchable and sortable roster grouped MLB through Rookie, with free agents last
- Dedicated player profile pages
- MLB-first, MiLB-second official headshots
- Nightly GitHub Actions automation
- Single editable player catalog in `players.json`

## Run locally

```bash
python3 -m http.server 8000
```

Open `http://localhost:8000`.

## Deploy

Upload the folder to a GitHub repository and connect it to GitHub Pages, Cloudflare Pages, Netlify or Vercel. Enable GitHub Actions read/write permissions so the nightly workflow can commit generated JSON feeds.

## Data files

- `players.json`: player catalog
- `stats.json`: season totals and latest assignments
- `nightly_summary.json`: previous-night appearances and highlights
- `daily_article.json`: generated Mustangs Daily story and awards
- `today_schedule.json`: today's tracked MLB schedule
- `transactions.json`: recent tracked roster moves

The article generator is deterministic and source-grounded: it writes only from official game-log results collected by the updater.

## Mustangs Daily appearance detection

The nightly recap checks each player's official game log first. For MLB players it now also checks the previous day's official MLB schedule and box score when the player game-log feed returns no matching split. This prevents a completed MLB appearance from being reported as "no one played" simply because the per-player game log is delayed or incomplete.

After installing this version, run **Actions -> Nightly baseball stats -> Run workflow** once so `nightly_summary.json` and `daily_article.json` are regenerated with the corrected detection logic.


## MLB alumni history

`career_mlb.json` contains the historical Cal Poly MLB alumni roster and career regular-season totals used by the Career Leaders and MLB Alumni Career Stats sections. Each historical card links to the corresponding Baseball Reference player page. The historical roster is based on the Baseball Reference school page supplied for Cal Poly.
