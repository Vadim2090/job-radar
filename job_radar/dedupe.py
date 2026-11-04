"""Cross-source duplicate detection for aggregator postings (freehire.me).

The same posting can arrive from its own board and from an aggregator under another id. A row is a duplicate of
an existing posting when, checked in this order: the (platform, board, posting id) triple matches, the canonical
URL matches, or company + title match within DEDUPE_DAYS and the countries do not disagree.
"""
import html
import re
import urllib.parse
from collections import Counter
from datetime import datetime

from .sources import COUNTRY_NAMES, parse_date

DEDUPE_DAYS = 30


TRACKING_PARAMS = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref", "refid", "referrer", "source",
                   "src", "trk", "trackingid", "campaign", "medium", "li_fat_id", "_hsenc", "_hsmi"}


def canonical_url(url):
    """Lower-case host and path, no scheme, fragment, www. or trailing slash. Tracking params are dropped;
    every other query parameter identifies the posting and is kept, sorted."""
    if not url:
        return None
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except (ValueError, AttributeError):   # e.g. 'https://[bad/' or a non-string
        return None
    host = parts.netloc.lower().removeprefix("www.")
    path = urllib.parse.unquote(parts.path).lower().rstrip("/")
    if not host:
        return None
    query = sorted((k.lower(), v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                   if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_"))
    return f"{host}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")


ATS_URL_PATTERNS = (
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?&]+)/jobs/(\d+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?]+)/([0-9a-f-]{36})", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([^/?]+)/([0-9a-f-]{36})", re.I)),
)


def ats_triple(job_id=None, url=None, fh_source=None, fh_external_id=None):
    """(platform, board, posting id), lower-cased, or None when it cannot be derived."""
    if job_id:
        parts = job_id.split(":", 2)
        if len(parts) == 3 and parts[0] in ("ashby", "greenhouse", "lever"):
            return parts[0], urllib.parse.unquote(parts[1]).lower(), parts[2].lower()
    if fh_source in ("ashby", "greenhouse", "lever") and fh_external_id and ":" in fh_external_id:
        board, ext = fh_external_id.split(":", 1)
        return fh_source, urllib.parse.unquote(board).lower(), ext.lower()
    for platform, pattern in ATS_URL_PATTERNS:
        match = pattern.search(url or "")
        if match:
            return platform, urllib.parse.unquote(match.group(1)).lower(), match.group(2).lower()
    return None


def norm_text(value):
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(value or "").lower()).strip()


def norm_company(value):
    return re.sub(r"\b(inc|ltd|llc|gmbh|limited|corp|corporation|ag|bv|sa)\b", " ", norm_text(value)).strip()


def _day(value):
    parsed = parse_date(value) if not isinstance(value, datetime) else value
    return parsed.date() if parsed else None


def location_conflict(codes, location):
    """True only when both sides state a country and they disagree. A missing key never counts as different."""
    names = {COUNTRY_NAMES[c] for c in codes if c in COUNTRY_NAMES}
    text = norm_text(location)
    if not names or not text:
        return False
    known = {n for n in COUNTRY_NAMES.values() if re.search(rf"\b{n}\b", text)}
    return bool(known) and not (known & names)


class DedupeIndex:
    """Existing postings keyed three ways, checked in this order: ATS triple, canonical url, then
    company + title + country within DEDUPE_DAYS. Each add() makes a posting visible to later checks."""

    def __init__(self):
        self.triples, self.urls, self.titles = {}, {}, {}

    def add(self, job_id, company, title, location, url, apply_url, posted, fh_source=None, fh_external_id=None):
        triple = ats_triple(job_id, url or apply_url, fh_source, fh_external_id)
        if triple:
            self.triples.setdefault(triple, job_id)
        for u in (url, apply_url):
            if canonical_url(u):
                self.urls.setdefault(canonical_url(u), job_id)
        if norm_company(company) and norm_text(title):
            self.titles.setdefault((norm_company(company), norm_text(title)), []).append((job_id, location, _day(posted)))

    def match(self, row):
        triple = ats_triple(None, row.get("url"), row.get("fh_source"), row.get("fh_external_id"))
        if triple and triple in self.triples:
            return self.triples[triple], "ats"
        cu = canonical_url(row.get("url"))
        if cu and cu in self.urls:
            return self.urls[cu], "url"
        mine = _day(row.get("posted"))
        for job_id, location, day in self.titles.get((norm_company(row.get("company")), norm_text(row.get("title"))), []):
            if job_id == row["job_id"]:
                continue
            if mine and day and abs((mine - day).days) > DEDUPE_DAYS:
                continue
            if location_conflict(row.get("fh_countries") or [], location):
                continue
            return job_id, "title"
        return None, None


def dedupe_freehire(rows, con, batch=()):
    """Mark every freehire row that is already stored or in this run's other rows with dup_of=<job_id> and
    dedupe_by=<ats|url|title>. The duplicate becomes a sighting on the existing posting, never a second row."""
    index = DedupeIndex()
    for jid, company, title, location, url, apply_url, posted in con.execute(
            "SELECT job_id, company, title, location, url, apply_url, posted_date FROM jobs "
            "WHERE job_id NOT LIKE 'fh:%'"):
        index.add(jid, company, title, location, url, apply_url, posted)
    for row in batch:
        if row.get("job_id") and not row["job_id"].startswith("fh:"):
            index.add(row["job_id"], row.get("company"), row.get("title"), row.get("location"),
                      row.get("url"), row.get("apply_url"), row.get("posted"))
    counts = Counter()
    for row in rows:
        if not row["job_id"].startswith("fh:"):
            continue
        dup, how = index.match(row)
        if dup:
            row["dup_of"], row["dedupe_by"] = dup, how
            counts[how] += 1
            continue
        # first unmatched freehire row stays; later reposts under other slugs match it on all three tiers
        index.add(row["job_id"], row.get("company"), row.get("title"), row.get("location"),
                  row.get("url"), row.get("apply_url"), row.get("posted"),
                  row.get("fh_source"), row.get("fh_external_id"))
    deduped = sum(counts.values())
    return deduped

