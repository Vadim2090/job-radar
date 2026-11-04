"""Role-family gate: titles that pass the title match but name a role the profile does not target.

Built-in families, checked in this order (each one can be switched off in config):

  president   "president" anywhere in the title (President, Vice President).
  salesforce  Salesforce or SFDC in the title, or a description that lists Salesforce as a required
              qualification (see `salesforce_required`).
  recruiter   recruiter, talent acquisition, sourcer, headhunter.
  junior      working student, intern, trainee, apprentice, junior, entry level, graduate.
  ops         sales ops, finance / product ops, and RevOps / marketing operations below lead level.
              An ops lead, manager or head is kept; a GTM Engineer is kept.
  anonymous   a hidden employer ("Confidential", "Our client").
  engineer    software / AI / data / platform engineers, systems / solutions architects, systems analysts.
              A GTM / Growth / Marketing Engineer is kept.
  sales       quota-carrying sales roles and sales leadership, by title or by a quota-heavy description.
  gm          Managing Director, General / Country Manager, Corporate Strategy, M&A, Chief of Staff
              (kept when the title names growth or marketing AND the description is about marketing).
  d2c         direct-to-consumer / e-commerce brands.
  retail      retailers, supermarkets, grocers, consumer-goods companies.
  mobile_app  the job is to build a mobile app (title word, or the company's own product is an app).

Leadership rule: a title with a growth / marketing / GTM word AND a leadership word (Head, Director, VP,
Lead...) skips the title-only families (recruiter, junior, ops) unless it names the excluded role itself
("Lead Recruiter", "Marketing Intern"). The company, industry and description families (anonymous,
engineer, sales, gm, d2c, retail, mobile_app) still apply to a leadership title.

    role_family(title, company=None, description=None, industries=None, families=None) -> name or None
"""
import html
import re

FAMILIES = ("president", "salesforce", "recruiter", "junior", "ops", "anonymous", "engineer", "sales", "gm",
            "d2c", "retail", "mobile_app")

RECRUITER = re.compile(r"\b(recruiters?|recruiting|recruitment|talent acquisition|talent sourcer|sourcers?|"
                       r"headhunters?|talent partner)\b", re.I)
JUNIOR = re.compile(r"\b(working students?|werkstudent(?:in)?|interns?|internships?|praktikum|praktikant(?:in)?|"
                    r"trainees?|apprentices?|apprenticeships?|junior|jr|entry[- ]level|new[- ]grads?|graduates?)\b", re.I)
# family word, up to two bounded words ("Strategy", "Performance"), optional "&" / "and", then operations / ops
OPS = re.compile(r"\brev ?ops\b|\brevenue operations\b|"
                 r"\b(?:marketing|sales|gtm)\b(?:\s+[\w-]+){0,2}?\s*(?:&|and)?\s*(?:operations|ops)\b", re.I)
LEADERSHIP = re.compile(r"\b(head|director|vp|vice president|chief|cmo|lead)\b", re.I)
TARGET_WORD = re.compile(r"growth|marketing|\bgtm\b|go[- ]to[- ]market", re.I)
# Nouns that name the excluded role itself: a leadership word next to one ("Lead Recruiter") does not rescue
# the title. "Recruitment Marketing Lead" and "Head of Growth - Graduate Program" stay (a marketing role, a
# programme name); "Marketing Recruitment Lead" and "Graduate Marketing Lead" go.
HARD_ROLE = re.compile(r"\b(recruiters?|sourcers?|headhunters?|interns?|internships?|working students?|werkstudent(?:in)?|"
                       r"praktikum|praktikant(?:in)?|trainees?|apprentices?|apprenticeships?|junior|jr|"
                       r"talent acquisition|talent partners?|talent sourcer|"
                       r"recruit(?:ment|ing)\s+(?:leads?|managers?|partners?|specialists?|coordinators?|heads?|directors?)|"
                       r"entry[- ]level|(?:new[- ]grads?|graduates?)(?!\s+(?:programs?|programmes?|schemes?|pathways?|academy|hiring|recruiting|recruitment)))\b", re.I)
