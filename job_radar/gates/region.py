"""Region gate: where a posting is, and whether its location rules it out.

Each place in a location string resolves to one area: europe, americas, australia, oceania (other than
Australia), asia_africa (the Middle East counts as Asia) or unknown. A posting lists several places when they are
separated by `;`, `|`, `/` or "or" ("Berlin / Remote", "London or Dublin"); "OR" right after a comma is the
Oregon state code ("Portland, OR") and keeps the place whole.

Two rules, both driven by config:
  blocked                Excluded when EVERY listed place is in a blocked area. One European place among Asian
                         ones keeps the posting; an unresolved place ("Remote", "Worldwide") always keeps it.
  relocation_required    Excluded when every listed place is in a blocked or relocation-required area, at least
                         one is relocation-required, and the posting does not offer relocation or visa support.

    area_of(place) -> area name
    classify_region(location) -> best area of the location (europe > americas > oceania/australia > unknown)
    region_reason(location, blocked, relocation_required, offers_relocation) -> "geo: ..." or None
"""
import re

ASIA_AFRICA = [
    # Asia incl. the Middle East
    "India", "China", "Hong Kong", "Taiwan", "Japan", "Korea", "Singapore", "Malaysia", "Indonesia",
    "Philippines", "Thailand", "Vietnam", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal",
    "Kazakhstan", "Uzbekistan", "Kyrgyzstan", "Armenia", "Georgia (country)", "Azerbaijan",
    "United Arab Emirates", "UAE", "Dubai", "Abu Dhabi", "Saudi Arabia", "Qatar", "Kuwait", "Bahrain",
    "Oman", "Israel", "Jordan", "Lebanon", "Iraq", "Iran", "Turkey", "Türkiye", "Istanbul", "APAC",
    "Asia", "Middle East", "MENA", "Bengaluru", "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune",
    "Chennai", "Gurugram", "Noida", "Tel Aviv", "Riyadh", "Doha", "Tokyo", "Seoul", "Shanghai",
    "Beijing", "Shenzhen", "Manila", "Jakarta", "Bangkok", "Kuala Lumpur", "Ho Chi Minh", "Karachi",
    "Lahore", "Dhaka", "Kolkata", "Myanmar", "Yangon", "Cambodia", "Mongolia",
    # Africa
    "Africa", "Nigeria", "Kenya", "Egypt", "South Africa", "Morocco", "Ghana", "Tunisia", "Ethiopia",
    "Uganda", "Rwanda", "Tanzania", "Senegal", "Algeria", "Lagos", "Nairobi", "Cairo",
    "Cape Town", "Johannesburg", "Casablanca", "Accra",
]
EUROPE_HINTS = [
    "European Union", "Europe", "EMEA", "United Kingdom", "Germany", "France", "Spain", "Portugal", "Netherlands",
    "Belgium", "Ireland", "Italy", "Poland", "Sweden", "Denmark", "Norway", "Finland", "Austria", "Switzerland",
    "Czechia", "Czech Republic", "Estonia", "Latvia", "Lithuania", "Romania", "Bulgaria", "Greece", "Cyprus",
    "Malta", "Luxembourg", "Hungary", "Croatia", "Slovenia", "Slovakia", "Montenegro", "Bosnia",
    "North Macedonia", "Albania", "Iceland", "Ukraine", "Moldova", "London", "Berlin", "Paris", "Madrid",
    "Barcelona", "Lisbon", "Amsterdam", "Dublin", "Munich", "Hamburg", "Stockholm", "Copenhagen", "Warsaw",
    "Prague", "Vienna", "Zurich", "Milan", "Krakow", "Kraków", "Wroclaw", "Wrocław", "Gdansk", "Tallinn",
    "Riga", "Vilnius", "Bucharest", "Sofia", "Athens", "Budapest", "Rotterdam", "Utrecht", "Brussels", "Antwerp",
    "Geneva", "Oslo", "Helsinki", "Porto", "Valencia", "Manchester", "Edinburgh", "Scotland", "Cambridge, England",
    "Bristol", "Frankfurt", "Cologne", "Stuttgart", "Düsseldorf", "Leipzig", "Lyon", "Nantes", "Turin", "Kyiv",
    "Kharkiv"]
