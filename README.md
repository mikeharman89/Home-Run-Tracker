# [MLB Home Run Tracker](https://mikeharman89.github.io/Home-Run-Tracker/)

A weekly MLB home run report auto-generated daily via GitHub Actions and published to GitHub Pages.

**Live report →** `https://mikeharman89.github.io/Home-Run-Tracker/`

## What it tracks
- Home runs by team (last 7 days + season running total)
- Top 10 player leaderboard for the last 7 days
- Top 10 HRs by exit velocity
- Top 10 HRs by distance

Data via [pybaseball](https://github.com/jldbc/pybaseball) / MLB Statcast.

## Repo structure
```
├── hr_tracker.py           # Main script
├── requirements.txt        # Python deps
├── index.html              # Latest generated report (auto-updated)
└── .github/
    └── workflows/
        └── update_hr_tracker.yml   # Daily schedule
```

## Running locally
```bash
pip install -r requirements.txt

python hr_tracker.py                        # Last 7 days
python hr_tracker.py --season 2026          # Full 2026 season to date
python hr_tracker.py --start 2026-04-01 --end 2026-04-10
```

## Schedule
Runs daily at **1:00 PM ET** via GitHub Actions cron. You can also trigger a manual run from the Actions tab.