GTM_ENGINEER = re.compile(r"\bgtm engineer", re.I)
# A RevOps / marketing-ops title at lead, manager or head level is a target, composite titles ("GTM Strategy &
# Operations Lead") included; sales ops, IC titles and finance / product ops stay out.
OPS_LEVEL = re.compile(r"\b(head|director|vp|vice president|lead|manager)\b", re.I)
# "sales" anywhere in an ops title is sales ops; pre- / post-sales is a customer-lifecycle scope and stays.
OPS_OUT = re.compile(r"(?<!pre[- ])(?<!post[- ])\bsales\b|\b(specialists?|analysts?|associates?|coordinators?|administrators?|admins?|"
                     r"assistants?|engineers?|developers?|consultants?|partners?|representatives?|"
                     r"financ\w*|fp\s*&\s*a|accounting|(?:product|platform) managers?)\b", re.I)
SALESFORCE = re.compile(r"\bsalesforce\b|\bsfdc\b", re.I)

# engineer: a technical qualifier (up to two words) before engineer / developer, or a systems-type architect /
# analyst. GTM, Growth and Marketing Engineers are target roles and are checked first.
PROFILE_ENGINEER = re.compile(r"\b(?:(?:gtm|go[- ]to[- ]market)(?:\s+(?:ai|automation|ai[- ]native))?|growth|marketing|revenue|demand gen\w*)\s+engineers?\b", re.I)
TECH_QUALIFIER = (r"(?:ai|a\.i\.|ml|llm|genai|gen ai|machine learning|deep learning|data|software|backend|back-end|front-?end|"
                  r"full[- ]?stack|platform|infrastructure|devops|sre|security|cloud|automation|agentic|applied|"
                  r"computer vision|nlp|mlops|embedded|firmware|ios|android|mobile|web|api|integrations?|solutions?|"
                  r"sales|systems?|gtm systems?)")
ENGINEER = re.compile(r"\b" + TECH_QUALIFIER + r"(?:\s+[\w/&.+-]+){0,2}?\s+(?:engineers?|developers?|programmers?)\b|"
                      r"\b(?:software|data|ml|ai)\s+(?:engineering|development)\s+(?:manager|lead)\b|"
                      r"\b(?:systems?|solutions?|technical|software|enterprise|data|cloud|platform|infrastructure|security|"
                      r"integrations?|gtm systems?)\s+architects?\b|"
                      r"\b(?:business systems?|systems?|gtm systems?|technical|it)\s+analysts?\b|"
                      r"\bgtm systems?\b.*\b(?:engineers?|architects?|analysts?|developers?)\b", re.I)

# IC sales and development titles: never a target, even next to a marketing word
SALES_IC = re.compile(r"\baccount executives?\b|\b(?:sdr|bdr)\b|\bsales (?:representatives?|reps?|executives?|engineers?)\b|"
                      r"\b(?:inside|outside) sales\b", re.I)
# business / sales development managers: IC sales unless the title also names marketing
SALES_BD = re.compile(r"\bsales development (?:representatives?|reps?|managers?|executives?)\b|\bmanager,? sales development\b|"
                      r"\bbusiness development (?:representatives?|managers?|executives?)\b", re.I)
# sales leadership; kept when the title also names marketing or GTM
SALES_LEAD = re.compile(r"\b(?:vp|vice president|director|head|chief|svp|evp)\b[\w ,&/-]*?\bsales\b|\bsales (?:director|manager|leader|head|lead)s?\b|"
                        r"\bchief (?:revenue|sales) officer\b|"
                        r"\b(?:regional|area|country|territory) (?:vice president|vp|svp|director|manager)\b(?=[^|]*?"
                        r"\b(?:enterprise|sales|commercial|strategic|corporate|mid[- ]?market|smb|public sector|accounts?)\b)|"
                        r"\b(?:enterprise|commercial|strategic|corporate|mid[- ]?market|smb)\s+sales\b", re.I)
