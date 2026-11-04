"""Language gate: flag postings that REQUIRE a language the reader does not speak.

A language that is only "a plus" is not a requirement. Deterministic, no model calls.

    detect(description, spoken=("English",)) -> (lang_req, lang_evidence)

lang_req is a comma-separated list such as "German" or "German, French" (None when clean);
lang_evidence is the matched phrase, at most 120 characters.

Two rules; evidence comes from the first that fires:
  1. A clause asks for the language: a proficiency word next to the language name ("fluent German",
     "German (C1)", "German speaker"), or a native-language form ("Deutschkenntnisse", "français courant").
     A clause with a nice-to-have marker ("a plus", "is an advantage", "von Vorteil") does not block,
     and neither does "English or German".
  2. The description itself is mostly written in another language (stop-word count).
"""
import re

DEFAULT_SPOKEN = ("English",)
EVIDENCE_MAX = 120
MIN_STOP = 8

# English name (regex) and native forms that name the language on their own.
LANGS = {
    "German": (r"german", r"deutsch|deutschkenntnisse|deutsche[nr]? sprache|deutschsprachig\w*"),
    "French": (r"french", r"fran[cç]ais|langue fran[cç]aise|francophone"),
    "Dutch": (r"dutch|flemish", r"nederlands(?:talig)?|vlaams"),
    "Spanish": (r"spanish|castilian", r"espa[nñ]ol|castellano|hispanohablante"),
    "Italian": (r"italian", r"italiano|lingua italiana|madrelingua italiana"),
    "Polish": (r"polish", r"j[eę]zyk(?:a|iem)? polski(?:ego|m)?|polskiego"),
    "Swedish": (r"swedish", r"svenska"),
    "Danish": (r"danish", r"dansk"),
    "Norwegian": (r"norwegian", r"norsk"),
    "Finnish": (r"finnish", r"suomi|suomen kiel\w*"),
    "Portuguese": (r"portuguese", r"portugu[eê]s"),
    "Czech": (r"czech", r"[čc]e[šs]tin[aouěe]|[čc]esk[ýy] jazyk"),
    # Further languages: same shapes, English name plus a proficiency word only.
    "Hungarian": (r"hungarian", r"magyar"),
    "Romanian": (r"romanian", r"rom[aâ]n[aă]"),
    "Greek": (r"greek", r"ελληνικ\w*"),
    "Turkish": (r"turkish", r"t[üu]rk[çc]e"),
    "Ukrainian": (r"ukrainian", r"\u0443\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\w*"),
    "Serbo-Croatian": (r"serb\w*|croatian|bosnian|montenegrin", r"srpsk\w*|hrvatsk\w*"),
    "Slovak": (r"slovak", r"sloven[čc]in\w*"),
    "Bulgarian": (r"bulgarian", r"\u0431\u044a\u043b\u0433\u0430\u0440\u0441\u043a\u0438"),
    "Baltic": (r"estonian|latvian|lithuanian", r"eesti keel\w*|latvie[šs]u valod\w*|lietuvi\w* kalb\w*"),
    "Hebrew": (r"hebrew", r"עברית"),
    "Arabic": (r"arabic", r"العربية"),
    "Chinese": (r"chinese|mandarin|cantonese", r"中文|普通话"),
    "Japanese": (r"japanese", r"日本語"),
    "Korean": (r"korean", r"한국어"),
    "Hindi": (r"hindi", r"हिन्दी"),
    "Russian": (r"russian", r"\u0440\u0443\u0441\u0441\u043a\w*"),
}
# Native forms that are safe on their own; the rest need a proficiency word in the clause
# ("Description Français:" heads the French half of bilingual Canadian postings, and
# "svenska", "dansk", "norsk" appear in company names).
SELF_EVIDENT = {"German"}

PRE = (r"fluent(?:ly)?|fluency|native|mother[- ]tongue|bilingual|business[- ](?:level|fluent|fluency|proficien\w+)"
       r"|proficient|proficiency|command|knowledge|(?:full )?professional(?: working)? (?:proficiency|fluency|level)"
       r"|excellent|very good|strong|good|perfect|advanced|c1|c2|b2|(?:written|spoken|verbal) and (?:written|spoken|verbal)"
       r"|you speak|speak(?:s|ing)? fluent|must speak|able to speak|ability to speak")
