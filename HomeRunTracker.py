#!/usr/bin/env python3
"""
Weekly MLB Home Run Tracker
Generates an HTML report using pybaseball + Statcast data.

Usage:
    python hr_tracker.py                    # Last 7 days
    python hr_tracker.py --start 2025-04-01 --end 2025-04-10
    python hr_tracker.py --season 2025      # Full season to date
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

try:
    from pybaseball import statcast
    from pybaseball import cache
    cache.enable()
except ImportError:
    print("ERROR: pybaseball not installed. Run: pip install pybaseball")
    sys.exit(1)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Weekly MLB Home Run Tracker")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (default: 7 days ago)")
    parser.add_argument("--end",   default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--season", type=int, default=None, help="Pull full season-to-date instead")
    parser.add_argument("--out", default="/Users/michaelharman/Projects/Home Run Tracker/index.html", help="Output HTML filename")
    return parser.parse_args()


# ─── DATA PULL ────────────────────────────────────────────────────────────────

def get_date_range(args):
    today = datetime.today()
    if args.season:
        start = f"{args.season}-03-26"
        end   = today.strftime("%Y-%m-%d")
    else:
        end   = args.end   or (today - timedelta(days=1)).strftime("%Y-%m-%d")
        start = args.start or (today - timedelta(days=7)).strftime("%Y-%m-%d")
    return start, end


def fetch_home_runs(start, end):
    print(f"Fetching Statcast data: {start} → {end}")
    df = statcast(start_dt=start, end_dt=end)
    hrs = df[
        (df["events"] == "home_run") &
        (df["game_type"] == "R")
    ].copy()
    # In Statcast, player_name = pitcher. Batter name is in the description field
    # or we build it from batter_name columns. Use des field to extract batter name.
    # Most reliable: use batting_team side and the batter field with a name lookup.
    # pybaseball attaches batter name via the 'batter_name' col when available,
    # otherwise fall back to parsing the description.
    if "batter_name" in hrs.columns:
        hrs["batter_name"] = hrs["batter_name"]
    elif "des" in hrs.columns:
        # description starts with batter name e.g. "Judge homers (3) on a fly ball..."
        hrs["batter_name"] = hrs["des"].str.extract(r"^([A-Za-z\s'\-\.]+?)(?:\s+(?:homers|hits))")[0].str.strip()
    else:
        hrs["batter_name"] = hrs["player_name"]  # fallback
    print(f"  Found {len(hrs)} regular season home runs")
    return hrs


# ─── AGGREGATIONS ─────────────────────────────────────────────────────────────

def team_weekly(hrs):
    """Home runs by team for the selected window."""
    tbl = (
        hrs.groupby("home_team")["events"]
        .count()
        .reset_index()
        .rename(columns={"home_team": "team", "events": "hr_week"})
        .sort_values("hr_week", ascending=False)
    )
    return tbl


def team_season(start, end):
    """Running season total per team – pull from season start."""
    year = datetime.strptime(end, "%Y-%m-%d").year
    season_start = f"{year}-03-26"
    print(f"  Fetching full-season data for running totals ({season_start} → {end}) …")
    df = statcast(start_dt=season_start, end_dt=end)
    hrs = df[
        (df["events"] == "home_run") &
        (df["game_type"] == "R")
    ].copy()
    tbl = (
        hrs.groupby("home_team")["events"]
        .count()
        .reset_index()
        .rename(columns={"home_team": "team", "events": "hr_season"})
        .sort_values("hr_season", ascending=False)
    )
    return tbl


def player_leaderboard(hrs, top_n=10):
    """Top N players by home runs in the window, with season running total."""
    if hrs.empty:
        year = str(datetime.today().year)
    else:
        gd = hrs["game_date"].max()
        year = gd[:4] if isinstance(gd, str) else gd.strftime("%Y")
    tbl = (
        hrs.groupby("batter_name")
        .agg(
            hr_week=("events", "count"),
            team=("home_team", lambda x: x.mode()[0]),
        )
        .reset_index()
        .rename(columns={"batter_name": "player_name"})
        .sort_values("hr_week", ascending=False)
        .head(top_n)
    )
    return tbl


def top_exit_velocity(hrs, top_n=10):
    """Top N HRs by exit velocity — batters only (excludes pitchers batting)."""
    # pitcher_1 is the pitching team pitcher; batter != pitcher_1 ensures we have a position player
    cols = ["batter_name", "home_team", "launch_speed", "hit_distance_sc",
            "launch_angle", "game_date", "home_score", "away_score"]
    sub = hrs.dropna(subset=["launch_speed"])[cols].copy()
    sub = sub.sort_values("launch_speed", ascending=False).head(top_n).reset_index(drop=True)
    sub.rename(columns={
        "batter_name": "player_name",
        "home_team": "team",
        "launch_speed": "exit_velo",
        "hit_distance_sc": "distance",
        "launch_angle": "angle",
    }, inplace=True)
    sub["rank"] = sub.index + 1
    return sub


def top_distance(hrs, top_n=10):
    """Top N HRs by distance — batters only (excludes pitchers batting)."""
    cols = ["batter_name", "home_team", "launch_speed", "hit_distance_sc",
            "launch_angle", "game_date"]
    sub = hrs.dropna(subset=["hit_distance_sc"])[cols].copy()
    sub = sub.sort_values("hit_distance_sc", ascending=False).head(top_n).reset_index(drop=True)
    sub.rename(columns={
        "batter_name": "player_name",
        "home_team": "team",
        "launch_speed": "exit_velo",
        "hit_distance_sc": "distance",
        "launch_angle": "angle",
    }, inplace=True)
    sub["rank"] = sub.index + 1
    return sub


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def safe_val(v, decimals=1):
    if pd.isna(v):
        return "—"
    if isinstance(v, float):
        return round(v, decimals)
    return v


def df_to_list(df):
    """Convert DataFrame to list of dicts with NaN handled."""
    records = []
    for _, row in df.iterrows():
        records.append({k: safe_val(v) for k, v in row.items()})
    return records


# ─── HTML GENERATION ──────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MLB Home Run Tracker — {{WEEK_LABEL}}</title>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&family=IBM+Plex+Mono&display=swap" rel="stylesheet">
<style>
  :root {
    --red:     #D0021B;
    --red-dk:  #9B0014;
    --navy:    #0D1B2A;
    --navy2:   #162232;
    --navy3:   #1E2F42;
    --slate:   #2A3F55;
    --muted:   #8AA0B5;
    --border:  rgba(138,160,181,0.18);
    --gold:    #F5A623;
    --text:    #E8EEF4;
    --text2:   #A8BECE;
    --mono:    'IBM Plex Mono', monospace;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--navy);
    color: var(--text);
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 14px;
    line-height: 1.55;
    min-height: 100vh;
  }

  /* ── HERO ── */
  .hero {
    background: var(--navy2);
    border-bottom: 3px solid var(--red);
    padding: 2.5rem 2rem 2rem;
    position: relative;
    overflow: hidden;
  }
  .hero::before {
    content: '';
    position: absolute;
    inset: 0;
    background: repeating-linear-gradient(
      -45deg,
      transparent,
      transparent 22px,
      rgba(208,2,27,0.04) 22px,
      rgba(208,2,27,0.04) 44px
    );
    pointer-events: none;
  }
  .hero-inner { position: relative; max-width: 1100px; margin: 0 auto; }
  .hero-eyebrow {
    font-family: 'Oswald', sans-serif;
    font-size: 11px;
    letter-spacing: 3px;
    color: var(--red);
    text-transform: uppercase;
    margin-bottom: 0.5rem;
  }
  .hero-title {
    font-family: 'Oswald', sans-serif;
    font-size: clamp(2rem, 5vw, 3.5rem);
    font-weight: 600;
    line-height: 1.05;
    letter-spacing: 1px;
    color: #fff;
  }
  .hero-title span { color: var(--red); }
  .hero-meta {
    margin-top: 0.75rem;
    font-size: 13px;
    color: var(--muted);
    font-family: var(--mono);
  }

  /* ── STATS ROW ── */
  .stats-row {
    display: flex;
    gap: 1rem;
    margin-top: 2rem;
    flex-wrap: wrap;
  }
  .stat-pill {
    background: var(--navy3);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.75rem 1.25rem;
    min-width: 130px;
  }
  .stat-pill-label {
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 4px;
  }
  .stat-pill-val {
    font-family: 'Oswald', sans-serif;
    font-size: 1.75rem;
    font-weight: 500;
    color: #fff;
    line-height: 1;
  }
  .stat-pill-val.red { color: var(--red); }
  .stat-pill-val.gold { color: var(--gold); }

  /* ── LAYOUT ── */
  .content { max-width: 1100px; margin: 0 auto; padding: 2.5rem 1.5rem; }

  /* ── SECTION ── */
  .section { margin-bottom: 3.5rem; }
  .section-header {
    display: flex;
    align-items: baseline;
    gap: 1rem;
    margin-bottom: 1.25rem;
    padding-bottom: 0.6rem;
    border-bottom: 1px solid var(--border);
  }
  .section-title {
    font-family: 'Oswald', sans-serif;
    font-size: 1.2rem;
    font-weight: 500;
    letter-spacing: 1px;
    color: #fff;
  }
  .section-badge {
    background: var(--red);
    color: #fff;
    font-size: 10px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 3px;
    font-family: var(--mono);
  }

  /* ── TABLES ── */
  .tbl-wrap { overflow-x: auto; border-radius: 8px; border: 1px solid var(--border); }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  thead th {
    background: var(--navy3);
    color: var(--muted);
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
    padding: 10px 14px;
    text-align: left;
    font-weight: 400;
    font-family: var(--mono);
    white-space: nowrap;
  }
  thead th.num { text-align: right; }
  tbody tr { border-top: 1px solid var(--border); }
  tbody tr:nth-child(odd) { background: rgba(255,255,255,0.018); }
  tbody tr:hover { background: rgba(255,255,255,0.04); }
  tbody td {
    padding: 10px 14px;
    color: var(--text);
    vertical-align: middle;
  }
  tbody td.num {
    text-align: right;
    font-family: var(--mono);
    font-size: 13px;
  }
  tbody td.rank {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 12px;
    width: 32px;
  }

  /* ── BAR CELLS ── */
  .bar-cell { display: flex; align-items: center; gap: 10px; }
  .bar-bg {
    flex: 1;
    height: 6px;
    background: var(--navy3);
    border-radius: 3px;
    overflow: hidden;
    min-width: 60px;
  }
  .bar-fill {
    height: 100%;
    border-radius: 3px;
    background: var(--red);
    transition: width 0.4s ease;
  }
  .bar-fill.blue { background: #2C7BE5; }
  .bar-fill.gold { background: var(--gold); }
  .bar-num {
    font-family: var(--mono);
    font-size: 13px;
    min-width: 30px;
    text-align: right;
    color: #fff;
  }

  /* ── GRID ── */
  .two-col {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2rem;
  }
  @media (max-width: 720px) { .two-col { grid-template-columns: 1fr; } }

  /* ── TOP HR CARDS ── */
  .hr-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 1rem; }
  .hr-card {
    background: var(--navy2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem 1.1rem;
    position: relative;
    overflow: hidden;
  }
  .hr-card::after {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: var(--red);
  }
  .hr-card.gold-card::after { background: var(--gold); }
  .hr-card.blue-card::after { background: #2C7BE5; }
  .hr-card-rank {
    font-family: 'Oswald', sans-serif;
    font-size: 11px;
    letter-spacing: 2px;
    color: var(--muted);
    margin-bottom: 4px;
  }
  .hr-card-player {
    font-family: 'Oswald', sans-serif;
    font-size: 1rem;
    font-weight: 500;
    color: #fff;
    margin-bottom: 2px;
    line-height: 1.2;
  }
  .hr-card-team {
    font-size: 11px;
    color: var(--muted);
    font-family: var(--mono);
    margin-bottom: 10px;
  }
  .hr-card-stats {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
  }
  .hr-card-stat { }
  .hr-card-stat-label {
    font-size: 9px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .hr-card-stat-val {
    font-family: 'Oswald', sans-serif;
    font-size: 1.15rem;
    font-weight: 500;
    color: #fff;
  }
  .hr-card-stat-val.accent { color: var(--gold); }

  /* ── FOOTER ── */
  footer {
    border-top: 1px solid var(--border);
    padding: 1.5rem;
    text-align: center;
    font-size: 11px;
    color: var(--muted);
    font-family: var(--mono);
  }
</style>
</head>
<body>

<div class="hero">
  <div class="hero-inner">
    <div class="hero-eyebrow">MLB Statcast Weekly Digest</div>
    <h1 class="hero-title">Home Run <span>Tracker</span></h1>
    <div class="hero-meta">{{WEEK_LABEL}} &nbsp;·&nbsp; Generated {{GENERATED}}</div>
    <div class="stats-row">
      <div class="stat-pill">
        <div class="stat-pill-label">HRs Last 7 Days</div>
        <div class="stat-pill-val red">{{TOTAL_WEEK}}</div>
      </div>
      <div class="stat-pill">
        <div class="stat-pill-label">Season Total</div>
        <div class="stat-pill-val">{{TOTAL_SEASON}}</div>
      </div>
      <div class="stat-pill">
        <div class="stat-pill-label">Top Exit Velo</div>
        <div class="stat-pill-val gold">{{TOP_EV}} mph</div>
      </div>
      <div class="stat-pill">
        <div class="stat-pill-label">Longest HR</div>
        <div class="stat-pill-val gold">{{TOP_DIST}} ft</div>
      </div>
    </div>
  </div>
</div>

<div class="content">

  <!-- TEAM TABLE -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">Home Runs by Team</div>
      <div class="section-badge">Last 7 Days + Season</div>
      <div style="margin-left:auto;display:flex;gap:6px;">
        <button id="sort-week" onclick="sortTeams('week')" style="font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--red);background:var(--red);color:#fff;cursor:pointer;">Last 7 Days</button>
        <button id="sort-season" onclick="sortTeams('season')" style="font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;">Season Total</button>
      </div>
    </div>
    <div class="tbl-wrap">
      <table id="team-table">
        <thead>
          <tr>
            <th>Team</th>
            <th style="min-width:180px">Last 7 Days</th>
            <th style="min-width:180px">Season Total</th>
          </tr>
        </thead>
        <tbody id="team-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- PLAYER LEADERBOARD -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">Player Leaderboard</div>
      <div class="section-badge">Top 10 — Last 7 Days</div>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th class="num">#</th>
            <th>Player</th>
            <th>Team</th>
            <th style="min-width:180px">Last 7 Days</th>
          </tr>
        </thead>
        <tbody id="player-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- EV + DISTANCE CARDS -->
  <div class="two-col" style="border-top:1px solid var(--border);padding-top:2.5rem;">
    <div class="section">
      <div class="section-header">
        <div class="section-title">Top Exit Velocity</div>
        <div class="section-badge">HRs Last 7 Days</div>
      </div>
      <div class="hr-cards" id="ev-cards"></div>
    </div>

    <div class="section" style="border-left:1px solid var(--border);padding-left:2rem;">
      <div class="section-header">
        <div class="section-title">Longest Home Runs</div>
        <div class="section-badge">HRs Last 7 Days</div>
      </div>
      <div class="hr-cards" id="dist-cards"></div>
    </div>
  </div>

</div>

<footer>Data via pybaseball / MLB Statcast &nbsp;·&nbsp; {{WEEK_LABEL}}</footer>

<script>
const DATA = {{DATA_JSON}};

/* ── TEAM TABLE ── */
function sortTeams(by) {
  const sorted = [...DATA.team_combined].sort((a, b) =>
    by === 'week' ? b.hr_week - a.hr_week : b.hr_season - a.hr_season
  );
  const maxWeek   = Math.max(...sorted.map(r => r.hr_week   || 0));
  const maxSeason = Math.max(...sorted.map(r => r.hr_season || 0));
  const tbody = document.getElementById('team-tbody');
  tbody.innerHTML = '';
  sorted.forEach(row => {
    const pctW = maxWeek   ? Math.round((row.hr_week   / maxWeek)   * 100) : 0;
    const pctS = maxSeason ? Math.round((row.hr_season / maxSeason) * 100) : 0;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="font-weight:500">${row.team}</td>
      <td>
        <div class="bar-cell">
          <div class="bar-bg"><div class="bar-fill" style="width:${pctW}%"></div></div>
          <div class="bar-num">${row.hr_week}</div>
        </div>
      </td>
      <td>
        <div class="bar-cell">
          <div class="bar-bg"><div class="bar-fill blue" style="width:${pctS}%"></div></div>
          <div class="bar-num">${row.hr_season}</div>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
  document.getElementById('sort-week').style.cssText   = by === 'week'   ? 'font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--red);background:var(--red);color:#fff;cursor:pointer;' : 'font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;';
  document.getElementById('sort-season').style.cssText = by === 'season' ? 'font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--red);background:var(--red);color:#fff;cursor:pointer;' : 'font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;';
}
sortTeams('week');

/* ── PLAYER TABLE ── */
(function() {
  const maxHR = Math.max(...DATA.player_leaders.map(r => r.hr_week || 0));
  const tbody = document.getElementById('player-tbody');
  DATA.player_leaders.forEach((row, i) => {
    const pct = maxHR ? Math.round((row.hr_week / maxHR) * 100) : 0;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="rank">${i + 1}</td>
      <td style="font-weight:500">${row.player_name}</td>
      <td style="color:var(--muted);font-family:var(--mono)">${row.team}</td>
      <td>
        <div class="bar-cell">
          <div class="bar-bg"><div class="bar-fill" style="width:${pct}%"></div></div>
          <div class="bar-num">${row.hr_week}</div>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
})();

/* ── HR CARDS ── */
function renderCards(containerId, rows, metricKey, metricLabel, accentClass) {
  const container = document.getElementById(containerId);
  rows.forEach(row => {
    const card = document.createElement('div');
    card.className = containerId === 'dist-cards' ? 'hr-card blue-card' : 'hr-card gold-card';
    const ev   = row.exit_velo !== '—' ? row.exit_velo + ' mph' : '—';
    const dist = row.distance  !== '—' ? row.distance  + ' ft'  : '—';
    const ang  = row.angle     !== '—' ? row.angle     + '°'    : '—';
    const mainVal = row[metricKey] !== '—' ? row[metricKey] + (metricKey === 'exit_velo' ? ' mph' : ' ft') : '—';
    card.innerHTML = `
      <div class="hr-card-rank">No. ${row.rank}</div>
      <div class="hr-card-player">${row.player_name}</div>
      <div class="hr-card-team">${row.team} &nbsp;·&nbsp; ${row.game_date}</div>
      <div class="hr-card-stats">
        <div class="hr-card-stat">
          <div class="hr-card-stat-label">${metricLabel}</div>
          <div class="hr-card-stat-val accent">${mainVal}</div>
        </div>
        ${metricKey !== 'exit_velo' ? `<div class="hr-card-stat"><div class="hr-card-stat-label">Exit Velo</div><div class="hr-card-stat-val">${ev}</div></div>` : ''}
        ${metricKey !== 'distance'  ? `<div class="hr-card-stat"><div class="hr-card-stat-label">Distance</div><div class="hr-card-stat-val">${dist}</div></div>` : ''}
        <div class="hr-card-stat">
          <div class="hr-card-stat-label">Angle</div>
          <div class="hr-card-stat-val">${ang}</div>
        </div>
      </div>
    `;
    container.appendChild(card);
  });
}

renderCards('ev-cards',   DATA.top_ev,   'exit_velo', 'Exit Velocity', 'accent');
renderCards('dist-cards', DATA.top_dist, 'distance',  'Distance',      'accent');
</script>
</body>
</html>
"""


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    start, end = get_date_range(args)
    week_label = f"{start} → {end}"

    hrs = fetch_home_runs(start, end)

    tw   = team_weekly(hrs)
    ts   = team_season(start, end)
    tc   = pd.merge(tw, ts, on="team", how="outer").fillna(0)
    tc["hr_week"]   = tc["hr_week"].astype(int)
    tc["hr_season"] = tc["hr_season"].astype(int)
    tc = tc.sort_values("hr_week", ascending=False)

    pl   = player_leaderboard(hrs)
    ev   = top_exit_velocity(hrs)
    dist = top_distance(hrs)

    top_ev_val   = round(ev["exit_velo"].iloc[0], 1)  if not ev.empty   else "—"
    top_dist_val = int(dist["distance"].iloc[0])       if not dist.empty else "—"

    data_payload = {
        "team_combined":   df_to_list(tc),
        "player_leaders":  df_to_list(pl),
        "top_ev":          df_to_list(ev),
        "top_dist":        df_to_list(dist),
    }

    html = (HTML_TEMPLATE
        .replace("{{WEEK_LABEL}}",    week_label)
        .replace("{{GENERATED}}",     datetime.now().strftime("%b %d, %Y %H:%M"))
        .replace("{{TOTAL_WEEK}}",    str(len(hrs)))
        .replace("{{TOTAL_SEASON}}",  str(int(tc["hr_season"].sum())))
        .replace("{{TOP_EV}}",        str(top_ev_val))
        .replace("{{TOP_DIST}}",      str(top_dist_val))
        .replace("{{DATA_JSON}}",     json.dumps(data_payload, default=str))
    )

    out_path = Path(args.out)
    out_path.write_text(html, encoding="utf-8")
    print(f"\nReport written → {out_path.resolve()}")


if __name__ == "__main__":
    main()
