# 🏠 CROUS Île-de-France Housing Sniper Bot

A sophisticated bot that monitors **100%** of CROUS housing availability in Île-de-France, including listings hidden from public search.

## 🎯 Features

- **Dual-source monitoring**: Combines search API + direct ID checking
- **Hidden gem detection**: Finds listings not shown in search results
- **Smart deduplication**: No duplicate alerts
- **Single log file**: Easy debugging with automatic rotation
- **Discord notifications**: Real-time alerts with commute distances
- **GitHub Actions**: Fully automated, runs every 5 minutes

## 📁 Structure
crous-monitor/
├── main.py # V8 Hybrid bot (both sources)
├── update_master_list.py # Master list updater (runs every 12h)
├── idf_master_list.json # Auto-generated IDF residences
├── history.json # State tracking (active/ghosts)
├── crous_monitor.log # Single rotating log file
├── .github/workflows/
│ ├── sniper.yml # Runs every 5 min
│ └── cartographer.yml # Runs every 12h
└── README.md # This file

text

## 🚀 Setup

1. **Fork this repository**
2. **Add Discord Webhook Secret**:
   - Go to Repository Settings → Secrets and variables → Actions
   - Add new secret: `DISCORD_WEBHOOK` = Your Discord webhook URL

3. **Enable GitHub Actions**:
   - Actions are automatically enabled when you fork
   - First run will start within 5 minutes

## 🔔 Notification Types

| Type | Color | Meaning |
|------|-------|---------|
| ✨ NEW DROP | Green | New listing in search results |
| 🎯 HIDDEN GEM | Purple | Available but NOT in search results |
| 👻 GHOST WOKE UP | Orange | Previously unavailable, now available |
| 📋 CURRENT | Blue | All current availabilities (force listing) |

## ⚙️ Manual Controls

### Force List All Available
```yaml
In GitHub → Actions → CROUS Sniper → Run workflow → Check "Force list ALL available housing?"