"""Collection from keyless public job-board APIs.

Per-company boards: Ashby, Greenhouse, Lever, Recruitee, Pinpoint. A company name from the config is resolved to a
board by probing slug candidates against each provider; the result (hit or miss, and which providers were tried)
is cached in a JSON file so the next run only fetches. Cross-company search: Workable. Optional: freehire.me.

Every request is a plain unauthenticated GET. One failing board, page or posting is logged and skipped; it never
stops the run.
"""
import http.client
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .store import text_of

PROVIDERS = ("ashby", "greenhouse", "lever", "recruitee", "pinpoint")
HOST_INTERVALS = {"recruitee": 0.4}   # Recruitee answers 429 above roughly 3 requests per second


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Throttle:
    """Spaces requests across worker threads: a global floor between any two requests and a slower floor per
    provider where the provider rate-limits."""

    def __init__(self, min_interval=0.12, host_intervals=None):
        self.min_interval = min_interval
        self.host_intervals = HOST_INTERVALS if host_intervals is None else host_intervals
        self.lock = threading.Lock()
        self.next_slot = 0.0
        self.host_slot = {}

    def wait(self, provider=None):
        with self.lock:
            now = time.monotonic()
            wait = max(0.0, self.next_slot - now)
            self.next_slot = max(now, self.next_slot) + self.min_interval
            if provider in self.host_intervals:
                host_wait = max(0.0, self.host_slot.get(provider, 0.0) - now)
                self.host_slot[provider] = max(now, self.host_slot.get(provider, 0.0)) + self.host_intervals[provider]
                wait = max(wait, host_wait)
        if wait:
            time.sleep(wait)


