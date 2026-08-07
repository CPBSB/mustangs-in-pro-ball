# Mustangs in Pro Ball — Version 5.0

A data-driven Cal Poly professional baseball hub designed for GitHub + Cloudflare Pages.

## What Version 5.0 does

- Displays the current professional roster from MLB through Rookie ball.
- Loads current season statistics from `data/stats.json`.
- Publishes a morning `Mustangs Daily` story from the previous day's official game logs.
- Shows official player-tagged highlights when MLB/MiLB supplies them.
- Shows today's MLB games and, during games, a current box-score line when available.
- Shows recent player transactions.
- Includes Cal Poly MLB career leaders and historical MLB alumni cards linked to Baseball Reference.
- Uses MLB/MiLB headshots with image fallbacks.
- Refreshes current stats and today's games every 30 minutes using GitHub Actions.
- Runs a full morning refresh once per day.
- Automatically redeploys on Cloudflare Pages whenever GitHub receives a data commit.

## Repository layout

```text
.github/workflows/
  live-refresh.yml       # every 30 minutes
  morning-edition.yml    # full morning edition
  validate.yml           # checks code on pushes

data/
  players.json
  stats.json
  nightly_summary.json
  daily_article.json
  today_schedule.json
  transactions.json
  career_mlb.json
scripts/
  update_stats.py
index.html
player.html
app.js
styles.css
_headers
_redirects
```

## Important idea

GitHub is the filing cabinet and automation engine. Cloudflare Pages is the public website.

When GitHub Actions changes a file in `data/`, it commits that file to `main`. A Cloudflare Pages project connected to the GitHub repository sees that commit and automatically publishes the new version.

## First-time setup

Read `SETUP_CLOUDFLARE.md`. It is intentionally written as a click-by-click guide.

## Manual test

In GitHub, open **Actions** and run either:

- **Publish morning edition** — refreshes all feeds and writes Mustangs Daily.
- **Refresh live baseball data** — refreshes current stats and today's games.

## Data safety

The updater preserves previous player stats when a provider request fails. The nightly summary also preserves the prior successful recap when every game-log request fails, instead of falsely publishing that nobody played.

## Notes about "live"

The 30-minute job is near-live, not pitch-by-pitch. Current MLB box-score lines can appear during a game. Minor-league live availability depends on what MLB/MiLB exposes through the public data feeds.

## V5.1 Daily-first update

This build adds:

- richer Mustangs Daily story generation from all detected previous-day appearances;
- a second one-day `byDateRange` lookup for MiLB players when the normal game log lags;
- MLB box-score fallback for missed major-league appearances;
- Player of the Night / Top Hitter / Top Pitcher cards with headshots;
- inline official MLB/MiLB video playback when a clip URL is available;
- scoreboard-style hero counters;
- cleaner player cards with separate Mustang and official-profile links;
- transaction-wire styling;
- morning edition publishing plus the existing 30-minute live refresh workflow.

After replacing the repository files, run **Publish morning edition** manually once from GitHub Actions to generate a fresh Mustangs Daily using the new appearance-detection logic.