AMERICAS_HINTS = [
    "United States", "Canada", "Mexico", "Brazil", "Argentina", "Colombia", "Chile", "Peru", "Uruguay",
    "Latin America", "LATAM", "Americas", "North America", "Costa Rica", "Panama", "San Francisco", "New York",
    "Los Angeles", "Seattle", "Boston", "Chicago", "Austin", "Denver", "Houston", "Dallas", "Atlanta", "Miami",
    "Washington DC", "St. Louis", "Salt Lake City", "Philadelphia", "Toronto", "Montreal", "Vancouver", "Calgary",
    "Ottawa", "Buenos Aires", "São Paulo", "Sao Paulo", "Mexico City", "Bogota", "Bogotá", "Santiago", "Lima",
    "Guyana", "Puerto Rico", "Guatemala", "Dominican Republic"]
US_RE = re.compile(r"\bU\.?S\.?(?:A\.?)?(?![A-Za-z])")   # US, USA, U.S., U.S.A.; case-sensitive
OCEANIA_HINTS = ["New Zealand", "Auckland", "Wellington", "Christchurch", "Fiji"]
# Victoria counts only as "Victoria, AU/Australia" (Victoria, British Columbia is Canadian); Perth, Melbourne and
# Sydney elsewhere ("Perth, Scotland", "Melbourne, FL") resolve to their own country because the rest of the place
# string is checked too.
AUSTRALIA_RE = re.compile(
    r"\bAustralia\b|\bAustralian Capital Territory\b|\bSydney\b|\bMelbourne\b|\bBrisbane\b|\bPerth\b|"
    r"\bAdelaide\b|\bCanberra\b|\bGold Coast\b|\bNew South Wales\b|\bNSW\b|\bQueensland\b|\bQLD\b|"
    r"\bTasmania\b|\bACT\b|\bVIC\b|\bNorthern Territory\b|\bVictoria,? (?:AU|Australia)\b")
SPLIT = re.compile(r"\s*(?:;|\||/|(?<!,)(?<!, )\b(?:or|OR|Or)\b)\s*")
AREAS = ("europe", "americas", "australia", "oceania", "asia_africa")


def _hint(hints, text):
    return any(re.search(r"\b" + re.escape(h) + r"\b", text) for h in hints)


def _base_area(place, extra=None):
    loc = place or ""
    for w in ASIA_AFRICA + list((extra or {}).get("asia_africa", [])):
        if w == "Georgia (country)":
            if re.search(r"Tbilisi|, Georgia$", loc) and "United States" not in loc:
                return "asia_africa"
            continue
        if re.search(r"\b" + re.escape(w) + r"\b", loc):
            return "asia_africa"
    extra = extra or {}
    for name, hints in (("europe", EUROPE_HINTS), ("americas", AMERICAS_HINTS), ("oceania", OCEANIA_HINTS)):
        if _hint(hints + list(extra.get(name, [])), loc):
            return name
    if US_RE.search(loc) or re.search(r", [A-Z][a-z]+(?: [A-Z][a-z]+)?, United States|, [A-Z]{2}$", loc):
        return "americas"
    return "unknown"


def area_of(place, extra=None):
    """Area of one place. Australian when it names Australia, a state / territory or a main city AND nothing
    else in it resolves elsewhere."""
    if AUSTRALIA_RE.search(place or ""):
        rest = AUSTRALIA_RE.sub(" ", place)
        if _base_area(rest, extra) == "unknown":
            return "australia"
    return _base_area(place, extra)


def places(location):
    return [p.strip() for p in SPLIT.split(location or "") if p and p.strip()]


def classify_region(location, extra=None):
    """Best area among the listed places: europe > americas > oceania > australia > unknown > asia_africa."""
    found = {area_of(p, extra) for p in places(location)} or {"unknown"}
    for name in ("europe", "americas", "oceania", "australia", "unknown"):
        if name in found:
            return name
    return "asia_africa"


def is_multi_place(location):
    return len(places(location)) > 1


def region_reason(location, blocked=(), relocation_required=(), offers_relocation=False, extra=None):
    """'geo: <area>' or 'geo: <area>, no relocation' when the location rules the posting out, else None."""
    areas = [area_of(p, extra) for p in places(location)]
    if not areas:
        return None
    blocked, reloc = set(blocked), set(relocation_required)
    if all(a in blocked for a in areas):
        return f"geo: {areas[0]}"
    if not offers_relocation and all(a in blocked | reloc for a in areas):
        first = next(a for a in areas if a in reloc)
        return f"geo: {first}, no relocation"
    return None