POST = (r"\(?\s*(?:c1|c2|b2)\b|fluent|fluency|native|mother[- ]tongue|proficiency|proficient|skills|language"
        r"|speaker|speaking and writing|written and spoken|spoken and written|required|mandatory|is a must"
        r"|at (?:a )?(?:business|professional|native|c1|c2|b2)\b")
# "German market", "German-speaking region": context, not a requirement.
CONTEXT_AFTER = (r"(?:[- ]speaking)?[- ](?:market|markets|region|regions|countries|country|area|clients|customers"
                 r"|companies|company|law|tax|startups?|ecosystem|office|entity|entities|gaap|accounting|legal"
                 r"|employment|labou?r|dach|economy|mittelstand|smes?|b2b|saas|brands?|retail|audience|users"
                 r"|language (?:learners|content|markets?))\b")
# "German-speaking Switzerland", "Spanish-speaking roles": geography, not a requirement.
SPEAKING_CONTEXT = (r"[- ](?:market|region|countries|world|roles?|positions?|switzerland|belgium|canada|europe"
                    r"|latam|america|parts?|cantons?|areas?|territor\w+)")
# Weak PRE words only count when the language is followed by a skill noun.
WEAK_PRE = {"excellent", "very good", "strong", "good", "perfect", "advanced", "knowledge", "command"}
LOCAL_MOD = (r"verhandlungssicher\w*|flie(?:ß|ss)end\w*|muttersprach\w*|sehr gute\w*|kenntnisse|in wort und schrift"
             r"|courant|natif|native|maîtrise|maitrise|vloeiend|moedertaal|nativo|fluido|madrelingua|biegł\w*"
             r"|flytande|flydende|flytende|sujuva\w*|plynul\w*|rodil\w*|c1|c2|b2")
NICE = (r"\b(?:a|strong|big|huge|great|real|definite|added|major) plus\b|\bis a plus|\basset\b|good to have|\bplus point|\bbonus\b|nice[- ]to[- ]have|\badvantage(?:ous)?\b|\bpreferred\b"
        r"|\bdesirable\b|\bdesired\b|\bbeneficial\b|\bwelcome\b|\bappreciated\b|\bideally\b|\bhelpful\b"
        r"|von vorteil|w[üu]nschenswert|idealerweise|ein plus|\bgerne\b|un plus|un atout|est un atout|pluspunt"
        r"|een pr[ée]\b|valorable|se valora|deseable|gradito|costituisce|mile widziana|meriter|f[öo]rdel"
        r"|speakers? (?:on|in) (?:the|our) team|\bif (?:applying|based|you are based|located)|where applicable|if applicable")
EN_OR = r"\benglish\s*(?:or|and/or|/)\s*(?:\w+\s+){0,2}?%s|%s\s*(?:or|and/or|/)\s*(?:\w+\s+){0,2}?english\b"

STOP = {
    "English": "the and of to in for with you our we are is a an on as be will this that your at by from or have".split(),
    "German": "und der die das mit für wir sie ist ein eine den dem zu auf bei von uns unsere nicht auch oder als du dich deine ihre".split(),
    "French": "et le la les des une un pour vous nous est avec dans du au aux sur votre nos sont en".split(),
    "Dutch": "en het een van voor je jouw wij we zijn met op te bij ons onze niet ook dat als".split(),
    "Spanish": "y el la los las de del para con una un en por nuestro nuestra tu es somos que como".split(),
    "Italian": "e il la gli le di del per con una un nel della sono che come nostro nostra tu".split(),
    "Polish": "i w z na do się jest oraz dla nie że jak nasz nasza twoje który która".split(),
    "Swedish": "och att det en ett för med som på är vi du oss vår dig till av".split(),
    "Danish": "og at det en et for med som på er vi du os vores dig til af".split(),
    "Norwegian": "og at det en et for med som på er vi du oss vår deg til av".split(),
    "Finnish": "ja on että sinä me meidän kanssa tai kun sekä myös joka".split(),
    "Portuguese": "e o os as de do da dos das para com uma um em por nosso nossa você é que como".split(),
    "Czech": "a v je na se s pro že jako nebo také být jsme vás naše".split(),
}
STOP_SETS = {k: set(v) for k, v in STOP.items()}
ANY_LANG = re.compile(r"\b(?:%s)" % "|".join(f"{en}|{native}" for en, native in LANGS.values()))