MARKETING_WORD = re.compile(r"marketing|demand gen|\bbrand|content|lifecycle|\bseo\b|\bsem\b|paid (?:media|social|search)|"
                            r"performance marketing|communications|\bpr\b|community|acquisition|activation|retention", re.I)
SALES_TERMS = [   # (regex, weight): each phrase counts at most 3 hits
    (re.compile(r"\bquota\b|\bquotas\b", re.I), 2), (re.compile(r"\bbookings?\b", re.I), 2),
    (re.compile(r"\baccount executives?\b|\baes\b", re.I), 1),
    (re.compile(r"\bsales (?:team|organi[sz]ation|leaders?|leadership|cycle|forecast\w*)\b", re.I), 1),
    (re.compile(r"\bclose[- ]won\b|\bclosing deals\b|\bclose deals\b|\bnew logos?\b|\bterritor(?:y|ies)\b", re.I), 1),
    (re.compile(r"\bsecond[- ]line\b|\bsales managers\b", re.I), 2)]
MARKETING_TERMS = re.compile(r"\b(?:marketing|demand gen\w*|brand\w*|campaigns?|content|seo|paid (?:media|social|search)|"
                             r"lifecycle|email marketing|growth marketing|funnel|mql|acquisition channels?)\b", re.I)
SALES_THRESHOLD = 8          # weighted sales score needed for the description rule
SALES_DOMINANCE = 2          # and the sales score must be at least this many times the marketing-word count
SALES_DESC_TITLE = re.compile(r"growth|commercial|customer|client", re.I)   # titles the description rule may judge
PRODUCT_WORD = re.compile(r"\bproduct\b", re.I)
GROWTH_OR_MARKETING = re.compile(r"growth|marketing", re.I)
MARKETING_HITS_FOR_GM = 5       # a gm title that names growth stays when the description has this many marketing words
MARKETING_HITS_FOR_GM_MKT = 2   # ... and this many when the title itself names marketing

GM_TITLE = re.compile(r"\bmanaging directors?\b|\bgeneral managers?\b|\bcountry (?:manager|head|director|lead|general manager)\b|"
                      r"\bcorporate strategy\b|\bcorporate development\b|\bcorp(?:orate)? dev\b|\bm\s*&\s*a\b|"
                      r"\bmergers? (?:&|and) acquisitions?\b|\bchief of staff\b|\bcorporate finance\b", re.I)
ANON_COMPANY = re.compile(r"\bconfidential\b|\bconfidencial\b|\bconfidentiel(?:le)?\b|\bvertraulich\b|^\s*(?:anonymous(?: employer| company)?|"
                          r"undisclosed(?: company| employer)?|private (?:company|employer|client)|"
                          r"our client|confidential)\s*$", re.I)

# d2c / retail never fire for a software / IT / fintech / marketplace-tech industry: B2B SaaS and consumer apps stay.
TECH_INDUSTRY = re.compile(r"software|it services|technology, information|information services|information technology|computer|"
                           r"financial|banking|insurance|internet marketplace|telecommunications|staffing|human resources|e-learning|"
                           r"saas|payments|fintech|"
                           r"gaming|games|\bapps?\b|mobile|entertainment|streaming|social (?:media|network)|consumer (?:app|software)", re.I)
RETAIL_INDUSTRY = re.compile(r"\bretail\b|online and mail order|supermarket|consumer goods|grocer", re.I)
D2C_TITLE = re.compile(r"\bd2c\b|\bdtc\b|direct[- ]to[- ]consumer|e-?commerce|\bshopify\b|tiktok shop|amazon (?:seller|growth|marketplace|fba)", re.I)
D2C_DESC = re.compile(r"\bd2c\b|\bdtc\b|direct[- ]to[- ]consumer|\bshopify\b|tiktok shop|amazon (?:seller|fba|marketplace seller)|"
                      r"e-?commerce (?:brand|store|business)", re.I)
