"""Relevance: a keyword score against the profile in the config, mapped to High / Medium / Low.

    profile:
      title:        {regex: weight}   matched against the title
      description:  {regex: weight}   matched against the description
      thresholds:   {high: 8, medium: 4}

Each pattern counts once, whatever the number of hits. Weights may be negative (an off-target word). The
`why` string lists every pattern that fired, so a level can always be explained.
"""
import re


class Profile:
    def __init__(self, profile):
        self.title = [(p, re.compile(p, re.I), w) for p, w in (profile.get("title") or {}).items()]
        self.description = [(p, re.compile(p, re.I), w) for p, w in (profile.get("description") or {}).items()]
        th = profile.get("thresholds") or {}
        self.high, self.medium = th.get("high", 8), th.get("medium", 4)

    def score(self, title, description):
        """(level, score, why)."""
        total, parts = 0, []
        for where, rules, text in (("title", self.title, title or ""), ("desc", self.description, description or "")):
            for pattern, rx, weight in rules:
                if rx.search(text):
                    total += weight
                    parts.append(f"{where} '{pattern}' {weight:+g}")
        level = "High" if total >= self.high else "Medium" if total >= self.medium else "Low"
        return level, total, f"{level} = {total:g}" + (": " + ", ".join(parts) if parts else ": no profile keyword")