_WORD = re.compile(r"[^\W\d_]+", re.U)


def written_in(desc, spoken=DEFAULT_SPOKEN):
    """(language, evidence) when the description is mostly in a language the reader does not speak."""
    words = [w.lower() for w in _WORD.findall(desc or "")]
    if len(words) < 30:
        return None
    counts = {lang: sum(w in s for w in words) for lang, s in STOP_SETS.items()}
    en = counts.pop("English")
    lang, top = max(counts.items(), key=lambda kv: kv[1])
    if top >= MIN_STOP and top > en and lang not in spoken:
        pct = lambda n: round(100 * n / len(words))
        return lang, f"description written in {lang} ({lang} stop words {pct(top)}%, English {pct(en)}%)"
    return None


def clauses(desc):
    for line in re.split(r"\n+", desc or ""):
        for part in re.split(r"(?<=[.!?;])\s+|;\s*|\s+[–—]\s+|\s\|\s", line):
            part = part.strip(" -•*\t")
            if part:
                yield part


def _snippet(clause, m):
    if len(clause) <= EVIDENCE_MAX:
        return clause
    start = max(0, min(m.start() - 40, len(clause) - EVIDENCE_MAX))
    return clause[start:start + EVIDENCE_MAX].strip()


def _required_in(clause):
    """[(language, evidence)] required in one clause."""
    low = clause.lower()
    if not ANY_LANG.search(low) or re.search(NICE, low):
        return []
    out = []
    for lang, (en, native) in LANGS.items():
        if not re.search(rf"\b(?:{en}|{native})", low):
            continue
        name = rf"(?<!non-)(?<!non )\b(?:{en})\b(?!{CONTEXT_AFTER})"
        if re.search(EN_OR % (rf"(?:{en}|{native})", rf"(?:{en}|{native})"), low):
            continue
        m = None
        pre = re.search(rf"\b({PRE})\b(?:[\s,/-]+(?:in|of|or|and|level|c1|c2|b2|english|russian|\w+-level)){{0,4}}[\s,/-]+{name}", low)
        if pre:
            weak = pre.group(1) in WEAK_PRE
            after = low[pre.end():pre.end() + 30]
            if not weak or re.match(r"[\s-]*(?:language|skills|proficiency|\(?\s*c1|\(?\s*c2|\(?\s*b2|speaker)", after):
                m = pre
        if not m:
            m = re.search(rf"{name}\s*(?:[:(,-]\s*|\s)(?:(?:language|speaking|and|english|level|is|at|a|an)\s+){{0,3}}(?:{POST})", low)
        if not m:
            m = re.search(rf"\b(?:{en})[- ]speaking\b(?!{CONTEXT_AFTER}|{SPEAKING_CONTEXT})", low)
        if not m:
            nat = re.search(rf"\b(?:{native})\b", low)
            if nat and (lang in SELF_EVIDENT or re.search(rf"\b(?:{LOCAL_MOD})", low)):
                m = nat
        if m:
            out.append((lang, _snippet(clause, m)))
    return out


def detect(desc, spoken=DEFAULT_SPOKEN):
    """(lang_req, lang_evidence) or (None, None). Languages in `spoken` are never reported."""
    if not desc:
        return None, None
    spoken = set(spoken)
    found = {}
    for clause in clauses(desc):
        for lang, ev in _required_in(clause):
            if lang not in spoken:
                found.setdefault(lang, ev)
    w = written_in(desc, spoken)
    if w:
        found.setdefault(w[0], w[1])
    if not found:
        return None, None
    langs = list(found)
    return ", ".join(langs), found[langs[0]][:EVIDENCE_MAX]