RETAIL_TITLE = re.compile(r"\bretail(?! media)|\be-?retail|\bgrocer|supermarket|\bfmcg\b|\bclubcard\b|consumer goods", re.I)
RETAIL_DESC = re.compile(r"\bclubcard\b|\bsupermarkets?\b|\bgrocer(?:y|ies)\b|\bin-store\b|\bstore network\b|\bfmcg\b|\bour stores\b", re.I)
D2C_DESC_HITS = 4            # description-only verdict
D2C_DESC_HITS_AGENCY = 5     # agencies name D2C clients; they need more before the text decides
AGENCY_INDUSTRY = re.compile(r"advertising|marketing services|business consulting|online media|public relations|staffing and recruiting|"
                             r"outsourcing|market research", re.I)
PRODUCT_TITLE = re.compile(r"\bproduct (?:manager|owner|lead|director)\b|\bproduct$", re.I)
RETAIL_DESC_HITS = 3
CLIENT_WORD = re.compile(r"\bclients?\b", re.I)
CLIENT_HITS = 3              # an agency posting that says "client" this often is a client-service role, never d2c

PRESIDENT = re.compile(r"president", re.I)

# The job builds a mobile app: a mobile title word, or a description that says the company's own product is an
# app. A B2B tool for app makers stays ("for mobile apps", "app developers", "mobile-first businesses").
MOBILE_TITLE = re.compile(r"\b(?:mobile|ios|android|app store|aso)\b|(?<!web )\bapps?\b", re.I)
MOBILE_PRODUCT = re.compile(r"\b(?:is|are)\s+(?:a|an|the)\s+(?:[\w-]+\s+){0,3}(?:mobile|ios|android)\s+apps?\b|"
                            r"\bour\s+(?:[\w-]+\s+)?(?:mobile|ios|android)\s+apps?\b|"
                            r"\b(?:ios and android|android and ios)\s+apps?\b|"
                            r"\b(?:download|install)\w*\s+(?:our|the)\s+app\b", re.I)
MOBILE_B2B = re.compile(r"\bfor (?:mobile|ios|android) apps?\b|\b(?:mobile )?app (?:developers|publishers|makers|marketers|businesses)\b|"
                        r"\bmobile[- ]first (?:businesses|companies|brands)\b", re.I)


def _clean(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or ""))


def sales_score(description):
    d = _clean(description)
    return sum(w * min(len(rx.findall(d)), 3) for rx, w in SALES_TERMS)


def marketing_hits(description):
    return len(MARKETING_TERMS.findall(_clean(description)))


def sales_description_family(title, description):
    """A growth / customer title with no marketing word whose description is dominated by quota, bookings and AEs."""
    if (not description or MARKETING_WORD.search(title or "") or not SALES_DESC_TITLE.search(title or "")
            or re.search(r"\bgtm\b|go[- ]to[- ]market|\bproduct\b", title or "", re.I)):
        return False
    s = sales_score(description)
    return s >= SALES_THRESHOLD and s >= SALES_DOMINANCE * marketing_hits(description)


def d2c_retail_family(title, industries, description):
    """'d2c' or 'retail' for a consumer-brand or retailer posting, else None. Agencies are judged on the text
    alone, with a higher bar. Without an industry, the text alone never decides d2c: B2B tools for e-commerce
    sellers use the same words."""
    ind, t = industries or "", title or ""
    if TECH_INDUSTRY.search(ind):
        return None
    d = _clean(description)
    desc_hits = len(D2C_DESC.findall(d))
    agency = bool(AGENCY_INDUSTRY.search(ind))
    title_hit = bool(D2C_TITLE.search(t)) and not agency and not (PRODUCT_TITLE.search(t) and not RETAIL_INDUSTRY.search(ind))
    if title_hit and ind:
        return "d2c"
    if title_hit and desc_hits >= 1:      # no industry on the posting: the text must agree with the title
        return "d2c"
    if agency and len(CLIENT_WORD.findall(d)) >= CLIENT_HITS:
        return None
    if ind and not (PRODUCT_TITLE.search(t) and not RETAIL_INDUSTRY.search(ind)) and desc_hits >= (D2C_DESC_HITS_AGENCY if agency else D2C_DESC_HITS):
        return "d2c"
    if agency:
        return None
    # retail wording alone needs a known industry: grocery-delivery marketplaces use the same words
    if RETAIL_INDUSTRY.search(ind) or RETAIL_TITLE.search(t) or (ind and len(RETAIL_DESC.findall(d)) >= RETAIL_DESC_HITS):
        return "retail"
    return None


