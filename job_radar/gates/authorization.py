"""Relocation / visa flags and the work-authorisation blocker, both read from the description.

    flags(description) -> (relocation_flag, visa_flag): 1 = offered, -1 = explicitly not offered, 0 = not mentioned
    blocker(description) -> ("work authorization" | "no sponsorship", phrase) or (None, None)
    offers_relocation(relocation_flag, visa_flag, description) -> bool
"""
import re

RELOC_POS = re.compile(r"relocat|visa sponsor|sponsor(?:ship)? (?:for |of )?(?:a |your )?(?:work )?visa|"
                       r"we (?:can |will |do )?sponsor|work permit (?:support|sponsor)|"
                       r"(?:skilled worker|blue card|work) visa", re.I)
# Negation in any word order: "do not offer visa sponsorship", "visa sponsorship is not available",
# "we are unable to sponsor", "sponsorship unavailable". A negative match always beats a positive one (see flags()).
_NEGW = r"\b(?:no|not|never|unable to|cannot|can't|can not|do not|does not|don't|doesn't|won't|will not|" \
        r"are not able to|is not able to|not able to|not in a position to)"
VISA_NEG = re.compile(
    _NEGW + r" (?:currently |able to |be able to )?(?:(?:offer(?:ing)? |provid(?:e|ing) |support(?:ing)? )(?:any |a )?)?"
            r"(?:any )?(?:(?:visa |work permit |immigration )?sponsor|(?:visa|work permit|immigration) support)|"
    r"(?:sponsor(?:ship)?|visa support|work permit|immigration support)(?: (?!may\b|might\b|could\b)\w+){0,3}? (?:is |are |will be |would be )?"
    r"(?:currently |generally )?(?:(?:not|never|n't|un)\s?|(?:(?:is|are|was|were)n't|cannot|can't|can not|won't|will not)(?: currently)?(?: be)? )(?:be )?(?:available|offered|provided|possible|supported|on offer|an option)|"
    r"sponsor(?:ship)? (?:is |are )?(?:currently )?unavailable|"
    r"(?:unable|not able) to (?:offer|provide) (?:any )?(?:visa|work permit|sponsor)|"
    r"without (?:the need for )?(?:visa |any )?sponsorship|must (?:already )?have (?:the )?right to work|"
    r"authori[sz]ed to work in", re.I)
RELOC_NEG = re.compile(
    _NEGW + r" (?:able to )?(?:offer |provide |support )?(?:any )?relocation|"
    r"relocation(?: (?!may\b|might\b|could\b)\w+){0,3}? (?:is |are |will be )?(?:currently )?(?:(?:not|never|n't|un)\s?|(?:(?:is|are|was|were)n't|cannot|can't|can not|won't|will not)(?: currently)?(?: be)? )(?:be )?"
    r"(?:available|offered|provided|possible|supported|on offer)|relocation (?:is )?unavailable", re.I)



def flags(desc):
    """relocation_flag / visa_flag: 1 = offered, -1 = explicitly not offered, 0 = not mentioned."""
    if not desc:
        return None, None
    visa_neg = bool(VISA_NEG.search(desc))
    visa_pos = bool(re.search(r"visa sponsor|sponsor(?:ship)? (?:for |of )?(?:a |your )?(?:work )?visa|"
                              r"we (?:can |will |do )?sponsor|blue card|skilled worker visa", desc, re.I))
    reloc_word = bool(re.search(r"relocat", desc, re.I))
    reloc_neg = bool(RELOC_NEG.search(desc))
    visa = -1 if visa_neg else (1 if visa_pos else 0)
    if reloc_word and not reloc_neg:
        reloc = 1
    elif visa == 1:
        reloc = 1
    elif reloc_neg or visa_neg:
        reloc = -1
    else:
        reloc = 0
    return reloc, visa



def offers_relocation(relocation_flag, visa_flag, description):
    """True when a stored flag is 1. A row without flags is judged on its description with `flags()`;
    no description and no flags counts as not offering."""
    if relocation_flag is None and visa_flag is None:
        relocation_flag, visa_flag = flags(description)
    return relocation_flag == 1 or visa_flag == 1


# Each pattern names an explicit statement: existing work authorisation / right to work in a country, or no
# sponsorship. Nothing implicit (a city, a time zone) ever counts.
BLOCKER_AUTH = re.compile(
    r"(?:must|need to|needs to|required to|requires?|requirement|have to|ability|able to|already|currently|"
    r"candidates|applicants|you are|you're)\b[^.\n]{0,60}?"
    r"(?:right to work|legally (?:entitled|authori[sz]ed|permitted|eligible)|authori[sz]ed to work|"
    r"eligible to work|permitted to work|entitled to work|legal(?:ly)? (?:able )?to work)"
    r"\s+(?:in|within)\s+(?:the\s+)?[A-Z][\w.&-]*(?:\s+[A-Z][\w.&-]*)?|"
    r"work authori[sz]ation (?:is )?(?:required|needed)|"
    r"(?:valid|current|existing|unrestricted|full) (?:work (?:permit|authori[sz]ation|visa)|right to work)|"
    r"proof of (?:your )?(?:legal )?right to work", re.I)
BLOCKER_NOSPONSOR = re.compile(
    r"(?:no|not|unable to|cannot|can't|can not|do not|don't|won't|will not|are not able to|is not able to) "
    r"(?:currently )?(?:offer(?:ing)? |provid(?:e|ing) |be able to |in a position to )?(?:visa )?sponsor|"
    r"sponsorship (?:is )?(?:not|un)available|without (?:the need for )?(?:visa )?sponsorship|"
    r"(?:no|without) visa (?:support|sponsorship)", re.I)


def blocker(desc):
    """(kind, matched phrase) for the first explicit work-authorisation or no-sponsorship statement
    in the description, else (None, None). The phrase is stored so it can be audited."""
    if not desc:
        return None, None
    for kind, rx in (("work authorization", BLOCKER_AUTH), ("no sponsorship", BLOCKER_NOSPONSOR)):
        m = rx.search(desc)
        if m:
            return kind, re.sub(r"\s+", " ", m.group(0)).strip()[:120]
    return None, None

