"""Software-engineer gate: flag postings that require a backend / software-engineer background.

Meant for a profile that is go-to-market, not engineering. Deterministic, no model calls. The phrase list lives in
a JSON rules file (default: job_radar/data/swe_gate.json).

    detect(title, description, rules) -> (score, [(name, evidence)])
    is_swe(title, description, rules) -> (True, "score N: name: evidence | ...") or (False, None)

Rule: a score, not a keyword hit.
  * The description is split into lines. A short line without a final period is a header: a requirements
    header ("What you bring", "Qualifications") turns the requirement context on, another header
    ("Responsibilities", "The role", "Nice to have") turns it off.
  * A phrase counts only when its clause is not nice-to-have ("a plus", "preferred", "familiarity with") AND
    either sits under a requirements header or carries a strong marker ("experience as", "proficiency",
    "must", "5+ years"). "Backend services" in a responsibilities bullet scores nothing.
  * A clause that offers the background as one option ("or a similar technical role") scores nothing.
  * Each phrase scores its weight once; a software-engineer title scores title_weight.
  * Excluded when score >= threshold. One strong requirement (weight 2) is never enough on its own.
"""
import json
import os
import re

DEFAULT_RULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "swe_gate.json")
EVIDENCE_MAX = 120
_CACHE = {}


def load(path=None, threshold=None):
    """Compiled rules from a JSON file; `threshold` overrides the file's value."""
    path = path or DEFAULT_RULES
    key = (path, threshold)
    if key in _CACHE:
        return _CACHE[key]
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    c = {"threshold": cfg["threshold"] if threshold is None else threshold, "title_weight": cfg["title_weight"]}
    c["title"] = re.compile("|".join(cfg["title_phrases"]), re.I)
    c["phrases"] = [(p["name"], p["weight"], re.compile(p["regex"], re.I)) for p in cfg["phrases"]]
    for k in ("alternative_markers", "requirement_headers", "other_headers", "strong_markers", "nice_markers"):
        c[k] = re.compile(cfg[k], re.I)
    _CACHE[key] = c
    return c


def _is_header(line):
    s = line.strip(" -•*\t:")
    return 0 < len(s) <= 60 and not s.endswith(".") and not re.match(r"[-•*]", line.strip())


def clauses(desc, c):
    """(clause, in_requirements) for every clause of the description."""
    in_req = False
    for line in re.split(r"\n+", desc or ""):
        if not line.strip():
            continue
        if _is_header(line):
            if c["requirement_headers"].search(line):
                in_req = True
            elif c["other_headers"].search(line):
                in_req = False
            continue
        for part in re.split(r"(?<=[.!?;])\s+", line):
            part = part.strip(" -•*\t")
            if part:
                yield part, in_req


def _snippet(clause, m):
    if len(clause) <= EVIDENCE_MAX:
        return clause
    start = max(0, min(m.start() - 40, len(clause) - EVIDENCE_MAX))
    return clause[start:start + EVIDENCE_MAX].strip()


def detect(title, desc, c=None):
    c = c or load()
    score, found = 0, []
    if title and c["title"].search(title):
        score += c["title_weight"]
        found.append(("title", title[:EVIDENCE_MAX]))
    seen = set()
    for clause, in_req in clauses(desc, c):
        if c["nice_markers"].search(clause) or c["alternative_markers"].search(clause):
            continue
        if not (in_req or c["strong_markers"].search(clause)):
            continue
        for name, weight, rx in c["phrases"]:
            m = rx.search(clause)
            if m and name not in seen:
                seen.add(name)
                score += weight
                found.append((name, _snippet(clause, m)))
    return score, found


def is_swe(title, desc, c=None):
    c = c or load()
    score, found = detect(title, desc, c)
    if score >= c["threshold"]:
        return True, f"score {score}: " + " | ".join(f"{n}: {e}" for n, e in found)[:400]
    return False, None