def mobile_family(t, description=None):
    if MOBILE_TITLE.search(t):
        return "mobile_app"
    # the description rule judges product titles only: a growth seat at an app company is not app development
    if PRODUCT_WORD.search(t) and description and MOBILE_PRODUCT.search(description) and not MOBILE_B2B.search(description):
        return "mobile_app"
    return None


# --- Salesforce as a required qualification -------------------------------------------------------------------
# The description is cut into units (lines, bullets, sentences). A unit is required when it sits under a
# requirements heading or states necessity itself. Nice-to-have wording, an alternative-tool list, a customer /
# partner / integration / background mention, or any other doubt keeps the posting: a false skip loses a real
# posting, a false keep costs one glance. Case-sensitive on purpose: lowercase "salesforce" is often the sales
# team, and other vendors' "Marketing Cloud" is not Salesforce.
SF_TERM = re.compile(r"\b(?:Salesforce|SALESFORCE|SFDC|Pardot|PARDOT|Apex|SOQL)\b|"
                     r"(?<!Adobe )(?<!Oracle )(?<!SAP )(?<!IBM )(?<!Acoustic )\b(?:Sales|Marketing) Cloud\b")
SF_REQ_HEADING = re.compile(r"^(?:(?:key |minimum |basic |required |core |essential )?(?:requirements?|qualifications?|skills(?: (?:&|and) (?:experience|qualifications))?)|"
                            r"must[- ]haves?|what you(?:'|’)?ll (?:need|bring)|what you need|what you bring|what we need|you have|you bring|"
                            r"who you are|about you|your profile|(?:the )?ideal candidate|what we(?:'|’)?re looking for|what we are looking for|"
                            r"we(?:'|’)?re looking for|we are looking for|experience|ihr profil|dein profil|das bringst du mit|was du mitbringst|"
                            r"profil recherch[ée]|votre profil)\b", re.I)
SF_NICE_HEADING = re.compile(r"^(?:nice[- ]to[- ]haves?|bonus(?: points)?|preferred(?: qualifications?| skills| experience)?|"
                             r"(?:it(?:'|’)?s |would be )?a plus|pluses|desirable|desired|ideally|good to have|extra credit|"
                             r"you(?:'|’)?ll stand out|even better|wünschenswert|von vorteil)\b", re.I)
SF_NICE = re.compile(r"nice[- ]to[- ]have|\bbonus\b|\bplus(?:es)?\b|\bpreferred\b|\bpreferabl[ey]\b|\bideally\b|\bdesirable\b|\bdesired\b|"
                     r"\badvantage(?:ous)?\b|\bfamiliarity\b|\bfamiliar with\b|\bnot required\b|\boptional\b|\bbeneficial\b|"
                     r"\bhelpful\b|\ban asset\b|\b(?:highly )?regarded\b|\bwould be great\b|\bgood to have\b|\bexposure\b|\b(?:don(?:'|’)?t|do not|no) need\b|"
                     r"\b(?:not|isn(?:'|’)?t|aren(?:'|’)?t|never)\s+(?:\w+\s+)?(?:necessary|essential|required|mandatory|needed|expected|critical|a must|a requirement)\b|"
                     r"von vorteil|wünschenswert|idealerweise|\bun (?:plus|atout)\b|souhait", re.I)
