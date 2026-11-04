"""Configuration: one YAML file, merged over the defaults below.

Relative paths in the file (database, digest_dir, boards_cache, rules files) resolve against the folder that
holds the config file, so a cron job can run from any working directory.
"""
import copy
import os
import re

DEFAULTS = {
    "database": "jobs.db",
    "digest_dir": "digest",
    "boards_cache": "boards.json",
    "timezone": "UTC",
    "user_agent": "Mozilla/5.0 (compatible; job-radar/0.1)",
    "companies": [],
    "sources": {
        "ats": {"enabled": True, "providers": ["ashby", "greenhouse", "lever", "recruitee", "pinpoint"],
                "workers": 6, "min_interval": 0.12},
        "workable": {"enabled": True, "queries": [], "locations": [], "remote_locations": [], "pages": 3},
        "freehire": {"enabled": False, "keywords": [], "days": 2, "queries": [
            {"label": "eu_uk", "params": {"regions": ["eu", "uk"]}},
            {"label": "remote", "params": {"work_mode": "remote", "regions": ["none"]}}]},
    },
    "gates": {
        "title_match": [],
        "role_families": None,          # None = every built-in family; [] = none
        "custom_families": [],          # [{name, title_regex}]
        "work_authorization": True,
        "languages": {"enabled": True, "spoken": ["English"]},
        "software_engineer": {"enabled": True, "threshold": None, "rules_file": None},
        "regions": {"blocked": [], "relocation_required": [], "extra_hints": {}},
        "work_mode": {"allowed": ["remote", "hybrid", "office", "unknown"]},
        "freshness_hours": 24,
    },
    "signals": [],                      # [{name, pattern}] tags that rank, never filter
    "profile": {"title": {}, "description": {}, "thresholds": {"high": 8, "medium": 4}},
    "digest": {"levels": ["High", "Medium"], "limit": 30, "description_chars": 2000},
    "notion": {"enabled": False, "token_env": "NOTION_TOKEN", "database_id": None,
               "database_id_env": "NOTION_DATABASE_ID", "create_levels": ["High", "Medium"],
               "keep_statuses": ["shortlist", "applied"], "log_dir": "notion-logs"},
}


class ConfigError(ValueError):
    pass


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path=None, data=None):
    """Config dict from a YAML file (or from `data`, for tests), defaults filled in and checked."""
    base_dir = os.getcwd()
    if data is None:
        try:
            import yaml
        except ImportError as exc:   # pragma: no cover
            raise ConfigError("PyYAML is required: pip install pyyaml") from exc
        if not path or not os.path.exists(path):
            raise ConfigError(f"config file not found: {path} (copy config.example.yaml to config.yaml)")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        base_dir = os.path.dirname(os.path.abspath(path))
    cfg = _merge(DEFAULTS, data)
    for key in ("database", "digest_dir", "boards_cache"):
        cfg[key] = os.path.join(base_dir, os.path.expanduser(cfg[key]))
    swe = cfg["gates"]["software_engineer"]
    if swe.get("rules_file"):
        swe["rules_file"] = os.path.join(base_dir, os.path.expanduser(swe["rules_file"]))
    cfg["notion"]["log_dir"] = os.path.join(base_dir, os.path.expanduser(cfg["notion"]["log_dir"]))
    validate(cfg)
    return cfg


def validate(cfg):
    from .gates import region, role
    for pattern in cfg["gates"]["title_match"]:
        _compile(pattern, "gates.title_match")
    fams = cfg["gates"]["role_families"]
    unknown = set(fams or []) - set(role.FAMILIES)
    if unknown:
        raise ConfigError(f"gates.role_families: unknown family {sorted(unknown)}; known: {list(role.FAMILIES)}")
    for fam in cfg["gates"]["custom_families"]:
        _compile(fam.get("title_regex", ""), f"gates.custom_families {fam.get('name')}")
    regions = cfg["gates"]["regions"]
    for key in ("blocked", "relocation_required"):
        bad = set(regions.get(key) or []) - set(region.AREAS)
        if bad:
            raise ConfigError(f"gates.regions.{key}: unknown area {sorted(bad)}; known: {list(region.AREAS)}")
    for sig in cfg["signals"]:
        _compile(sig.get("pattern", ""), f"signals {sig.get('name')}")
    for where in ("title", "description"):
        for pattern, weight in (cfg["profile"].get(where) or {}).items():
            _compile(pattern, f"profile.{where}")
            if not isinstance(weight, (int, float)):
                raise ConfigError(f"profile.{where}[{pattern!r}]: weight must be a number")
    for c in cfg["companies"]:
        if isinstance(c, dict) and c.get("provider") and c["provider"] not in cfg["sources"]["ats"]["providers"] + [
                "ashby", "greenhouse", "lever", "recruitee", "pinpoint"]:
            raise ConfigError(f"companies: unknown provider {c['provider']!r}")


def _compile(pattern, where):
    try:
        return re.compile(pattern, re.I)
    except re.error as exc:
        raise ConfigError(f"{where}: bad regex {pattern!r}: {exc}") from exc


def title_regex(cfg):
    """One compiled title-match regex; an empty list matches every title."""
    patterns = cfg["gates"]["title_match"]
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I) if patterns else re.compile("")
