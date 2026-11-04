"""Run every gate on stored postings and write the verdicts back to the jobs table.

excluded_reason holds the first gate that rules a posting out, in this order:
  geo        every listed place is in a blocked area, or in a relocation-required area without relocation / visa
  blocker    an explicit work-authorisation or no-sponsorship statement
  role       a role family from gates/role.py or a custom family from the config
  swe        a software-engineer background is required
  language   a language outside `spoken` is required
  work mode  the detected work mode is not in the allowed list
A posting with no excluded_reason and title_match = 1 is in scope. Relevance is scored for every posting.
"""
import re

from . import config as config_mod
from .gates import authorization, lang, region, role, swe, work_mode
from .relevance import Profile


class Gates:
    """Compiled gates for one config."""

    def __init__(self, cfg):
        g = cfg["gates"]
        self.title = config_mod.title_regex(cfg)
        fams = g["role_families"]
        self.families = None if fams is None else set(fams)
        self.custom = [(f["name"], re.compile(f["title_regex"], re.I)) for f in g["custom_families"]]
        self.work_auth = g["work_authorization"]
        self.lang_on = g["languages"].get("enabled", True)
        self.spoken = g["languages"].get("spoken") or ["English"]
        sw = g["software_engineer"]
        self.swe = swe.load(sw.get("rules_file"), sw.get("threshold")) if sw.get("enabled", True) else None
        reg = g["regions"]
        self.blocked = reg.get("blocked") or []
        self.reloc_required = reg.get("relocation_required") or []
        self.extra_hints = reg.get("extra_hints") or {}
        self.modes = set(g["work_mode"].get("allowed") or [])
        self.signals = [(s["name"], re.compile(s["pattern"], re.I)) for s in cfg["signals"]]
        self.profile = Profile(cfg["profile"])

    def region_blocked(self, location):
        """Pre-filter used at ingest: True when the location alone is in a blocked area."""
        return bool(region.region_reason(location, self.blocked, (), True, self.extra_hints))

    def classify(self, job):
        """Verdict columns for one posting dict (title, company, location, workplace, description, flags)."""
        title, desc, loc = job.get("title"), job.get("description"), job.get("location")
        reloc, visa = job.get("relocation_flag"), job.get("visa_flag")
        if reloc is None and visa is None:
            reloc, visa = authorization.flags(desc)
        out = {"relocation_flag": reloc, "visa_flag": visa,
               "region": region.classify_region(loc, self.extra_hints),
               "title_match": None if not title else int(bool(self.title.search(title)))}
        reasons = []
        geo = region.region_reason(loc, self.blocked, self.reloc_required,
                                   authorization.offers_relocation(reloc, visa, desc), self.extra_hints)
        if geo:
            reasons.append(geo)
        kind, phrase = authorization.blocker(desc) if self.work_auth else (None, None)
        out["blocker_phrase"] = phrase
        if kind:
            reasons.append(f"blocker: {kind}")
        fam = role.role_family(title, job.get("company"), desc, None, self.families, self.custom)
        out["role_evidence"] = role.salesforce_required(desc, job.get("company")) if fam == "salesforce" else None
        if fam:
            reasons.append(f"role: {fam}")
        is_swe, evidence = swe.is_swe(title, desc, self.swe) if self.swe else (False, None)
        out["swe_evidence"] = evidence
        if is_swe:
            reasons.append("role: software engineer")
        lang_req, lang_ev = lang.detect(desc, self.spoken) if self.lang_on else (None, None)
        out["lang_req"], out["lang_evidence"] = lang_req, lang_ev
        if lang_req:
            reasons.append(f"language: {lang_req}")
        mode, mode_ev = work_mode.detect(desc, loc, job.get("workplace"))
        out["work_mode"], out["work_mode_evidence"] = mode, mode_ev
        if mode not in self.modes:
            reasons.append(f"work mode: {mode}")
        out["excluded_reason"] = reasons[0] if reasons else None
        tags = [name for name, rx in self.signals if rx.search(f"{loc or ''}\n{desc or ''}")]
        if region.is_multi_place(loc):
            tags.append("Multi-location")
        out["signals"] = ",".join(tags) or None
        out["relevance"], out["relevance_score"], out["relevance_why"] = self.profile.score(title, desc)
        return out


COLUMNS = ("relocation_flag", "visa_flag", "region", "title_match", "excluded_reason", "blocker_phrase", "role_evidence", "swe_evidence",
           "lang_req", "lang_evidence", "work_mode", "work_mode_evidence", "signals", "relevance",
           "relevance_score", "relevance_why")


def classify_all(con, cfg, job_id=None, gates=None):
    """Classify every stored posting (or one). Returns the number of rows written."""
    gates = gates or Gates(cfg)
    q = "SELECT job_id, title, company, location, workplace, description, relocation_flag, visa_flag FROM jobs"
    rows = con.execute(q + (" WHERE job_id=?" if job_id else ""), (job_id,) if job_id else ()).fetchall()
    keys = ("job_id", "title", "company", "location", "workplace", "description", "relocation_flag", "visa_flag")
    sets = ", ".join(f"{c}=?" for c in COLUMNS)
    for r in rows:
        v = gates.classify(dict(zip(keys, r)))
        con.execute(f"UPDATE jobs SET {sets} WHERE job_id=?", (*[v[c] for c in COLUMNS], r[0]))
    con.commit()
    return len(rows)