def request_json(url, user_agent, tries=4, timeout=30):
    """(status, body). Retries 429 / 5xx and network errors with backoff. 599 = no answer, 400 = invalid URL."""
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    delay = 3
    for attempt in range(tries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(delay + random.random() * 2)
                delay *= 2
                continue
            return exc.code, ""
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            if attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return 599, ""
        except ValueError:   # invalid URL, e.g. a slug too long for a host name label: no board, no retry
            return 400, ""
    return 599, ""


# --- board resolution ------------------------------------------------------------------------------------------

def slug_candidates(name):
    clean = re.sub(r"\([^)]*\)", "", name).strip().lower()
    clean = re.sub(r",?\s*\b(inc|ltd|llc|gmbh|limited|corp|corporation)\.?$", "", clean).strip()
    base = re.sub(r"[^a-z0-9]+", "-", clean).strip("-")
    compact = base.replace("-", "")
    no_tld = re.sub(r"-(io|ai|com|co)$", "", base)
    return list(dict.fromkeys(x for x in (base, compact, no_tld) if x))


def board_url(provider, slug):
    return {
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
        "recruitee": f"https://{slug}.recruitee.com/api/offers/",
        "pinpoint": f"https://{slug}.pinpointhq.com/postings.json",
    }[provider]


LIST_KEY = {"ashby": "jobs", "greenhouse": "jobs", "recruitee": "offers", "pinpoint": "data"}


def valid_board(provider, body):
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False
    key = LIST_KEY.get(provider)
    if key:
        return isinstance(data, dict) and isinstance(data.get(key), list)
    return isinstance(data, list)


def board_jobs(provider, data):
    """The postings list inside one board response."""
    key = LIST_KEY.get(provider)
    jobs = data.get(key, []) if key else data
    return jobs if isinstance(jobs, list) else []


def company_entries(cfg):
    """[(name, explicit board or None)] from the config's company list (strings or {name, provider, slug})."""
    out = []
    for c in cfg["companies"]:
        if isinstance(c, str):
            out.append((c, None))
        elif isinstance(c, dict) and c.get("name"):
            board = {"provider": c["provider"], "slug": c.get("slug") or slug_candidates(c["name"])[0]} \
                if c.get("provider") else None
            out.append((c["name"], board))
    return out


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(path, cache):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def resolve_boards(cfg, refresh=False, save=True, fetch=request_json, throttle=None):
    """{company: board} for every configured company. Explicit boards win; the rest are probed once and cached.
    A cached miss is retried only against providers it was not tried on yet; a 429 / 5xx is not an answer, so
    that provider stays untried."""
    ats = cfg["sources"]["ats"]
    providers = [p for p in ats["providers"] if p in PROVIDERS]
    cache = {} if refresh else load_cache(cfg["boards_cache"])
    throttle = throttle or Throttle(ats.get("min_interval", 0.12))
    ua = cfg["user_agent"]
    boards, pending = {}, []
    for name, explicit in company_entries(cfg):
        if explicit:
            boards[name] = explicit
            continue
        entry = cache.get(name)
        if entry and entry.get("provider"):
            boards[name] = entry
            continue
        untried = [p for p in providers if p not in (entry or {}).get("tried", [])]
        if untried:
            pending.append((name, untried, entry))

    def probe(item):
        name, untried, entry = item
        failed = set()
        for slug in slug_candidates(name):
            for provider in untried:
                throttle.wait(provider)
                status, body = fetch(board_url(provider, slug), ua, tries=2)
                if status == 429 or status >= 500:
                    failed.add(provider)
                elif status == 200 and valid_board(provider, body):
                    return name, {"provider": provider, "slug": slug, "resolved_at": _stamp()}
        tried = sorted((set((entry or {}).get("tried", [])) | set(untried)) - failed)
        return name, {"provider": None, "slug": None, "tried": tried, "resolved_at": _stamp()}

    if pending:
        with ThreadPoolExecutor(max_workers=ats.get("workers", 6)) as ex:
            for fut in as_completed([ex.submit(probe, p) for p in pending]):
                try:
                    name, result = fut.result()
                except Exception as exc:   # one bad company never stops the run; it is retried next time
                    log(f"[resolve-error] {type(exc).__name__}: {exc}")
                    continue
                cache[name] = result
                if result.get("provider"):
                    boards[name] = result
    if save and pending:
        save_cache(cfg["boards_cache"], cache)
    found = Counter(b["provider"] for b in boards.values())
    log(f"[resolve] companies={len(company_entries(cfg))} boards={len(boards)} probed={len(pending)} by_provider={dict(found)}")
    return boards


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- normalisation ---------------------------------------------------------------------------------------------

def parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    value = str(value).replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(value)
        return result.replace(tzinfo=result.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


def plain_text(value):
    return text_of(value) if value else None


def normalize_ats(provider, slug, company, raw):
    """One board posting as a row dict: job_id '<provider>:<slug>:<id>', title, company, location, workplace,
    description, url, apply_url, posted (aware datetime or None)."""
    if provider == "ashby":
        return {
            "job_id": f"ashby:{slug}:{raw.get('id') or raw.get('jobUrl')}",
            "title": raw.get("title"), "company": company, "location": raw.get("location"),
            "workplace": raw.get("workplaceType"),
            "description": plain_text(raw.get("descriptionHtml")) or raw.get("descriptionPlain"),
            "url": raw.get("jobUrl"), "apply_url": raw.get("applyUrl"), "posted": parse_date(raw.get("publishedAt")),
        }
    if provider == "greenhouse":
        return {
            "job_id": f"greenhouse:{slug}:{raw.get('id')}",
            "title": raw.get("title"), "company": company,
            "location": (raw.get("location") or {}).get("name"), "workplace": None,
            "description": plain_text(raw.get("content")), "url": raw.get("absolute_url"),
            "apply_url": raw.get("absolute_url"),
            "posted": parse_date(raw.get("first_published") or raw.get("updated_at")),
        }
    if provider == "recruitee":
        if raw.get("status") not in (None, "published"):
            return {"job_id": None}
        published = (raw.get("published_at") or raw.get("created_at") or "").replace(" UTC", "+00:00").replace(" ", "T", 1)
        return {
            "job_id": f"recruitee:{slug}:{raw.get('id')}",
            "title": raw.get("title"), "company": raw.get("company_name") or company,
            "location": raw.get("location") or ", ".join(x for x in (raw.get("city"), raw.get("country")) if x) or None,
            "workplace": "remote" if raw.get("remote") else ("hybrid" if raw.get("hybrid") else (
                "on_site" if raw.get("on_site") else None)),
            "description": plain_text((raw.get("description") or "") + " " + (raw.get("requirements") or "")),
            "url": raw.get("careers_url"), "apply_url": raw.get("careers_apply_url") or raw.get("careers_url"),
            "posted": parse_date(published or None),
        }
    if provider == "pinpoint":
        loc = raw.get("location") or {}
        place = ", ".join(x for x in (loc.get("name"), loc.get("city"), loc.get("province")) if x) or None
        parts = [raw.get(k) for k in ("description", "key_responsibilities", "skills_knowledge_expertise")]
        return {
            "job_id": f"pinpoint:{slug}:{raw.get('id')}",
            "title": raw.get("title"), "company": company, "location": place,
            "workplace": {"remote": "remote", "hybrid": "hybrid", "onsite": "on_site"}.get(raw.get("workplace_type")),
            "description": plain_text(" ".join(x for x in parts if x)),
            "url": raw.get("url"), "apply_url": raw.get("url"), "posted": None,   # the feed carries no publish date
        }
    categories = raw.get("categories") or {}
    return {
        "job_id": f"lever:{slug}:{raw.get('id')}",
        "title": raw.get("text"), "company": company, "location": categories.get("location"),
        "workplace": raw.get("workplaceType"),
        "description": raw.get("descriptionPlain") or plain_text(raw.get("description")),
        "url": raw.get("hostedUrl"), "apply_url": raw.get("applyUrl"), "posted": parse_date(raw.get("createdAt")),
    }


def collect_ats(boards, cfg, keep, fetch=request_json, throttle=None):
    """Title-matched rows from every resolved board. `keep(row)` is the ingest pre-filter (title, region)."""
    ats = cfg["sources"]["ats"]
    throttle = throttle or Throttle(ats.get("min_interval", 0.12))
    stats, rows = Counter(), []

    def get(item):
        company, board = item
        throttle.wait(board["provider"])
        status, body = fetch(board_url(board["provider"], board["slug"]), cfg["user_agent"])
        if status != 200:
            return item, status, []
        try:
            return item, status, board_jobs(board["provider"], json.loads(body))
        except (json.JSONDecodeError, AttributeError) as exc:   # unexpected shape (null, array): skip this board
            log(f"[ats-error] {board['provider']}/{board['slug']}: {type(exc).__name__}")
            return item, 598, []

    with ThreadPoolExecutor(max_workers=ats.get("workers", 6)) as ex:
        for fut in as_completed([ex.submit(get, b) for b in boards.items()]):
            (company, board), status, jobs = fut.result()
            stats["boards"] += 1
            if status != 200:
                stats["boards_failed"] += 1
                log(f"[ats-error] {board['provider']}/{board['slug']}: HTTP {status}")
                continue
            stats["postings"] += len(jobs)
            for raw in jobs:
                try:
                    row = normalize_ats(board["provider"], board["slug"], board.get("company") or company, raw)
                    if not row.get("job_id") or not keep(row, stats):
                        continue
                except Exception:   # one malformed posting (null, wrong field types) is skipped
                    stats["bad_posting"] += 1
                    continue
                rows.append(row)
    log(f"[ats] {dict(stats)} kept={len(rows)}")
    return rows


# --- Workable global search -----------------------------------------------------------------------------------

WORKABLE_URL = "https://jobs.workable.com/api/v1/jobs"
WORKABLE_PAGE = 20


def normalize_workable(raw):
    loc = raw.get("location") or {}
    place = ", ".join(x for x in (loc.get("city"), loc.get("subregion"), loc.get("countryName")) if x) or None
    if "TELECOMMUTE" in (raw.get("locations") or []) and place is None:
        place = "Remote"
    return {
        "job_id": f"workable:{raw.get('id')}",
        "title": raw.get("title"), "company": (raw.get("company") or {}).get("title"), "location": place,
        "workplace": raw.get("workplace"),
        "description": plain_text(" ".join(x for x in (raw.get("description"), raw.get("requirementsSection"),
                                                       raw.get("benefitsSection")) if x)),
        "url": raw.get("url"), "apply_url": raw.get("url"), "posted": parse_date(raw.get("created")),
    }


def workable_searches(wk):
    for query in wk["queries"]:
        for where in wk["locations"]:
            yield query, {"location": where}
        for where in wk["remote_locations"]:
            yield query, {"location": where, "workplace": "remote"}
        if not wk["locations"] and not wk["remote_locations"]:
            yield query, {}


def collect_workable(cfg, keep, fetch=request_json, throttle=None):
    """Newest-first pages of the one public cross-company ATS search; same pre-filter as the boards."""
    wk = cfg["sources"]["workable"]
    throttle = throttle or Throttle(cfg["sources"]["ats"].get("min_interval", 0.12))
    stats, rows, seen = Counter(), [], set()
    for query, extra in workable_searches(wk):
        token = None
        for _ in range(wk.get("pages", 3)):
            params = {"query": query, **extra}
            if token:
                params["pageToken"] = token
            throttle.wait("workable")
            status, body = fetch(WORKABLE_URL + "?" + urllib.parse.urlencode(params), cfg["user_agent"])
            stats["requests"] += 1
            try:
                data = json.loads(body) if status == 200 else None
            except json.JSONDecodeError:
                data = None
            jobs = data.get("jobs") if isinstance(data, dict) else None
            if not isinstance(jobs, list):
                stats["errors"] += 1
                log(f"[workable-error] {query!r} {extra}: HTTP {status}")
                break
            for raw in jobs:
                try:
                    if raw.get("id") in seen:
                        continue
                    seen.add(raw.get("id"))
                    stats["postings"] += 1
                    row = normalize_workable(raw)
                    if not keep(row, stats):
                        continue
                except Exception:
                    stats["bad_posting"] += 1
                    continue
                rows.append(row)
            token = data.get("nextPageToken")
            if not token or len(jobs) < WORKABLE_PAGE:
                break
    log(f"[workable] {dict(stats)} kept={len(rows)}")
    return rows


# --- freehire.me (optional, off by default) --------------------------------------------------------------------

FREEHIRE_URL = "https://freehire.me/api/v1/agent/jobs/search"
FREEHIRE_PAGE = 100
FREEHIRE_MAX_OFFSET = 1000   # safety cap per query; the API bounds offset + limit
COUNTRY_NAMES = {
    "at": "austria", "be": "belgium", "bg": "bulgaria", "hr": "croatia", "cy": "cyprus", "cz": "czech",
    "dk": "denmark", "ee": "estonia", "fi": "finland", "fr": "france", "de": "germany", "gr": "greece",
    "hu": "hungary", "ie": "ireland", "it": "italy", "lv": "latvia", "lt": "lithuania", "lu": "luxembourg",
    "mt": "malta", "nl": "netherlands", "pl": "poland", "pt": "portugal", "ro": "romania", "sk": "slovakia",
    "si": "slovenia", "es": "spain", "se": "sweden", "gb": "united kingdom", "uk": "united kingdom",
    "ch": "switzerland", "no": "norway", "us": "united states", "ca": "canada"}


def freehire_get(params, user_agent, tries=4):
    """GET one freehire page. Paces on X-RateLimit-Remaining and sleeps for Retry-After on 429.
    Returns (status, parsed JSON or None)."""
    url = FREEHIRE_URL + "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    delay = 3
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                remaining = response.headers.get("X-RateLimit-Remaining")
                reset = response.headers.get("X-RateLimit-Reset")
                body = response.read().decode("utf-8", "ignore")
            if remaining is not None and str(remaining).isdigit() and int(remaining) <= 5:
                time.sleep(int(reset) + 1 if reset and str(reset).isdigit() else 5)
            return 200, json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < tries - 1:
                retry = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(int(retry) + 1 if retry and str(retry).isdigit() else delay)
                continue
            if exc.code in (500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return exc.code, None
        except (urllib.error.URLError, OSError, http.client.HTTPException, json.JSONDecodeError):
            if attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return 599, None
    return 599, None


def normalize_freehire(raw, keyword):
    cities, countries = raw.get("cities") or [], raw.get("countries") or []
    enrichment = raw.get("enrichment") or {}
    if not (isinstance(cities, list) and isinstance(countries, list) and isinstance(enrichment, dict)):
        raise TypeError("cities/countries must be lists and enrichment an object")
    cities, countries = [str(c) for c in cities], [str(c) for c in countries]
    location = ", ".join(dict.fromkeys(cities + [COUNTRY_NAMES.get(c, c.upper()) for c in countries])) or raw.get("location")
    summary = " | ".join(f"{k}={enrichment[k]}" for k in ("summary", "seniority", "category", "employment_type")
                         if enrichment.get(k))
    return {
        "job_id": "fh:" + raw["public_slug"],
        "title": raw.get("title"), "company": raw.get("company"), "location": location,
        "workplace": raw.get("work_mode"), "description": raw.get("description"),
        "url": raw.get("url"), "apply_url": raw.get("url"), "posted": parse_date(raw.get("posted_at")),
        "query": raw.get("source") or "", "location_query": keyword,
        "fh_source": raw.get("source"), "fh_external_id": raw.get("external_id"),
        "fh_countries": [c.lower() for c in countries],
        "note": f"freehire enrichment: {summary}" if summary else None,
    }


def collect_freehire(cfg, keep, get=freehire_get):
    fh = cfg["sources"]["freehire"]
    stats, rows = Counter(), []
    for keyword in fh["keywords"]:
        for q in fh["queries"]:
            offset = 0
            while True:
                params = {"q": keyword, "q_fields": "title", "posted_within_days": fh.get("days", 2),
                          "description_format": "text", "limit": FREEHIRE_PAGE, "offset": offset, **q["params"]}
                status, data = get(params, cfg["user_agent"])
                page = data.get("data") if isinstance(data, dict) else None
                if status != 200 or not isinstance(page, list):
                    log(f"[freehire-error] {keyword!r} {q['label']} offset={offset}: HTTP {status}")
                    stats["errors"] += 1
                    break
                meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                for raw in page:
                    try:
                        if raw.get("public_slug") and not raw.get("closed_at"):
                            row = normalize_freehire(raw, keyword)
                            if keep(row, stats):
                                rows.append(row)
                    except (AttributeError, TypeError, KeyError, ValueError, OverflowError):
                        stats["bad_posting"] += 1
                offset += len(page)
                total = meta.get("total")
                if not page or offset >= (total if isinstance(total, int) else 0) or offset >= FREEHIRE_MAX_OFFSET:
                    break
    log(f"[freehire] {dict(stats)} kept={len(rows)}")
    return rows
