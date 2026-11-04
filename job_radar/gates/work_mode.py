"""Work mode: remote / hybrid / office / unknown, from explicit phrases only.

Evidence, in order of use:
  1. the posting text (description) and the location string: explicit phrases such as "fully remote",
     "remote-first", "hybrid working model", "on-site role", "office first", "3 days a week in the office";
     a city in the location is NOT evidence of anything;
  2. the `workplace` field (the employer's own workplace type from an ATS feed), used only when the text
     gives no verdict.
If the text names more than one mode ("remote, hybrid or office-based") the verdict is unknown.

    detect(description, location, workplace=None) -> (mode, evidence)
"""
import html
import re

_END = r"(?=$|[,;·|•)\]\-–—:/.\s])"

HYBRID = re.compile(
    r"\bhybrid[- ](?:work(?:ing)?|role|model|setup|set-up|schedule|position|policy|environment|arrangement|basis|"
    r"mode|approach|culture|office|team|remote|from|in)\b"
    r"|\b(?:work(?:ing)?|workplace|location|model|type|setup|policy|arrangement|schedule|role|position|is|are|be)\s*(?:model|type|policy|arrangement|setup)?\s*[:\-–—]?\s*hybrid(?!\s+(?:cloud|product|system|solution|infrastructure|app|platform|data))" + _END +
    r"|\b(?:offer|offers|operate|operates)\s+(?:a\s+)?hybrid\s*(?:\.|$)"
    r"|\boffice[- ]based\s+(?:\d|one|two|three|four)\s*days?\s*(?:a|per|each)\s*week\b(?=[^.\n]{0,60}\b(?:wfh\b|(?:a|one|two|three|\d)\s*days?\s*(?:a\s*week\s*)?(?:(?:working|work)\s+)?(?:from\s+|at\s+)?(?:home|remote(?:ly)?)\b|(?:the\s+)?(?:rest|remainder|remaining)\b[^.\n]{0,30}\b(?:home|remote(?:ly)?)\b))"
    r"|[,;·|•(\[–—]\s*hybrid\s*(?:[,;·|•)\]–—]|$)"
    r"|\b(?:\d|one|two|three|four)(?:\s*(?:-|to|or)\s*(?:\d|one|two|three|four))?\s*days?\s*(?:a|per|/|each)\s*week\s*"
    r"(?:in|at|from)\s*(?:the|our|an?)?\s*(?:\w+\s+)?office"
    r"|\bin[- ]office\s*(?:\d|one|two|three|four)\s*days?"
    r"|\b(?:\d|one|two|three|four)\s*days?\s*(?:a|per|each)\s*week\s*in[- ]office"
    r"|\bminimum of (?:\d|two|three|one|four) days? (?:per|a) week in the office", re.I)

REMOTE = re.compile(
    r"\bfully[- ]remote\b|\b100\s?%\s*remote\b|\bremote[- ]first\b|\ball[- ]remote\b|\bremote[- ]only\b"
    r"|\bwork(?:ing)? (?:fully |completely |entirely )?remotely\b|\bwork from anywhere\b|#LI-Remote\b"
    r"|\b(?:this|the) (?:role|position|job|opportunity) is (?:a )?(?:fully |100\s?% )?remote\b"
    r"|\bremote (?:role|position|job|opportunity)\b"
    r"|\b(?:location|work(?:ing)? (?:model|type|location|arrangement)|workplace|work mode)\s*[:\-–—]\s*remote\b"
    r"|[,;·|•(\[–—]\s*remote\s*(?:[,;·|•)\]–—]|$)", re.I)

OFFICE = re.compile(
    r"\b(?:this|the|an?) (?:is an? )?(?:fully )?on-?site (?:role|position|job)\b|\b(?:role|position|job) is (?:fully )?(?:on-?site|office[- ]based)\b(?!\s+(?:\d|one|two|three|four)\s*days?)|"
    r"\boffice[- ]based\b(?!\s+(?:\d|one|two|three|four)\s*days?\s*(?:a|per|each)\s*week|\s+(?:at|in|on|near)\b)|\bthis is an? on-?site\b"
    r"|\bon-?site[- ]only\b|\bfully on-?site\b|\bfull[- ]time (?:on-?site|in[- ]office)\b"
    r"|\bon-?site (?:work|presence|requirement|based)\b|\bon-?site \d days\b|\bonsite \w+ days\b"
    r"|\bin[- ]office (?:role|position|requirement|presence|only)\b|\boffice[- ]first\b|\boffice[- ]based (?:role|position)\b"
    r"|\bwork(?:ing)? (?:from|in) (?:the|our) (?:\w+ )?office (?:full|five|5)\b|\b5 days (?:a|per) week in (?:the|our) office\b"
    r"|\b(?:location|work(?:ing)? (?:model|type|location|arrangement)|workplace)\s*[:\-–—]\s*on-?site\b"
    r"|[,;·|•(\[–—]\s*on-?site\s*(?:[,;·|•)\]–—]|$)", re.I)

# "a hybrid and remote model", "Remote, hybrid, or office-based work": a list of options, not a verdict
OPTION_LIST = re.compile(r"\b(?:remote|hybrid|on-?site|office[- ]based)\b[^.\n]{0,25}\b(?:or|and|/)\b[^.\n]{0,25}"
                         r"\b(?:remote|hybrid|on-?site|office[- ]based)\b", re.I)

OFFICE_DAYS = re.compile(r"\boffice[- ]based\s+(?:\d|one|two|three|four)\s*days?\s*(?:a|per|each)\s*week", re.I)

FIELD = {"remote": "remote", "hybrid": "hybrid", "on-site": "office", "on_site": "office", "on site": "office",
         "onsite": "office", "office": "office"}


def _clean(text):
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"[ \t\r\f\v\u00a0]+", " ", t)


def detect(description, location=None, workplace=None):
    text = _clean(description) + "\n" + (location or "")
    found = {}
    spans = {}
    for mode, rx in (("hybrid", HYBRID), ("remote", REMOTE), ("office", OFFICE)):
        if mode == "remote":
            # the remote day inside "office based 4 days a week and one day working remotely" is part of the
            # hybrid schedule, not a second mode: skip a "working remotely" that follows it in the same sentence
            hs = spans.get("hybrid")
            ms = [x for x in rx.finditer(text) if not (
                hs and text[hs[0]:hs[0] + 6].lower() == "office" and x.start() >= hs[1]
                and re.fullmatch(r"work(?:ing)? remotely", x.group(0), re.I) and "." not in text[hs[1]:x.start()])]
            m = ms[0] if ms else None
        else:
            m = rx.search(text)
        if m:
            found[mode] = m.group(0).strip()
            spans[mode] = m.span()
    # a fixed office schedule next to a remote phrase that is not its remote day is a conflict, not "remote"
    if "hybrid" not in found and "remote" in found and OFFICE_DAYS.search(text):
        found["office"] = "office based schedule"
    loc = (location or "").lower()
    if re.search(r"\bremote\b", loc):
        found.setdefault("remote", "location: " + location)
    if re.search(r"\bhybrid\b", loc):
        found.setdefault("hybrid", "location: " + location)
    if len(found) == 1 and OPTION_LIST.search(text):
        found = {}
    if len(found) == 1:
        mode, ev = next(iter(found.items()))
        return mode, ev[:80]
    if len(found) > 1:
        return "unknown", "mixed: " + ", ".join(sorted(found))
    w = FIELD.get((workplace or "").strip().lower())
    if w:
        return w, "workplace field: " + workplace
    return "unknown", ""

