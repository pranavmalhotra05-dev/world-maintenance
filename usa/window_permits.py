"""Window jobs per house, read from the city building permits already joined to each house (h.permits, usa/city_enrich.py).

    .venv/Scripts/python -u -W ignore -m usa.window_permits [slug ...]

No government level keeps a count of windows per house. What exists is the permit a city issues when windows are
replaced, added or enlarged (egress). This script only reads those records - nothing is guessed:
  * a permit counts when its type / work / description names windows, skylights, egress or patio/sliding doors;
  * 'count' is the number written in the description ("replace 14 windows", "two egress windows"), else null;
  * a permit whose CATEGORY merely lists windows (Columbus "Roof, Siding, Windows, Doors") is kept as
    kind 'category lists windows' - it may have been roof or siding work only;
  * voided / expired / withdrawn permits are kept and flagged (work may not have happened).
Writes h.window_permits (newest first) and h.windows {permits, stated_windows, last_date, note} for houses with any,
and meta.window_permits {generated, blocks counts, method}. Permit licences are those of the city adapter (green only).
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
WORDS = {"one": 1, "a": 1, "an": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}
WIN = re.compile(r"\b(windows?|skylights?|egress|glazing|fenestration|sliding (?:glass )?doors?|patio doors?)\b", re.I)
CATEGORY = re.compile(r"roof,?\s*siding,?\s*windows,?\s*doors", re.I)
NUM = re.compile(r"\b(\d{1,3}|" + "|".join(WORDS) + r")\s+(?:new\s+|existing\s+|basement\s+|egress\s+|replacement\s+|vinyl\s+|"
                 r"wood\s+|double[- ]hung\s+|casement\s+|egress-compliant\s+)*(windows?|skylights?)\b", re.I)
DEAD = re.compile(r"void|expired|withdrawn|cancel", re.I)


def classify(p):
    desc = str(p.get("comments") or "")
    full = " ".join(str(p.get(k) or "") for k in ("type", "work", "comments"))
    if not WIN.search(full):
        return None
    in_desc = bool(WIN.search(desc))
    if not in_desc and CATEGORY.search(full):
        kind = "category lists windows (may be roof or siding work only)"
    elif re.search(r"skylight", desc, re.I):
        kind = "skylight"
    elif re.search(r"egress", full, re.I):
        kind = "egress window (new or enlarged opening)"
    elif re.search(r"\b(add|new|install|enlarg|larger|smaller|cut)\w*\b.*\bwindows?\b|\bwindows?\b.*\b(added|new opening)", desc, re.I) \
            and not re.search(r"replac", desc, re.I):
        kind = "window added / opening changed"
    elif re.search(r"sliding|patio door", desc, re.I) and not re.search(r"windows?", desc, re.I):
        kind = "glass door"
    else:
        kind = "window replacement"
    count = 0
    for n, _ in NUM.findall(desc):
        v = int(n) if n.isdigit() else WORDS.get(n.lower(), 0)
        if 0 < v < 200:
            count += v
    if kind.startswith("egress") and not count and re.search(r"\begress window\b", desc, re.I) and not re.search(r"windows", desc, re.I):
        count = 1
    same = bool(re.search(r"same size|size for size|existing opening|no change[sd]? to (?:the )?(?:rough )?opening", desc, re.I))
    status = str(p.get("status") or "")
    return {"date": p.get("date"), "kind": kind, "count": count or None, "same_openings": same,
            "status": status or None, "maybe_not_done": bool(DEAD.search(status + " " + desc)),
            "permit_type": p.get("type"), "work": p.get("work"), "text": desc[:300]}


def run(slug):
    d = BLOCKS / slug
    hj = json.loads((d / "houses.json").read_text(encoding="utf-8"))
    hs = hj["houses"] if isinstance(hj, dict) else hj
    n_h = n_p = n_w = 0
    for h in hs:
        h.pop("window_permits", None); h.pop("windows", None)
        ws = [w for w in (classify(p) for p in (h.get("permits") or [])) if w]
        if not ws:
            continue
        # one job is often filed as building + electrical + mechanical + plumbing permits with the same text: keep one
        by = {}
        for w in sorted(ws, key=lambda w: (0 if re.search(r"build", str(w["permit_type"] or ""), re.I) else 1, str(w["date"] or ""))):
            key = (re.sub(r"\W+", " ", w["text"].lower()).strip()
                   if len(w["text"]) > 25 and not w["kind"].startswith("category") else id(w))
            if key in by:
                by[key].setdefault("also_filed_as", []).append(f"{w['permit_type']} {w['date']}")
            else:
                by[key] = w
        ws = list(by.values())
        ws.sort(key=lambda w: str(w["date"] or ""), reverse=True)
        real = [w for w in ws if not w["kind"].startswith("category")]
        stated = sum(w["count"] or 0 for w in real if not w["maybe_not_done"])
        h["window_permits"] = ws
        h["windows"] = {"permits": len(ws), "window_permits": len(real), "stated_windows": stated or None,
                        "last_date": (real or ws)[0]["date"],
                        "note": "from city building permits only; a house's total number of windows is not recorded anywhere open"}
        n_h += 1; n_p += len(ws); n_w += stated
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta["window_permits"] = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "houses": n_h, "permits": n_p,
                              "stated_windows": n_w, "method": "usa/window_permits.py: window words in permit type/work/description; "
                              "counts only where written; voided/expired kept and flagged"}
    with block_lock(d):
        (d / "houses.json").write_text(json.dumps(hj, separators=(",", ":")), encoding="utf-8")
        (d / "meta.json").write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    return meta["window_permits"]


def main():
    slugs = sys.argv[1:] or sorted(p.name for p in BLOCKS.iterdir() if (p / "houses.json").exists() and p.name != "slc-ut-test")
    for s in slugs:
        try:
            m = run(s)
        except Exception as e:  # noqa: BLE001
            print(f"{s}: FAILED {e!r}"); continue
        if m["permits"]:
            print(f"{s}: {m['houses']} houses, {m['permits']} window permits, {m['stated_windows']} windows written in descriptions")


if __name__ == "__main__":
    main()
