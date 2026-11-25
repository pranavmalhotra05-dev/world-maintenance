"""Merge the verification lanes' patch files into web/usa/data/rules.json.

  python usa/merge_rules.py            (reads usa/research/rules_verify_*.json)

Each patch: {"cities": {key: {field: {value, source_url, verified, checked, evidence, changed}}},
             "states": {ST: {...}}, "platforms": {...}}
A patched field replaces value/source_url/verified/checked and gains "evidence"; a field marked verified=false in a
patch never downgrades one that is already verified=true. Prints before/after verification counts and every change.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "web" / "usa" / "data" / "rules.json"
rules = json.loads(RULES.read_text(encoding="utf-8"))


def fields_of(section, key):
    node = rules.get(section, {}).get(key)
    if node is None:
        return None
    return node.get("fields", node) if isinstance(node, dict) else None


def tally():
    c = {"true": 0, "partial": 0, "false": 0}
    for sec in ("cities", "states"):
        for node in rules.get(sec, {}).values():
            for f in node.get("fields", {}).values():
                v = f.get("verified")
                c["true" if v is True else "partial" if v == "partial" else "false"] += 1
    return c


before = tally()
changes, applied, skipped = [], 0, []
for p in sorted((ROOT / "usa" / "research").glob("rules_verify_*.json")):
    patch = json.loads(p.read_text(encoding="utf-8"))
    # platforms: rules.json has [{name, items:[...]}]; patches send {name: [{index, value, ...}]}
    plat = {p_["name"]: p_ for p_ in rules.get("platforms", []) if isinstance(p_, dict)}
    for name, items in (patch.get("platforms") or {}).items():
        for it in items if isinstance(items, list) else []:
            tgt = plat.get(name)
            i = it.get("index")
            if tgt is None or i is None or i >= len(tgt.get("items", [])) or not str(it.get("source_url", "")).startswith("http"):
                skipped.append(f"{p.name}: platforms.{name}[{i}] unmatched"); continue
            old = tgt["items"][i]
            if old.get("verified") is True and it.get("verified") is not True and not it.get("changed"):
                skipped.append(f"{p.name}: platforms.{name}[{i}] would downgrade"); continue
            if old.get("value") != it["value"]:
                changes.append(f"platforms.{name}[{i}]: {str(old.get('value'))[:70]!r} -> {it['value'][:70]!r}")
            tgt["items"][i] = {"value": it["value"], "source_url": it["source_url"], "verified": it.get("verified", False),
                               "checked": it.get("checked", "2026-11-24"), **({"evidence": it["evidence"]} if it.get("evidence") else {})}
            applied += 1
    for sec in ("cities", "states"):
        for key, flds in (patch.get(sec) or {}).items():
            target = fields_of(sec, key)
            if target is None:
                skipped.append(f"{p.name}: {sec}.{key} not in rules.json"); continue
            for fname, new in flds.items():
                if not isinstance(new, dict) or "value" not in new:
                    continue
                if not str(new.get("source_url", "")).startswith("http"):
                    skipped.append(f"{p.name}: {sec}.{key}.{fname} has no source_url"); continue
                old = target.get(fname, {})
                if old.get("verified") is True and new.get("verified") is not True and not new.get("changed"):
                    skipped.append(f"{p.name}: {sec}.{key}.{fname} would downgrade a verified field"); continue
                if old.get("value") != new["value"]:
                    changes.append(f"{sec}.{key}.{fname}: {str(old.get('value'))[:70]!r} -> {new['value'][:70]!r}")
                target[fname] = {"value": new["value"], "source_url": new["source_url"], "verified": new.get("verified", False),
                                 "checked": new.get("checked", "2026-11-24"), **({"evidence": new["evidence"]} if new.get("evidence") else {})}
                applied += 1
rules["checked"] = "2026-11-24"
rules.setdefault("notes", [])
if isinstance(rules["notes"], list) and not any("second-pass verification" in str(n) for n in rules["notes"]):
    rules["notes"].append("2026-11-24: second-pass verification against official code hosts, statutes and agency pages; 'evidence' names the section relied on.")
RULES.write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")
after = tally()
print("before:", before, "\nafter: ", after, f"\napplied {applied} field updates, {len(changes)} value changes")
for c in changes:
    print("  ~", c)
for s in skipped:
    print("  skip:", s)
