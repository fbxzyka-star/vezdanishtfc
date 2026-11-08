#!/usr/bin/env python3
"""Fetch the club's goal scorers from the shared OneDrive Excel file.

Writes data/scorers.json, which index.html reads for the Top Scorers section.

Expected sheet layout (first sheet): player name in column A and the number of
goals in the last filled numeric cell of the same row (currently column D).
Rows without a number (e.g. the "Goalscorers:" title) are ignored.

Standard library only (an .xlsx file is a zip of XML files), no dependencies.
"""
import io
import json
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

# Direct download for the shared link https://1drv.ms/x/c/99dcc8466d66e949/IQAI-Jp6DK3gR4YU6fSa9tjjARDoqJQPkoECYZVJgpCK1Jg
SHARE_ID = "IQAI-Jp6DK3gR4YU6fSa9tjjARDoqJQPkoECYZVJgpCK1Jg"
DOWNLOAD_URL = (
    "https://my.microsoftpersonalcontent.com/personal/99dcc8466d66e949"
    f"/_layouts/15/download.aspx?share={SHARE_ID}"
)
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "scorers.json"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def download():
    req = urllib.request.Request(DOWNLOAD_URL, headers={"user-agent": "vezdanishtfc-site-updater"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    if not data.startswith(b"PK"):
        raise RuntimeError("Download did not return an Excel file (is the link still shared?)")
    return data


def col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_first_sheet(xlsx_bytes):
    z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    # first sheet in workbook order
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    first = wb.find("m:sheets/m:sheet", NS)
    rid = first.get(REL_NS)
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = next(r.get("Target") for r in rels if r.get("Id") == rid)
    path = target.lstrip("/") if target.startswith("/") else "xl/" + target
    sheet = ET.fromstring(z.read(path))
    rows = []
    for row in sheet.iter(f"{{{NS['m']}}}row"):
        cells = {}
        for c in row.findall("m:c", NS):
            v = c.find("m:v", NS)
            t = c.get("t")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            elif v is None:
                continue
            elif t == "s":
                val = shared[int(v.text)]
            elif t in ("str", "b", "e"):
                val = v.text
            else:
                try:
                    f = float(v.text)
                    val = int(f) if f.is_integer() else f
                except (TypeError, ValueError):
                    val = v.text
            cells[col_index(c.get("r"))] = val
        rows.append(cells)
    return rows


def parse_scorers(rows):
    scorers = []
    for cells in rows:
        name = cells.get(0)
        if not isinstance(name, str) or not name.strip():
            continue
        nums = [v for k, v in sorted(cells.items()) if k > 0 and isinstance(v, (int, float))]
        if not nums:
            continue  # title rows such as "Goalscorers:"
        goals = int(nums[-1])
        scorers.append({"name": re.sub(r"\s+", " ", name).strip(), "goals": goals})
    scorers.sort(key=lambda s: (-s["goals"], s["name"]))
    return scorers


def main():
    scorers = parse_scorers(read_first_sheet(download()))
    if not scorers:
        sys.exit("No scorers found in the Excel file; keeping the existing data file.")
    data = {"scorers": scorers, "totalGoals": sum(s["goals"] for s in scorers)}
    if OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8"))
        old.pop("updatedAt", None)
        if old == data:
            print("No changes.")
            return
    data["updatedAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(scorers)} scorers, {data['totalGoals']} goals).")


if __name__ == "__main__":
    main()
