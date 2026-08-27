# lol-trmnl

Fetches League of Legends Solo/Duo stats (region **EUW**) via the Riot API
and writes the result to `data.json`. A GitHub Actions workflow runs the
fetch every 30 minutes and commits `data.json` back into the repo.

## Setup

1. Add a repository **secret** `RIOT_API_KEY` with your Riot API key.
2. Add a repository **variable** `RIOT_ID` with your Riot ID, e.g. `Name#TAG`.

## `data.json` contents

- `soloDuo`: current Ranked Solo/Duo tier, rank, LP, wins/losses, winrate
- `recentMatches`: last 20 Solo/Duo matches with KDA, CS/min, win/loss
- `topChampions`: winrate for the 3 most-played champions among those matches

## Run locally

```bash
pip install -r requirements.txt  # none needed, stdlib only
RIOT_API_KEY=xxx RIOT_ID="Name#TAG" python scripts/fetch.py
```