# Salesforce as one of several tools: "HubSpot or Salesforce", "Salesforce/HubSpot", "e.g. Salesforce"
SF_ALT = re.compile(r"\bor\b|\bany of\b|\betc\b|\bsimilar\b|\bcomparable\b|\bequivalent\b|\bany (?:major )?crm\b|\boder\b|\bou\b", re.I)
SF_EXAMPLE = re.compile(r"\bsuch as\b|\be\.?g\.?(?!\w)|\bfor example\b|\bfor instance\b|\bincluding\b|\blike\b|\bz\.\s?B\.|\bwie\b|\bcomme\b", re.I)
# Salesforce as a customer, partner, investor, integration or the team's background
SF_CONTEXT = re.compile(r"integrat\w*|connect(?:ors?|ed|s|ing)?\b|\bcustomers?\b|\bclients?\b|\bpartners?\b|\bpartnerships?\b|\bex-|\bformer\w*|"
                        r"\balumni\b|\bbacked by\b|\binvestors?\b|\bventures\b|\bpreviously\b|\bworked at\b|\bcompanies like\b|"
                        r"\btrusted by\b|\bbrands like\b|\bfounded by\b|\bteam (?:brings|from|includes)\b|\bacquired\b|\bacquisition\b|"
                        r"\becosystem\b|\bAppExchange\b|\bpart of\b|\bjoin\b|\bat Salesforce\b|\bemploy\w*|\bwork(?:ing)? (?:at|for)\b", re.I)
# Necessity counts only when it governs the Salesforce qualification in the same clause. A "must" that governs
# another duty ("We use Salesforce internally, and you must grow revenue") does not.
SF_CLAUSE_BREAK = re.compile(r"[,;:()]|\s[–—-]\s|\b(?:but|while|whereas|because|although|though|which|where|who|whom|so that)\b|"
                             r"\b(?:and|or|so|then)\s+(?=(?:you|we|they|our|your|it|this|that|he|she|I|there|the team)\b)", re.I)
SF_COORD = re.compile(r"\b(?:and|or|plus|as well as|is|are|was|were)\b|&", re.I)
SF_NEED_LABEL = re.compile(r"^\W*(?:absolutely |strictly )?(?:required|mandatory|essential|must[- ]haves?|a must)\s*:", re.I)
SF_NEED_AFTER = re.compile(r"^((?:\s+[\w/&+.-]+){0,5}?)\s+(?:is |are |will be )?(?:absolutely |strictly |also )?"
                           r"(?<!not )(?:required|mandatory|essential|a must)\b", re.I)
SF_NEED_BEFORE = re.compile(r"\bmust\s+(?:already\s+)?(?:have|possess|bring|demonstrate|show|know|be\s+(?:\w+\s+){0,2}?(?:proficient|experienced|"
                            r"fluent|skilled|an? expert|expert|certified|comfortable|well[- ]versed|adept|strong))\b|"
                            r"\b(?<!not )(?:requires?|required to (?:have|know|be))\b", re.I)
# a requirements-section unit counts only when it reads as a qualification, not a tool list
SF_QUAL = re.compile(r"experience|expertise|\bexpert|knowledge|proficien|fluen|skill|understanding|command of|mastery|comfortable|"
                     r"background|certifi|\byears?\b|\bability\b|\bable to\b|\bknow\b|track record|hands-on|\badmin", re.I)
# years and certification count anywhere; depth wording outside a known non-requirements section
SF_NEED = re.compile(r"\b\d+\+?\s*(?:years?|yrs)\b[^.;]{0,40}?(?:Salesforce|SFDC|Pardot|Apex|SOQL|Sales Cloud|Marketing Cloud)\b|"
                     r"\bSalesforce(?:\.com)? Certified\b|\bSalesforce(?: Certified)? (?:\w+(?:/\w+)? )?(?:admin|administrator|consultant|developer)\s+certifi\w*|"
                     r"\bcertified Salesforce\b", re.I)
