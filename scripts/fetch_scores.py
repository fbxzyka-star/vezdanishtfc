#!/usr/bin/env python3
"""Fetch FC Vezhdanisht Rumst matches and league standings from Voetbal Vlaanderen.

Writes data/matches.json, which index.html reads to render results,
the next match countdown and the standings table.

Uses the same GraphQL queries the voetbalvlaanderen.be website itself sends.
This is not an official public API, so it may change without notice.
Standard library only, no dependencies.
"""
import json
import pathlib
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ENDPOINT = "https://www.voetbalvlaanderen.be/graphql"
CLUB_ID = "10000"
TEAM_ID = "369334"  # Eerste Elftal (Futsal)
LANGUAGE = "nl"
TZ = ZoneInfo("Europe/Brussels")
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "matches.json"

TEAM_CALENDAR = """
query GetTeamCalendar ($teamId: ID!, $language: Language!, $sortByDate: SortDirection) {
  teamCalendar(teamId: $teamId, language: $language, sortByDate: $sortByDate) {
    id
    startTime
    homeTeam { id name clubId logo }
    awayTeam { id name clubId logo }
    outcome { status homeTeamGoals homeTeamPenaltiesScored awayTeamGoals awayTeamPenaltiesScored subscript }
    series { id name }
    showScore
    state
  }
}
"""

SERIES_RANKINGS = """
query GetSeriesRankings ($seriesId: ID!, $language: Language!) {
  seriesRankings(seriesId: $seriesId, language: $language) {
    id
    name
    rankings {
      type
      teams {
        teamId name position clubId logo points matchesPlayed
        matchesWon matchesLost matchesDrawn goalsFor goalsAgainst goalDifference
      }
    }
  }
}
"""


SERIES_CALENDAR = """
query GetSeriesCalendar($seriesId: ID!, $endDate: String!, $startDate: String!, $language: Language!) {
  seriesCalendar(seriesId: $seriesId, startDate: $startDate, endDate: $endDate, language: $language) {
    id
    state
    startTime
    homeTeam { id name clubId logo }
    awayTeam { id name clubId logo }
    outcome { status homeTeamGoals awayTeamGoals }
  }
}
"""

LEAGUE_RESULTS = 12  # how many recent league results (all teams) to keep for the "Latest results" strip


def gql(operation, query, variables):
    body = json.dumps({"operationName": operation, "query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        ENDPOINT, body, {"content-type": "application/json", "user-agent": "vezdanishtfc-site-updater"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise RuntimeError(f"{operation}: {payload['errors']}")
    return payload["data"]


def team(t):
    return {"id": t["id"], "name": t["name"], "clubId": t.get("clubId"), "logo": t.get("logo")}


def match(m):
    local = datetime.fromisoformat(m["startTime"]).replace(tzinfo=TZ)
    o = m.get("outcome") or {}
    is_home = m["homeTeam"]["clubId"] == CLUB_ID
    played = m.get("state") == "finished" and o.get("homeTeamGoals") is not None
    result = None
    if played:
        ours, theirs = (o["homeTeamGoals"], o["awayTeamGoals"]) if is_home else (o["awayTeamGoals"], o["homeTeamGoals"])
        result = "W" if ours > theirs else "L" if ours < theirs else "D"
    return {
        "id": m["id"],
        "start": local.isoformat(),
        "state": m.get("state"),
        "series": m["series"]["name"],
        "home": team(m["homeTeam"]),
        "away": team(m["awayTeam"]),
        "homeGoals": o.get("homeTeamGoals") if played else None,
        "awayGoals": o.get("awayTeamGoals") if played else None,
        "status": o.get("status"),
        "isHome": is_home,
        "result": result,
    }


def main():
    raw = gql("GetTeamCalendar", TEAM_CALENDAR,
              {"teamId": TEAM_ID, "language": LANGUAGE, "sortByDate": "asc"})["teamCalendar"] or []
    if not raw:
        sys.exit("No matches returned; keeping the existing data file.")
    matches = [match(m) for m in raw]

    # The league is the series most of the team's matches belong to (cup games are the exception).
    series_ids = [m["series"]["id"] for m in raw]
    series_id = max(set(series_ids), key=series_ids.count)

    rk = gql("GetSeriesRankings", SERIES_RANKINGS, {"seriesId": series_id, "language": LANGUAGE})["seriesRankings"]
    general = next((r for r in rk["rankings"] if r["type"] == "generalRanking"), rk["rankings"][0] if rk["rankings"] else None)
    standings = sorted(general["teams"], key=lambda t: t["position"]) if general else []

    # Latest results of every team in the league (newest first), incl. live and forfeited games.
    today = datetime.now(TZ).date()
    season_start = today.year if today.month >= 7 else today.year - 1
    cal = gql("GetSeriesCalendar", SERIES_CALENDAR, {
        "seriesId": series_id, "language": LANGUAGE,
        "startDate": f"{season_start}-07-01", "endDate": f"{season_start + 1}-06-30",
    })["seriesCalendar"] or []
    shown = [m for m in cal if m.get("state") in ("finished", "started", "forfeit")]
    shown.sort(key=lambda m: m["startTime"], reverse=True)
    league_results = []
    for m in shown[:LEAGUE_RESULTS]:
        o = m.get("outcome") or {}
        league_results.append({
            "id": m["id"],
            "start": datetime.fromisoformat(m["startTime"]).replace(tzinfo=TZ).isoformat(),
            "state": m["state"],
            "home": team(m["homeTeam"]),
            "away": team(m["awayTeam"]),
            "homeGoals": o.get("homeTeamGoals"),
            "awayGoals": o.get("awayTeamGoals"),
        })

    data = {
        "club": {"id": CLUB_ID, "teamId": TEAM_ID, "name": "FC VEZHDANISHT RUMST"},
        "series": {"id": series_id, "name": rk["name"]},
        "matches": matches,
        "standings": standings,
        "leagueResults": league_results,
        "leagueCalendarUrl": f"https://www.voetbalvlaanderen.be/competitie/{series_id}/kalender",
        "source": f"https://www.voetbalvlaanderen.be/club/{CLUB_ID}/komende-wedstrijden",
    }

    # Only bump updatedAt when the actual data changed, so the workflow does not commit every run.
    if OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8"))
        old.pop("updatedAt", None)
        if old == data:
            print("No changes.")
            return
    data["updatedAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(matches)} matches, {len(standings)} teams in standings).")


if __name__ == "__main__":
    main()
