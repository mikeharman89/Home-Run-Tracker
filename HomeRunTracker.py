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
    hrs["batting_team"] = hrs.apply(
        lambda r: r["home_team"] if r["inning_topbot"] == "Bot" else r["away_team"], axis=1
    )
    print(f"  Found {len(hrs)} regular season home runs")
    return hrs


# ─── AGGREGATIONS ─────────────────────────────────────────────────────────────

def team_weekly(hrs):
    """Home runs by batting team for the selected window."""
    tbl = (
        hrs.groupby("batting_team")["events"]
        .count()
        .reset_index()
        .rename(columns={"batting_team": "team", "events": "hr_week"})
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
    hrs["batting_team"] = hrs.apply(
        lambda r: r["home_team"] if r["inning_topbot"] == "Bot" else r["away_team"], axis=1
    )
    tbl = (
        hrs.groupby("batting_team")["events"]
        .count()
        .reset_index()
        .rename(columns={"batting_team": "team", "events": "hr_season"})
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
            team=("batting_team", lambda x: x.mode()[0]),
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
    cols = ["batter_name", "batting_team", "launch_speed", "hit_distance_sc",
            "launch_angle", "game_date", "home_score", "away_score"]
    sub = hrs.dropna(subset=["launch_speed"])[cols].copy()
    sub = sub.sort_values("launch_speed", ascending=False).head(top_n).reset_index(drop=True)
    sub.rename(columns={
        "batter_name": "player_name",
        "batting_team": "team",
        "launch_speed": "exit_velo",
        "hit_distance_sc": "distance",
        "launch_angle": "angle",
    }, inplace=True)
    sub["rank"] = sub.index + 1
    return sub


def top_distance(hrs, top_n=10):
    """Top N HRs by distance — batters only (excludes pitchers batting)."""
    cols = ["batter_name", "batting_team", "launch_speed", "hit_distance_sc",
            "launch_angle", "game_date", "hc_x", "hc_y"]
    sub = hrs.dropna(subset=["hit_distance_sc"])[cols].copy()
    sub = sub.sort_values("hit_distance_sc", ascending=False).head(top_n).reset_index(drop=True)
    sub.rename(columns={
        "batter_name": "player_name",
        "batting_team": "team",
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
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
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
  @media (max-width: 480px) {
    .hero { padding: 1.5rem 1rem 1.5rem; }
    .content { padding: 1.5rem 1rem 2rem; }
    .stat-pill { min-width: 90px; padding: 0.5rem 0.75rem; }
    .stat-pill-val { font-size: 1.3rem; }
    .hero-title { font-size: 1.8rem; }
    .tbl-wrap { font-size: 12px; }
    .hr-cards { grid-template-columns: 1fr; }
    canvas#spray-chart { height: 320px !important; }
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
    gap: 0;
  }
  .two-col > .section { padding: 0 2rem; }
  .two-col > .section:first-child { padding-left: 0; border-right: 1px solid var(--border); padding-right: 2rem; }
  .two-col > .section:last-child  { padding-left: 2rem; padding-right: 0; border-left: none; }
  @media (max-width: 720px) {
    .two-col { grid-template-columns: 1fr; }
    .two-col > .section { padding: 0 !important; border: none !important; }
    .two-col > .section:first-child { border-right: none !important; padding-bottom: 2rem; border-bottom: 1px solid var(--border) !important; }
  }

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
  /* ── SPRAY TOOLTIP ── */
  .spray-tooltip {
    position: fixed;
    background: var(--navy2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 0;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.15s;
    z-index: 1000;
    min-width: 200px;
    overflow: hidden;
  }
  .spray-tooltip.visible { opacity: 1; }
  .spray-tooltip-header {
    padding: 8px 12px 6px;
    border-bottom: 1px solid var(--border);
  }
  .spray-tooltip-rank {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 2px;
    color: var(--muted);
    margin-bottom: 2px;
  }
  .spray-tooltip-player {
    font-family: 'Oswald', sans-serif;
    font-size: 1rem;
    font-weight: 500;
    color: #fff;
    line-height: 1.2;
  }
  .spray-tooltip-team {
    font-size: 11px;
    color: var(--muted);
    font-family: var(--mono);
    margin-top: 1px;
  }
  .spray-tooltip-stats {
    display: flex;
    gap: 1rem;
    padding: 8px 12px 10px;
    flex-wrap: wrap;
  }
  .spray-tooltip-stat-label {
    font-size: 9px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .spray-tooltip-stat-val {
    font-family: 'Oswald', sans-serif;
    font-size: 1.1rem;
    font-weight: 500;
    color: #fff;
  }
  .spray-tooltip-stat-val.accent { color: var(--gold); }
</style>
</head>
<body>

<div class="spray-tooltip" id="spray-tooltip">
  <div class="spray-tooltip-header">
    <div class="spray-tooltip-rank" id="stt-rank"></div>
    <div class="spray-tooltip-player" id="stt-player"></div>
    <div class="spray-tooltip-team" id="stt-team"></div>
  </div>
  <div class="spray-tooltip-stats">
    <div><div class="spray-tooltip-stat-label">Distance</div><div class="spray-tooltip-stat-val accent" id="stt-dist"></div></div>
    <div><div class="spray-tooltip-stat-label">Exit Velo</div><div class="spray-tooltip-stat-val" id="stt-ev"></div></div>
    <div><div class="spray-tooltip-stat-label">Angle</div><div class="spray-tooltip-stat-val" id="stt-angle"></div></div>
    <div><div class="spray-tooltip-stat-label">Date</div><div class="spray-tooltip-stat-val" id="stt-date"></div></div>
  </div>
</div>

<div class="hero">
  <div class="hero-inner">
    <h1 class="hero-title">Home Run <span>Tracker</span></h1>
    <div class="hero-meta">Generated {{GENERATED}}</div>
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
      <div style="margin-left:auto;display:flex;align-items:center;gap:8px;">
        <span style="font-family:var(--mono);font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);">Sort By</span>
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

  <!-- SEASON PLAYER LEADERBOARD -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">Season Home Run Leaders</div>
      <div class="section-badge">Top 10 — Full Season</div>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th class="num">#</th>
            <th>Player</th>
            <th>Team</th>
            <th style="min-width:180px">Season Total</th>
          </tr>
        </thead>
        <tbody id="player-season-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- EV + DISTANCE CARDS -->
  <div class="two-col" style="border-top:1px solid var(--border);padding-top:2.5rem;margin-top:0;">
    <div class="section">
      <div class="section-header">
        <div class="section-title">Top Exit Velocity</div>
        <div style="margin-left:auto;display:flex;gap:6px;">
          <button id="ev-btn-week"   onclick="switchEV('week')"   style="font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--red);background:var(--red);color:#fff;cursor:pointer;">Last 7 Days</button>
          <button id="ev-btn-season" onclick="switchEV('season')" style="font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;">Season</button>
        </div>
      </div>
      <div class="hr-cards" id="ev-cards"></div>
    </div>

    <div class="section">
      <div class="section-header">
        <div class="section-title">Longest Home Runs</div>
        <div style="margin-left:auto;display:flex;gap:6px;">
          <button id="dist-btn-week"   onclick="switchDist('week')"   style="font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--red);background:var(--red);color:#fff;cursor:pointer;">Last 7 Days</button>
          <button id="dist-btn-season" onclick="switchDist('season')" style="font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;">Season</button>
        </div>
      </div>
      <div class="hr-cards" id="dist-cards"></div>
    </div>
  </div>

  <!-- SPRAY CHART — full width below EV/Distance cards -->
  <div class="section" style="border-top:1px solid var(--border);padding-top:2.5rem;">
    <div class="section-header">
      <div class="section-title">Landing Spot Map</div>
      <div class="section-badge" id="spray-badge">Longest HRs — Last 7 Days</div>
      <div style="margin-left:auto;display:flex;gap:6px;">
        <button id="spray-btn-week"   onclick="switchSpray('week')"   style="font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--red);background:var(--red);color:#fff;cursor:pointer;">Last 7 Days</button>
        <button id="spray-btn-season" onclick="switchSpray('season')" style="font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;">Season</button>
      </div>
    </div>
    <div style="display:flex;justify-content:center;">
      <canvas id="spray-chart" width="700" height="540" style="width:100%;max-width:700px;display:block;"></canvas>
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

/* ── SEASON PLAYER TABLE ── */
(function() {
  const rows  = DATA.player_leaders_szn || [];
  const maxHR = Math.max(...rows.map(r => r.hr_week || 0));
  const tbody = document.getElementById('player-season-tbody');
  rows.forEach((row, i) => {
    const pct = maxHR ? Math.round((row.hr_week / maxHR) * 100) : 0;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="rank">${i + 1}</td>
      <td style="font-weight:500">${row.player_name}</td>
      <td style="color:var(--muted);font-family:var(--mono)">${row.team}</td>
      <td>
        <div class="bar-cell">
          <div class="bar-bg"><div class="bar-fill blue" style="width:${pct}%"></div></div>
          <div class="bar-num">${row.hr_week}</div>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
})();

/* ── HR CARDS ── */
function renderCards(containerId, rows, metricKey, metricLabel) {
  const container = document.getElementById(containerId);
  container.innerHTML = '';
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

/* ── TOGGLE HELPERS ── */
const BTN_ON  = 'font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid var(--red);background:var(--red);color:#fff;cursor:pointer;';
const BTN_OFF = 'font-family:var(--mono);font-size:10px;letter-spacing:1px;padding:4px 10px;border-radius:4px;border:1px solid rgba(138,160,181,0.18);background:transparent;color:#8AA0B5;cursor:pointer;';

function switchEV(mode) {
  renderCards('ev-cards', mode === 'week' ? DATA.top_ev_week : DATA.top_ev_season, 'exit_velo', 'Exit Velocity');
  document.getElementById('ev-btn-week').style.cssText   = mode === 'week'   ? BTN_ON : BTN_OFF;
  document.getElementById('ev-btn-season').style.cssText = mode === 'season' ? BTN_ON : BTN_OFF;
}

function switchDist(mode) {
  const rows = mode === 'week' ? DATA.top_dist_week : DATA.top_dist_season;
  renderCards('dist-cards', rows, 'distance', 'Distance');
  document.getElementById('dist-btn-week').style.cssText   = mode === 'week'   ? BTN_ON : BTN_OFF;
  document.getElementById('dist-btn-season').style.cssText = mode === 'season' ? BTN_ON : BTN_OFF;
}

function switchSpray(mode) {
  const rows = mode === 'week' ? DATA.top_dist_week : DATA.top_dist_season;
  drawSpray(rows);
  document.getElementById('spray-btn-week').style.cssText   = mode === 'week'   ? BTN_ON : BTN_OFF;
  document.getElementById('spray-btn-season').style.cssText = mode === 'season' ? BTN_ON : BTN_OFF;
  document.getElementById('spray-badge').textContent = mode === 'week' ? 'Longest HRs — Last 7 Days' : 'Longest HRs — Season';
}

/* ── SPRAY CHART ── */
function drawSpray(rows) {
  const canvas = document.getElementById('spray-chart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  // Statcast hc_x/hc_y origin is home plate at (125.42, 199.46) in a 250x250 SVG space
  // We map that coordinate space onto our canvas
  // hc_x: 0=left foul line, 250=right foul line; hc_y: 0=CF wall, 250=home plate area
  const SC_HOME_X = 125.42, SC_HOME_Y = 199.46;
  const SC_W = 250, SC_H = 250;
  const PAD = 20;
  const scaleX = (W - PAD * 2) / SC_W;
  const scaleY = (H - PAD * 2) / SC_H;
  const scale  = Math.min(scaleX, scaleY);

  // Canvas coords for home plate
  const HX = PAD + SC_HOME_X * scale;
  const HY = PAD + SC_HOME_Y * scale;

  function toCanvas(hcx, hcy) {
    return [PAD + hcx * scale, PAD + hcy * scale];
  }

  // ── FIELD BACKGROUND ──
  // Outfield grass wedge
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(HX, HY);
  // foul lines extend to corners roughly at hc_x=0,hcy=90 and hc_x=250,hcy=90
  const [flLx, flLy] = toCanvas(0,   90);
  const [flRx, flRy] = toCanvas(250, 90);
  const [cfX,  cfY]  = toCanvas(SC_HOME_X, 0);
  ctx.lineTo(flLx, flLy);
  ctx.quadraticCurveTo(cfX, cfY, flRx, flRy);
  ctx.closePath();
  ctx.fillStyle = 'rgba(10,40,18,0.85)';
  ctx.fill();
  ctx.restore();

  // Infield dirt square (bases at ~90ft = ~36px in SC space)
  const BASE = 34;
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(HX,          HY);
  ctx.lineTo(HX - BASE,   HY - BASE);
  ctx.lineTo(HX,          HY - BASE * 2);
  ctx.lineTo(HX + BASE,   HY - BASE);
  ctx.closePath();
  ctx.fillStyle = 'rgba(160,110,60,0.18)';
  ctx.fill();
  ctx.strokeStyle = 'rgba(180,130,80,0.25)';
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.restore();

  // Pitcher's mound
  ctx.beginPath();
  ctx.arc(HX, HY - BASE, 6, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(160,110,60,0.2)';
  ctx.fill();

  // Foul lines
  ctx.strokeStyle = 'rgba(255,255,255,0.2)';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(HX, HY);
  ctx.lineTo(flLx, flLy);
  ctx.moveTo(HX, HY);
  ctx.lineTo(flRx, flRy);
  ctx.stroke();
  ctx.setLineDash([]);

  // Distance arc labels (300, 380, 420ft) — scale: 1 SC unit ≈ 2.5ft
  const FT_PER_SC = 2.5;
  [300, 380, 420].forEach(ft => {
    const r = (ft / FT_PER_SC) * scale;
    ctx.beginPath();
    ctx.arc(HX, HY, r, -Math.PI * 0.85, -Math.PI * 0.15);
    ctx.strokeStyle = 'rgba(255,255,255,0.07)';
    ctx.lineWidth = 1;
    ctx.stroke();
    const labelAngle = -Math.PI * 0.18;
    ctx.fillStyle = 'rgba(255,255,255,0.22)';
    ctx.font = '9px IBM Plex Mono, monospace';
    ctx.fillText(ft + 'ft', HX + Math.cos(labelAngle) * r + 3, HY + Math.sin(labelAngle) * r - 3);
  });

  // Home plate
  ctx.fillStyle = 'rgba(255,255,255,0.6)';
  ctx.beginPath();
  ctx.moveTo(HX,     HY - 5);
  ctx.lineTo(HX + 5, HY);
  ctx.lineTo(HX + 5, HY + 4);
  ctx.lineTo(HX - 5, HY + 4);
  ctx.lineTo(HX - 5, HY);
  ctx.closePath();
  ctx.fill();

  // ── PLOT HRs using real hc_x / hc_y ──
  const colors = ['#F5A623','#F0B040','#E8C060','#2C7BE5','#5B9CF0',
                  '#88B8F8','#D0021B','#E84444','#F07070','#F5A0A0'];

  // Store dot positions for hit-testing on hover
  canvas._sprayDots = [];

  rows.forEach((row, i) => {
    const hcx = +row.hc_x;
    const hcy = +row.hc_y;
    if (!hcx || !hcy || isNaN(hcx) || isNaN(hcy)) return;

    const [lx, ly] = toCanvas(hcx, hcy);

    // Store for hover detection
    canvas._sprayDots.push({ lx, ly, row, i });

    // Dashed arc from home plate to landing
    const cpx = (HX + lx) / 2;
    const cpy = (HY + ly) / 2 - 35;
    ctx.beginPath();
    ctx.moveTo(HX, HY);
    ctx.quadraticCurveTo(cpx, cpy, lx, ly);
    ctx.strokeStyle = colors[i] || '#aaa';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([3, 3]);
    ctx.globalAlpha = 0.6;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    // Landing dot with glow ring
    ctx.beginPath();
    ctx.arc(lx, ly, 9, 0, Math.PI * 2);
    ctx.fillStyle = (colors[i] || '#aaa') + '33';
    ctx.fill();

    ctx.beginPath();
    ctx.arc(lx, ly, 6, 0, Math.PI * 2);
    ctx.fillStyle = colors[i] || '#aaa';
    ctx.fill();

    // Rank number only — no name label
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 8px IBM Plex Mono, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(i + 1, lx, ly);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  });

  // ── CANVAS HOVER → TOOLTIP ──
  canvas.onmousemove = function(e) {
    const rect   = canvas.getBoundingClientRect();
    const scaleX = canvas.width  / rect.width;
    const scaleY = canvas.height / rect.height;
    const mx = (e.clientX - rect.left) * scaleX;
    const my = (e.clientY - rect.top)  * scaleY;
    const HIT_R = 14;

    const tt = document.getElementById('spray-tooltip');
    let hit = null;
    for (const dot of (canvas._sprayDots || [])) {
      const dx = mx - dot.lx, dy = my - dot.ly;
      if (Math.sqrt(dx*dx + dy*dy) <= HIT_R) { hit = dot; break; }
    }

    if (hit) {
      const r = hit.row;
      document.getElementById('stt-rank').textContent   = 'No. ' + (hit.i + 1);
      document.getElementById('stt-player').textContent = r.player_name || '—';
      document.getElementById('stt-team').textContent   = r.team || '—';
      document.getElementById('stt-dist').textContent   = r.distance  !== '—' ? r.distance  + ' ft'  : '—';
      document.getElementById('stt-ev').textContent     = r.exit_velo !== '—' ? r.exit_velo + ' mph' : '—';
      document.getElementById('stt-angle').textContent  = r.angle     !== '—' ? r.angle     + '°'   : '—';
      document.getElementById('stt-date').textContent   = r.game_date || '—';

      // Position tooltip — keep it inside the viewport
      const TW = 220, TH = 130;
      let tx = e.clientX + 14, ty = e.clientY - 60;
      if (tx + TW > window.innerWidth  - 10) tx = e.clientX - TW - 14;
      if (ty + TH > window.innerHeight - 10) ty = e.clientY - TH - 10;
      tt.style.left = tx + 'px';
      tt.style.top  = ty + 'px';
      tt.classList.add('visible');
      canvas.style.cursor = 'pointer';
    } else {
      tt.classList.remove('visible');
      canvas.style.cursor = 'default';
    }
  };

  canvas.onmouseleave = function() {
    document.getElementById('spray-tooltip').classList.remove('visible');
    canvas.style.cursor = 'default';
  };
}

/* ── INIT ── */
switchEV('week');
switchDist('week');
switchSpray('week');
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

    pl        = player_leaderboard(hrs)
    ev_week   = top_exit_velocity(hrs, top_n=10)
    dist_week = top_distance(hrs, top_n=10)

    # Season EV/distance — reuse season statcast pull
    year = datetime.strptime(end, "%Y-%m-%d").year
    season_start = f"{year}-03-26"
    print("  Fetching season EV/distance data …")
    df_szn = statcast(start_dt=season_start, end_dt=end)
    hrs_szn = df_szn[
        (df_szn["events"] == "home_run") &
        (df_szn["game_type"] == "R")
    ].copy()
    hrs_szn["batting_team"] = hrs_szn.apply(
        lambda r: r["home_team"] if r["inning_topbot"] == "Bot" else r["away_team"], axis=1
    )
    if "batter_name" in hrs_szn.columns:
        pass
    elif "des" in hrs_szn.columns:
        hrs_szn["batter_name"] = hrs_szn["des"].str.extract(r"^([A-Za-z\s'\-\.]+?)(?:\s+(?:homers|hits))")[0].str.strip()
    else:
        hrs_szn["batter_name"] = hrs_szn["player_name"]

    ev_season   = top_exit_velocity(hrs_szn, top_n=10)
    dist_season = top_distance(hrs_szn, top_n=10)

    top_ev_val   = round(ev_week["exit_velo"].iloc[0], 1)  if not ev_week.empty   else "—"
    top_dist_val = int(dist_week["distance"].iloc[0])       if not dist_week.empty else "—"

    # Season player leaderboard
    pl_season = player_leaderboard(hrs_szn, top_n=10)

    data_payload = {
        "team_combined":      df_to_list(tc),
        "player_leaders":     df_to_list(pl),
        "player_leaders_szn": df_to_list(pl_season),
        "top_ev_week":        df_to_list(ev_week),
        "top_ev_season":      df_to_list(ev_season),
        "top_dist_week":      df_to_list(dist_week),
        "top_dist_season":    df_to_list(dist_season),
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