SF_DEPTH = re.compile(r"\b(?:expert|deep|hands-on|proven)\s+(?:\w+\s+){0,2}?(?:experience|expertise|knowledge|proficiency)?\s*(?:with|in|of)\s+"
                      r"(?:the\s+)?(?:Salesforce|SFDC|Pardot|Apex|SOQL|Sales Cloud|Marketing Cloud)\b|"
                      r"\b(?:Salesforce|SFDC)\s+(?:expert|expertise)\b", re.I)
SF_WINDOW = 60     # characters on each side of the Salesforce word that the alternative / context checks read


def _units(description):
    """Lines, bullets and sentences of a description, HTML or plain text."""
    d = html.unescape(html.unescape(description or ""))
    d = re.sub(r"(?i)<\s*(?:/?(?:p|li|ul|ol|div|h[1-6]|tr|section)|br)\b[^>]*>", "\n", d)
    d = re.sub(r"<[^>]+>", " ", d)
    out = []
    for line in re.split(r"\n|\s[•·▪●◦]\s|^\s*[-*•]\s|\s+-\s+(?=[A-Z])", d):
        out += [u for u in (re.sub(r"^[\s*•·▪●◦–-]+", "", x).strip() for x in re.split(r"(?<=[.!?;])\s+(?=[A-Z])", line)) if u]
    return out


def _heading(u):
    """('req' | 'nice' | 'other', following text) when the unit is a heading, else (None, unit)."""
    core = re.sub(r"^[\W\d_]+|[\W_]+$", "", u)
    m = re.match(r"^\W*([^:]{2,45}):\s*(.+)$", u)   # inline "Requirements: Experience with Salesforce."
    if m:
        h = m.group(1).strip()
        if SF_NICE_HEADING.search(h):
            return "nice", m.group(2)
        if SF_REQ_HEADING.search(h):
            return "req", m.group(2)
    # "Experience with Salesforce" under a requirements heading is a qualification, not a heading
    if SF_TERM.search(core) and SF_QUAL.search(core) and not u.rstrip().endswith(":"):
        return None, u
    if len(core) <= 70 and not re.search(r"[.!?;]|:\s*\S", core) and len(core.split()) <= 9:
        kind = "nice" if SF_NICE_HEADING.search(core) else "req" if SF_REQ_HEADING.search(core) else None
        if kind or u.rstrip().endswith(":") or core.isupper() or (
                len(core.split()) <= 3 and core[:1].isupper() and not SF_TERM.search(core) and not SF_QUAL.search(core)) \
                or re.match(r"^(?:what|who|how|why|about|our|your|benefits|perks|responsibilities|the role)\b", core, re.I):
            return kind or "other", ""
    return None, u


def _sf_need(text, m):
    """True when a necessity word in the Salesforce word's own clause governs the Salesforce qualification."""
    if SF_NEED_LABEL.match(text[:m.start()] + " "):
        return True
    starts = [b.end() for b in SF_CLAUSE_BREAK.finditer(text[:m.start()])]
    ends = [b.start() for b in SF_CLAUSE_BREAK.finditer(text, m.end())]
    clause_start, clause_end = (starts[-1] if starts else 0), (ends[0] if ends else len(text))
    before, after = text[clause_start:m.start()], text[m.end():clause_end]
    a = SF_NEED_AFTER.match(after)
    # "Salesforce is used and SQL expertise is required": the necessity has its own subject
    if a and not SF_COORD.search(a.group(1)) and (not a.group(1).strip() or SF_QUAL.search(text[clause_start:clause_end]) or SF_TERM.search(a.group(1))):
        return True
    b = list(SF_NEED_BEFORE.finditer(before))
    # "You must have SQL skills and use Salesforce": the necessity governs the first object only
    if not b or SF_COORD.search(before[b[-1].end():]):
        return False
    return bool(re.search(r"\bmust\b", before, re.I) or SF_QUAL.search(text[clause_start:clause_end]))


def salesforce_required(description, company=None):
    """The unit that makes Salesforce a required qualification, or None. Salesforce's own postings never count."""
    if not description or (company and re.search(r"salesforce", company, re.I)):
        return None
    section = None
    for u in _units(description):
        kind, text = _heading(u)
        if kind:
            section = kind
        ms = list(SF_TERM.finditer(text))
        if not ms or SF_NICE.search(text) or section == "nice":
            continue
        # one doubtful mention (alternative, example list, customer / integration, inside a parenthesis) drops the unit
        if any(SF_ALT.search(text[max(0, m.start() - SF_WINDOW):m.end() + SF_WINDOW])
               or re.search(r"/\s*$", text[:m.start()]) or re.match(r"\s*/", text[m.end():])
               or SF_CONTEXT.search(text[max(0, m.start() - SF_WINDOW):m.end() + SF_WINDOW])
               or SF_EXAMPLE.search(text[:m.start()]) or re.search(r"\([^()]*$", text[:m.start()]) for m in ms):
            continue
        if ((section == "req" and SF_QUAL.search(text)) or SF_NEED.search(text) or (section != "other" and SF_DEPTH.search(text))
                or any(_sf_need(text, m) for m in ms)):
            return re.sub(r"\s+", " ", text)[:200]
    return None


def leadership_kept(t):
    """True for a Head / Director / VP / Lead growth, marketing or GTM title that names no excluded role."""
    return bool(LEADERSHIP.search(t) and TARGET_WORD.search(t) and not HARD_ROLE.search(t))


def title_family(t, on):
    if "salesforce" in on and SALESFORCE.search(t):
        return "salesforce"
    if leadership_kept(t):
        return None
    if "recruiter" in on and RECRUITER.search(t):
        return "recruiter"
    if "junior" in on and JUNIOR.search(t):
        return "junior"
    if "ops" in on and OPS.search(t):
        if GTM_ENGINEER.search(t):
            return None
        if OPS_LEVEL.search(t) and not OPS_OUT.search(t):
            return None   # an ops lead, manager or head
        return "ops"
    return None


def context_family(t, company, description, industries, on):
    if "anonymous" in on and company and ANON_COMPANY.search(company):
        return "anonymous"
    # "Product Manager - Financial Systems Engineer" is a product role
    if "engineer" in on and ENGINEER.search(t) and not PROFILE_ENGINEER.search(t) and not PRODUCT_WORD.search(t):
        return "engineer"
    if "sales" in on:
        if not PROFILE_ENGINEER.search(t) and (SALES_IC.search(t) or (SALES_BD.search(t) and not MARKETING_WORD.search(t))):
            return "sales"
        if SALES_LEAD.search(t) and not MARKETING_WORD.search(t) and not re.search(r"\bgtm\b|go[- ]to[- ]market", t, re.I):
            return "sales"
        if sales_description_family(t, description):
            return "sales"
    if "gm" in on and GM_TITLE.search(t) and not PRODUCT_WORD.search(t):
        need = MARKETING_HITS_FOR_GM_MKT if re.search(r"marketing", t, re.I) else MARKETING_HITS_FOR_GM
        if not (GROWTH_OR_MARKETING.search(t) and description and marketing_hits(description) >= need):
            return "gm"
    fam = d2c_retail_family(t, industries, description)
    return fam if fam in on else None


def role_family(title, company=None, description=None, industries=None, families=None, custom=()):
    """The excluded family of a posting, or None. `families` is the set of enabled built-in families (None = all);
    `custom` is a sequence of (name, compiled title regex) checked after the built-in ones."""
    t = title or ""
    on = set(FAMILIES if families is None else families)
    if "president" in on and PRESIDENT.search(t):
        return "president"
    fam = title_family(t, on)
    if not fam and "salesforce" in on and salesforce_required(description, company):
        fam = "salesforce"
    fam = fam or context_family(t, company, description, industries, on)
    if not fam and "mobile_app" in on:
        fam = mobile_family(t, description)
    if not fam:
        fam = next((name for name, rx in custom if rx.search(t)), None)
    return fam


def excluded_reason(title, company=None, description=None, industries=None, families=None, custom=()):
    fam = role_family(title, company, description, industries, families, custom)
    return f"role: {fam}" if fam else None
