import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from html import unescape

import requests
from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "research_publications.json"
TZ = ZoneInfo("Africa/Tunis")
# Initial backfill begins on 1 October 2026. Subsequent scheduled runs
# add newly published items while retaining the existing list for up to 30 days.
SEED_START_DATE = "2026-10-01"
MAX_AGE_DAYS = 30
MAX_PER_CENTER = 30
MAX_TOTAL = 800
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AfricanSecurityObserver/1.0)"}

# Direct, source-specific searches: this file records publications and links only.
CENTERS = [
    {"name": "المعهد الدولي للدراسات الاستراتيجية (IISS)", "short": "IISS", "domain": "iiss.org", "query": "Africa OR Sahel OR security OR military OR defence OR defense OR strategy OR geopolitics"},
    {"name": "مركز الدراسات الاستراتيجية والدولية (CSIS)", "short": "CSIS", "domain": "csis.org", "query": "Africa OR Sahel OR security OR military OR defence OR defense OR strategy OR geopolitics"},
    {"name": "تشاتام هاوس", "short": "Chatham House", "domain": "chathamhouse.org", "query": "Africa OR Sahel OR security OR conflict OR defence OR defense OR geopolitics"},
    {"name": "معهد ستوكهولم الدولي لأبحاث السلام (SIPRI)", "short": "SIPRI", "domain": "sipri.org", "query": "Africa OR arms transfers OR military spending OR weapons OR conflict OR security"},
    {"name": "مؤسسة RAND", "short": "RAND", "domain": "rand.org", "query": "Africa OR national security OR military strategy OR defense OR defence OR conflict"},
    {"name": "معهد بروكينغز", "short": "Brookings", "domain": "brookings.edu", "query": "Africa OR security OR foreign policy OR military OR conflict OR strategy"},
    {"name": "المجلس الأطلسي", "short": "Atlantic Council", "domain": "atlanticcouncil.org", "query": "Africa OR Sahel OR defense OR security OR geopolitics OR military"},
    {"name": "المعهد الملكي للخدمات المتحدة (RUSI)", "short": "RUSI", "domain": "rusi.org", "query": "Africa OR military OR defence OR defense OR security OR strategy OR conflict"},
    {"name": "مؤسسة كارنيغي للسلام الدولي", "short": "Carnegie Endowment", "domain": "carnegieendowment.org", "query": "Africa OR security OR foreign policy OR military OR geopolitics"},
    {"name": "مركز كارنيغي للشرق الأوسط", "short": "Carnegie Middle East", "domain": "carnegie-mec.org", "query": "security OR defense OR military OR Middle East OR North Africa OR Libya"},
    {"name": "مجموعة الأزمات الدولية", "short": "International Crisis Group", "domain": "crisisgroup.org", "query": "Africa OR Sahel OR Libya OR Sudan OR conflict OR security"},
    {"name": "المجلس الأوروبي للعلاقات الخارجية (ECFR)", "short": "ECFR", "domain": "ecfr.eu", "query": "Africa OR security OR defense OR defence OR geopolitics OR Middle East"},
    {"name": "معهد ستيمسون", "short": "Stimson Center", "domain": "stimson.org", "query": "Africa OR security OR defense OR arms control OR military OR geopolitics"},
    {"name": "مركز ويلسون", "short": "Wilson Center", "domain": "wilsoncenter.org", "query": "Africa OR security OR military OR defense OR Middle East OR geopolitics"},
    {"name": "مجلس العلاقات الخارجية", "short": "CFR", "domain": "cfr.org", "query": "Africa OR security OR military OR foreign policy OR conflict OR defense"},
    {"name": "مؤسسة هيريتيج", "short": "Heritage Foundation", "domain": "heritage.org", "query": "national security OR military OR defense OR Africa OR Middle East"},
    {"name": "معهد هدسون", "short": "Hudson Institute", "domain": "hudson.org", "query": "military OR defense OR security OR Africa OR Middle East OR geopolitics"},
    {"name": "صندوق مارشال الألماني (GMF)", "short": "GMF", "domain": "gmfus.org", "query": "Africa OR security OR defense OR geopolitics OR NATO OR military"},
    {"name": "المعهد الألماني للشؤون الدولية والأمنية (SWP)", "short": "SWP", "domain": "swp-berlin.org", "query": "Africa OR security OR defense OR military OR geopolitics"},
    {"name": "معهد كلينغندايل", "short": "Clingendael", "domain": "clingendael.org", "query": "Africa OR Sahel OR security OR defense OR conflict"},
    {"name": "المعهد الفرنسي للعلاقات الدولية (IFRI)", "short": "IFRI", "domain": "ifri.org", "query": "Africa OR sécurité OR défense OR militaire OR géopolitique"},
    {"name": "معهد العلاقات الدولية والاستراتيجية (IRIS)", "short": "IRIS", "domain": "iris-france.org", "query": "Afrique OR sécurité OR défense OR géopolitique OR militaire"},
    {"name": "المعهد الملكي إلكانو", "short": "Elcano", "domain": "realinstitutoelcano.org", "query": "Africa OR security OR defense OR geopolitics OR military"},
    {"name": "المعهد الفنلندي للشؤون الدولية (FIIA)", "short": "FIIA", "domain": "fiia.fi", "query": "security OR defense OR NATO OR geopolitics OR military"},
    {"name": "معهد لوي", "short": "Lowy Institute", "domain": "lowyinstitute.org", "query": "Indo-Pacific OR security OR defense OR military OR geopolitics"},
    {"name": "المعهد الأسترالي للسياسة الاستراتيجية (ASPI)", "short": "ASPI", "domain": "aspistrategist.org.au", "query": "defence OR defense OR military OR security OR Indo-Pacific"},
    {"name": "مؤسسة أبحاث المراقب (ORF)", "short": "ORF", "domain": "orfonline.org", "query": "security OR defense OR military OR Africa OR geopolitics"},
    {"name": "معهد الدراسات الأمنية الإفريقي (ISS Africa)", "short": "ISS Africa", "domain": "issafrica.org", "query": "Africa OR Sahel OR security OR conflict OR military"},
    {"name": "مركز إفريقيا للدراسات الاستراتيجية", "short": "Africa Center for Strategic Studies", "domain": "africacenter.org", "query": "Africa OR security OR military OR Sahel OR conflict"},
    {"name": "معهد الدراسات الدفاعية والاستراتيجية الهندي (MP-IDSA)", "short": "MP-IDSA", "domain": "idsa.in", "query": "defence OR defense OR security OR military OR Africa OR geopolitics"},
    {"name": "المعهد الوطني الياباني للدراسات الدفاعية (NIDS)", "short": "NIDS", "domain": "nids.mod.go.jp", "query": "security OR defense OR military OR Indo-Pacific"},
    {"name": "المعهد الكوري للتحليلات الدفاعية (KIDA)", "short": "KIDA", "domain": "kida.re.kr", "query": "security OR defense OR military OR strategy"},
    {"name": "المكتب الوطني للأبحاث الآسيوية (NBR)", "short": "NBR", "domain": "nbr.org", "query": "Asia OR security OR defense OR military OR geopolitics"},
    {"name": "معهد أبحاث الدفاع والأمن الوطني التايواني (INDSR)", "short": "INDSR", "domain": "indsr.org.tw", "query": "defense OR security OR military OR strategy OR Indo-Pacific"},
    {"name": "معهد الدراسات الأمنية الأوروبي (EUISS)", "short": "EUISS", "domain": "iss.europa.eu", "query": "Africa OR Mediterranean OR Sahel OR security OR defence OR defense OR strategic autonomy"},
    {"name": "مركز الأمن الأمريكي الجديد (CNAS)", "short": "CNAS", "domain": "cnas.org", "query": "national security OR defense OR military OR technology OR China OR Russia"},
    {"name": "مركز بلفر للعلوم والشؤون الدولية", "short": "Belfer Center", "domain": "belfercenter.org", "query": "security OR defense OR nuclear OR cyber OR energy OR geopolitics"},
    {"name": "معهد الشرق الأوسط", "short": "Middle East Institute", "domain": "mei.edu", "query": "North Africa OR Tunisia OR Libya OR Algeria OR Mediterranean OR security OR defense"},
    {"name": "المركز العربي في واشنطن", "short": "Arab Center Washington DC", "domain": "arabcenterdc.org", "query": "Tunisia OR Libya OR Algeria OR North Africa OR Mediterranean OR security"},
    {"name": "مركز الإمارات للسياسات", "short": "Emirates Policy Center", "domain": "epc.ae", "query": "North Africa OR Libya OR Algeria OR Mediterranean OR security OR geopolitics"},
    {"name": "مركز أبحاث الخليج", "short": "Gulf Research Center", "domain": "grc.net", "query": "Mediterranean OR North Africa OR energy security OR maritime security OR geopolitics"},
    {"name": "تريندز للبحوث والاستشارات", "short": "TRENDS Research", "domain": "trendsresearch.org", "query": "Africa OR North Africa OR security OR defense OR terrorism OR geopolitics"},
    {"name": "مؤسسة سيتا التركية", "short": "SETA", "domain": "setav.org", "query": "Libya OR Tunisia OR Algeria OR North Africa OR Mediterranean OR security"},
    {"name": "مركز الاقتصاد والسياسة الخارجية التركي", "short": "EDAM", "domain": "edam.org.tr", "query": "Mediterranean OR Libya OR North Africa OR defense OR security OR NATO"},
    {"name": "المعهد النرويجي للشؤون الدولية (NUPI)", "short": "NUPI", "domain": "nupi.no", "query": "Africa OR Sahel OR Mediterranean OR security OR conflict OR geopolitics"},
    {"name": "المعهد الدنماركي للدراسات الدولية (DIIS)", "short": "DIIS", "domain": "diis.dk", "query": "Africa OR Sahel OR migration OR security OR defense OR geopolitics"},
    {"name": "المعهد البولندي للشؤون الدولية (PISM)", "short": "PISM", "domain": "pism.pl", "query": "Africa OR Mediterranean OR NATO OR security OR defense OR geopolitics"},
    {"name": "المعهد الإستوني للسياسة الخارجية", "short": "Estonian Foreign Policy Institute", "domain": "evi.ee", "query": "NATO OR Russia OR Europe OR security OR defense OR geopolitics"},
    {"name": "وكالة أبحاث الدفاع السويدية (FOI)", "short": "FOI", "domain": "foi.se", "query": "defense OR security OR military OR Russia OR NATO OR cyber"},
    {"name": "الجمعية الألمانية للسياسة الخارجية (DGAP)", "short": "DGAP", "domain": "dgap.org", "query": "Africa OR Mediterranean OR Europe OR security OR defense OR geopolitics"},
    {"name": "مؤسسة كونراد أديناور", "short": "Konrad Adenauer Stiftung", "domain": "kas.de", "query": "Africa OR Sahel OR North Africa OR security OR geopolitics"},
    {"name": "مؤسسة فريدريش إيبرت", "short": "Friedrich Ebert Stiftung", "domain": "fes.de", "query": "North Africa OR Tunisia OR Libya OR Sahel OR peace OR security"},
    {"name": "المعهد الجنوب إفريقي للشؤون الدولية (SAIIA)", "short": "SAIIA", "domain": "saiia.org.za", "query": "Africa OR Sahel OR security OR defense OR conflict OR geopolitics"},
    {"name": "معهد الدراسات الدفاعية والاستراتيجية في إسلام آباد", "short": "ISSI", "domain": "issi.org.pk", "query": "Africa OR Middle East OR security OR defense OR military OR geopolitics"},
    {"name": "معهد اليابان للشؤون الدولية (JIIA)", "short": "JIIA", "domain": "jiia.or.jp", "query": "security OR defense OR Middle East OR Africa OR maritime OR geopolitics"},
    {"name": "معهد جنيف للسياسات الأمنية (GCSP)", "short": "GCSP", "domain": "gcsp.ch", "query": "cybersecurity OR defense OR security OR artificial intelligence OR geopolitics"},
    {"name": "مسح الأسلحة الصغيرة (Small Arms Survey)", "short": "Small Arms Survey", "domain": "smallarmssurvey.org", "query": "Africa OR Sahel OR Libya OR arms trafficking OR armed violence"},
    {"name": "المعهد الدولي لنزع السلاح (UNIDIR)", "short": "UNIDIR", "domain": "unidir.org", "query": "cyber OR artificial intelligence OR arms control OR weapons OR security"},
    {"name": "المبادرة العالمية لمكافحة الجريمة المنظمة العابرة للحدود", "short": "Global Initiative", "domain": "globalinitiative.net", "query": "Africa OR Sahel OR Libya OR smuggling OR trafficking OR organized crime"},
    {"name": "شبكة القيادة الأوروبية", "short": "European Leadership Network", "domain": "europeanleadershipnetwork.org", "query": "NATO OR Russia OR nuclear OR European security OR defense"},
    {"name": "المعهد السويسري لأبحاث السلام (swisspeace)", "short": "swisspeace", "domain": "swisspeace.ch", "query": "Africa OR Sahel OR conflict OR peacebuilding OR security"},
    {"name": "المركز البرازيلي للعلاقات الدولية (CEBRI)", "short": "CEBRI", "domain": "cebri.org", "query": "Africa OR Mediterranean OR security OR defense OR geopolitics"},
    {"name": "مركز الدراسات الاستراتيجية والأمنية في بنغلادش", "short": "BIPSS", "domain": "bipss.org.bd", "query": "Africa OR Middle East OR security OR defense OR terrorism"},
    {"name": "مركز الدراسات السياسية والاستراتيجية بالأهرام", "short": "ACPSS", "domain": "acpss.ahram.org.eg", "query": "Libya OR Tunisia OR Algeria OR North Africa OR security OR military"},
    {"name": "المركز المصري للفكر والدراسات الاستراتيجية", "short": "ECSS", "domain": "ecss.com.eg", "query": "Libya OR Tunisia OR Algeria OR North Africa OR security OR defense"},
    {"name": "مركز الدراسات الاستراتيجية في جامعة أديس أبابا", "short": "IPSS Addis Ababa", "domain": "ipss-addis.org", "query": "Africa OR Sahel OR security OR conflict OR peace"},
    {"name": "مركز تحليل البيانات المتعلقة بالنزاعات المسلحة (ACLED)", "short": "ACLED", "domain": "acleddata.com", "query": "Africa OR Sahel OR Libya OR Algeria OR conflict OR political violence"},
    {"name": "المعهد الدولي لمكافحة الإرهاب (ICT)", "short": "ICT", "domain": "ict.org.il", "query": "Africa OR Sahel OR North Africa OR terrorism OR counterterrorism"},
    {"name": "مركز أولويات الدفاع", "short": "Defense Priorities", "domain": "defensepriorities.org", "query": "Africa OR Middle East OR military strategy OR defense OR foreign policy"},
    {"name": "معهد كوينسي للحكم المسؤول", "short": "Quincy Institute", "domain": "quincyinst.org", "query": "Africa OR Middle East OR military intervention OR foreign policy OR security"},
    {"name": "مركز أبحاث السياسة الخارجية النمساوي (oiip)", "short": "oiip", "domain": "oiip.ac.at", "query": "Mediterranean OR North Africa OR security OR migration OR geopolitics"},
    {"name": "معهد الدراسات الأمنية والتنمية (ISDP)", "short": "ISDP", "domain": "isdp.eu", "query": "Africa OR Middle East OR security OR defense OR geopolitics"},
    {"name": "المعهد التونسي للدراسات الاستراتيجية", "short": "ITES Tunisia", "domain": "ites.tn", "query": "Tunisia OR Tunisian OR strategic studies OR public policy OR security OR economy"},
    {"name": "المركز العربي للأبحاث ودراسة السياسات - تونس", "short": "CAREP Tunisia", "domain": "carep.tn", "query": "Tunisia OR Tunisian OR Maghreb OR politics OR society OR economy"},
    {"name": "مركز الدراسات المتقدمة حول المغرب العربي (MECAM)", "short": "MECAM", "domain": "mecam.tn", "query": "Tunisia OR Tunisian OR Maghreb OR political OR social OR economic research"},
    {"name": "المنتدى التونسي للحقوق الاقتصادية والاجتماعية", "short": "FTDES", "domain": "ftdes.net", "query": "Tunisia OR Tunisian OR migration OR economy OR social rights OR environment"},
    {"name": "المعهد التونسي للقدرة التنافسية والدراسات الكمية", "short": "ITCEQ", "domain": "itceq.tn", "query": "Tunisia OR Tunisian OR economic policy OR competitiveness OR development OR statistics"},
    {"name": "مركز الدراسات الاستراتيجية والدبلوماسية التونسي", "short": "CSDS Tunisia", "domain": "csds-center.com", "query": "Tunisia OR Tunisian OR diplomacy OR security OR foreign policy OR economy"},
    {"name": "مبادرة الإصلاح العربي", "short": "Arab Reform Initiative", "domain": "arab-reform.net", "query": "Tunisia OR Tunisian OR political economy OR governance OR democracy OR security"},
    {"name": "مؤسسة هاينريش بول - مكتب تونس", "short": "Heinrich Böll Tunisia", "domain": "tn.boell.org", "query": "Tunisia OR Tunisian OR democracy OR climate OR energy OR migration"},
    {"name": "مؤسسة روزا لوكسمبورغ - شمال إفريقيا", "short": "Rosa Luxemburg North Africa", "domain": "rosaluxna.org", "query": "Tunisia OR Tunisian OR North Africa OR economy OR social movements OR democracy"},
    {"name": "منظمة البوصلة", "short": "Al Bawsala", "domain": "al-bawsala.com", "query": "Tunisia OR Tunisian OR parliament OR governance OR public policy OR budget"},
    {"name": "المعهد العربي لحقوق الإنسان", "short": "Arab Institute for Human Rights", "domain": "aihr.tn", "query": "Tunisia OR Tunisian OR human rights OR governance OR civil society"},
    {"name": "المعهد العربي لرؤساء المؤسسات", "short": "IACE", "domain": "iace.tn", "query": "Tunisia OR Tunisian OR economy OR investment OR competitiveness OR employment"},
    {"name": "مركز المرأة العربية للتدريب والبحوث", "short": "CAWTAR", "domain": "cawtar.org", "query": "Tunisia OR Tunisian OR gender OR social policy OR economic participation"},
    {"name": "مجموعة حقوق الشرق الأوسط وشمال إفريقيا", "short": "MENA Rights Group", "domain": "menarights.org", "query": "Tunisia OR Tunisian OR human rights OR detention OR rule of law"},
    {"name": "المعهد الدولي للديمقراطية والمساعدة الانتخابية", "short": "International IDEA", "domain": "idea.int", "query": "Tunisia OR Tunisian OR elections OR constitution OR democracy OR governance"},
    {"name": "الباروميتر العربي", "short": "Arab Barometer", "domain": "arabbarometer.org", "query": "Tunisia OR Tunisian OR public opinion OR trust OR democracy OR economy"},
    {"name": "أفروباروميتر", "short": "Afrobarometer", "domain": "afrobarometer.org", "query": "Tunisia OR North Africa OR governance OR public opinion OR democracy"},
    {"name": "المركز الدولي للعدالة الانتقالية", "short": "ICTJ", "domain": "ictj.org", "query": "Tunisia OR Tunisian OR transitional justice OR accountability OR human rights"},
    {"name": "فريدوم هاوس", "short": "Freedom House", "domain": "freedomhouse.org", "query": "Tunisia OR Tunisian OR political rights OR civil liberties OR democracy"},
    {"name": "معهد V-Dem للديمقراطية", "short": "V-Dem Institute", "domain": "v-dem.net", "query": "Tunisia OR Tunisian OR democracy OR autocratization OR governance"},
    {"name": "منتدى الشرق الأوسط للسياسة", "short": "POMEPS", "domain": "pomeps.org", "query": "Tunisia OR Tunisian OR North Africa OR political economy OR governance"},
    {"name": "معهد التحرير لسياسة الشرق الأوسط", "short": "TIMEP", "domain": "timep.org", "query": "Tunisia OR Tunisian OR North Africa OR politics OR human rights OR security"},
    {"name": "مركز الجزيرة للدراسات", "short": "Al Jazeera Centre for Studies", "domain": "studies.aljazeera.net", "query": "Tunisia OR Tunisian OR Maghreb OR Libya OR Algeria OR regional security"},
    {"name": "المعهد الأوروبي للبحر الأبيض المتوسط", "short": "IEMed", "domain": "iemed.org", "query": "Tunisia OR Tunisian OR Mediterranean OR migration OR security OR regional cooperation"},
    {"name": "شبكة أوروميسكو للدراسات الأورومتوسطية", "short": "EuroMeSCo", "domain": "euromesco.net", "query": "Tunisia OR Tunisian OR Mediterranean OR migration OR security OR foreign policy"},
    {"name": "مركز برشلونة للشؤون الدولية", "short": "CIDOB", "domain": "cidob.org", "query": "Tunisia OR Tunisian OR North Africa OR Mediterranean OR migration OR security"},
    {"name": "منظمة ACAPS للتحليل الإنساني", "short": "ACAPS", "domain": "acaps.org", "query": "Tunisia OR Tunisian OR Libya OR North Africa OR migration OR regional crisis"},
    {"name": "مركز التكامل المتوسطي", "short": "Center for Mediterranean Integration", "domain": "cmimarseille.org", "query": "Tunisia OR Tunisian OR Mediterranean OR economic development OR migration OR regional cooperation"},
]

# Strict relevance filter: keep only work directly about Tunisia, work on
# nearby/regional issues with a plausible spillover to Tunisia, or global
# strategic developments with a credible transmission channel to Tunisia.
TUNISIA_TERMS = [
    "tunisia", "tunisian", "tunisie", "tunisien", "تونس", "تونسي",
    "تونسيه", "قرطاج", "صفاقس", "بنزرت", "سوسة", "القيروان", "قابس",
    "مدنين", "جربة", "جرجيس"
]
NEARBY_TERMS = [
    "libya", "libyan", "libye", "libyen", "ليبيا", "ليبي", "طرابلس",
    "بنغازي", "حفتر", "الدبيبة", "المنفي", "algeria", "algerian",
    "algérie", "algérien", "algiers", "الجزائر", "جزائري", "تبون",
    "شنقريحة", "north africa", "north african", "maghreb", "maghrib",
    "المغرب العربي", "شمال افريقيا", "شمال إفريقيا", "morocco", "moroccan",
    "maroc", "المغرب", "مغربي", "egypt", "egyptian", "مصر", "موريتانيا",
    "mauritania", "mediterranean", "méditerranée", "البحر المتوسط",
    "southern mediterranean", "western sahara", "الصحراء الغربية",
    "sahel", "الساحل الإفريقي", "الساحل الافريقي"
]
# A nearby-country article is retained only when its title/description shows
# a plausible link such as cross-border effects, trade, migration, energy,
# maritime routes, or regional security—not simply because it names Libya.
SPILLOVER_TERMS = [
    "tunisia", "tunisian", "tunisie", "تونس", "تونسية", "تونسيا",
    "cross-border", "cross border", "border region", "border security",
    "border crossing", "shared border", "neighbouring", "neighboring",
    "spillover", "regional impact", "regional security", "regional stability",
    "transnational", "bilateral", "trade ties", "trade route", "trade routes",
    "exports", "imports", "energy supply", "gas supply", "gas exports",
    "oil supply", "energy market", "energy markets", "oil prices", "gas prices",
    "migration route", "migration routes", "irregular migration",
    "human trafficking", "smuggling network", "maritime security",
    "shipping route", "shipping routes", "mediterranean", "méditerranée",
    "المتوسط", "البحر المتوسط", "حدود", "عبر الحدود", "الحدود المشتركة",
    "تعاون أمني", "تعاون دفاعي", "التعاون الأمني", "التعاون الدفاعي",
    "الهجرة غير النظامية", "مسارات الهجرة", "شبكات تهريب عابرة للحدود",
    "إمدادات الطاقة", "صادرات الغاز", "صادرات النفط", "أسعار النفط",
    "أسعار الغاز", "التبادل التجاري", "طرق التجارة الإقليمية",
    "الأمن الإقليمي", "استقرار المنطقة", "طرق الملاحة", "الأمن البحري",
    "تداعيات إقليمية", "تداعيات على تونس"
]
# Global developments need both a strategic subject and a credible impact
# pathway relevant to Tunisia (energy, food, trade, shipping, migration,
# European/Mediterranean security, Sahel security, sanctions, or cyber risks).
GLOBAL_CONTEXT_TERMS = [
    "global", "worldwide", "international", "europe", "european union", "eu ",
    "nato", "russia", "ukraine", "china", "united states", "u.s.", "iran",
    "middle east", "red sea", "gulf", "black sea", "indo-pacific", "africa",
    "sahel", "الساحل", "الاتحاد الأوروبي", "الناتو", "روسيا", "أوكرانيا",
    "الصين", "الولايات المتحدة", "الشرق الأوسط", "البحر الأحمر", "الخليج",
    "دولي", "عالمي", "أوروبا", "إفريقيا", "افريقيا"
]
GLOBAL_STRATEGIC_TERMS = [
    "war", "conflict", "military", "defen", "security", "sanction", "terror",
    "armed", "missile", "drone", "arms", "weapon", "naval", "maritime",
    "energy", "oil", "gas", "pipeline", "lng", "food security", "grain",
    "wheat", "supply chain", "trade", "shipping", "migration", "asylum",
    "cyber", "critical infrastructure", "defense", "defence", "تسلح",
    "عسكري", "أمني", "الأمن", "الحرب", "الصراع", "العقوبات", "الطاقة",
    "النفط", "الغاز", "الغذاء", "الحبوب", "التجارة", "الملاحة", "الهجرة",
    "الإرهاب", "الصواريخ", "المسيرات", "الأمن السيبراني"
]
GLOBAL_IMPACT_PATH_TERMS = [
    "energy market", "energy markets", "oil price", "oil prices", "gas price",
    "gas prices", "energy supply", "gas supply", "oil supply", "fuel prices",
    "food security", "food prices", "grain prices", "wheat", "fertilizer",
    "supply chain", "supply chains", "global trade", "trade routes",
    "shipping", "maritime", "mediterranean", "méditerranée", "red sea",
    "suez", "european security", "european defence", "european defense",
    "eu security", "nato", "sahel", "africa", "migration", "asylum",
    "counterterrorism", "cybersecurity", "critical infrastructure",
    "commodity prices", "commodities", "المتوسط", "البحر المتوسط",
    "البحر الأحمر", "قناة السويس", "أمن الطاقة", "أسعار الطاقة", "أسعار النفط",
    "أسعار الغاز", "أمن غذائي", "الأمن الغذائي", "الحبوب", "سلاسل الإمداد",
    "التجارة العالمية", "أمن الملاحة", "الهجرة", "أمن أوروبا",
    "الأمن الأوروبي", "العقوبات", "البنية التحتية الحيوية", "الأمن السيبراني"
]



# A publication must have a clear security, defence, or state-strategy subject.
# Geography or the publisher's identity alone is not enough to qualify it.
STRATEGIC_TOPIC_TERMS = [
    "security", "national security", "regional security", "defence", "defense",
    "military", "armed forces", "army", "navy", "air force", "arms deal",
    "arms transfer", "weapons", "missile", "drone", "war", "warfare",
    "armed conflict", "conflict", "battlefield", "ceasefire", "coup",
    "terrorism", "counterterrorism", "insurgency", "militia", "intelligence",
    "espionage", "cybersecurity", "cyber security", "hybrid threats",
    "border security", "maritime security", "naval", "piracy", "organized crime",
    "smuggling networks", "critical infrastructure", "nuclear deterrence",
    "deterrence", "military spending", "defence industry", "defense industry",
    "peacekeeping", "peace operations", "war crimes", "genocide",
    "foreign policy", "international relations", "geopolitics", "geopolitical",
    "strategic competition", "grand strategy", "national strategy",
    "defence policy", "defense policy", "strategic autonomy", "military doctrine",
    "alliances", "alliance", "nato", "diplomatic crisis", "diplomatic tensions",
    "bilateral relations", "regional power", "great power", "sanctions",
    "export controls", "arms control", "non-proliferation", "proliferation",
    "energy security", "food security", "water security", "supply chain security",
    "maritime routes", "shipping lanes", "critical minerals", "strategic resources",
    "migration security", "irregular migration routes", "border management",
    "political violence", "state fragility", "state collapse", "regional stability",
    "sécurité", "défense", "militaire", "forces armées", "conflit armé",
    "guerre", "terrorisme", "renseignement", "cybersécurité", "géopolitique",
    "politique étrangère", "relations internationales", "industrie de défense",
    "transferts d'armes", "dissuasion", "sécurité énergétique", "sécurité maritime",
    "prolifération", "sanctions internationales", "stabilité régionale",
    "الأمن", "الأمني", "أمن قومي", "الأمن القومي", "الأمن الإقليمي",
    "الدفاع", "عسكري", "عسكرية", "القوات المسلحة", "الجيش", "البحرية",
    "القوات الجوية", "التسلح", "صفقات السلاح", "نقل الأسلحة", "الصواريخ",
    "المسيرات", "الحرب", "الحروب", "النزاع المسلح", "الصراع المسلح",
    "الإرهاب", "مكافحة الإرهاب", "الاستخبارات", "التجسس", "الأمن السيبراني",
    "التهديدات السيبرانية", "أمن الحدود", "الأمن البحري", "القرصنة",
    "الجريمة المنظمة", "التهريب", "البنية التحتية الحيوية", "الردع النووي",
    "السياسة الخارجية", "العلاقات الدولية", "الجغرافيا السياسية",
    "التنافس الاستراتيجي", "الاستراتيجية الوطنية", "العقيدة العسكرية",
    "التحالفات", "أزمة دبلوماسية", "التوترات الدبلوماسية", "القوى الكبرى",
    "العقوبات الدولية", "ضبط التسلح", "منع الانتشار", "أمن الطاقة",
    "الأمن الغذائي", "أمن المياه", "الموارد الاستراتيجية", "الاستقرار الإقليمي",
    "العنف السياسي", "هشاشة الدولة", "انهيار الدولة", "جرائم الحرب",
    "جرائم ضد الإنسانية", "الانقلابات العسكرية"
]


def normalize_relevance_text(value):
    text = str(value or "").lower()
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    text = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", text)
    return re.sub(r"\s+", " ", text)


def has_any(text, terms):
    return any(normalize_relevance_text(term) in text
               for term in terms)


def classify_relevance(item):
    """
    Four ranked relevance tiers:
    1. Directly about Tunisia.
    2. Directly about Tunisia's immediate neighbours, Libya or Algeria.
    3. North Africa, the wider Maghreb, the Mediterranean, or nearby Sahel
       when the topic has a plausible regional-security/economic connection.
    4. Global strategic issues only when a credible impact pathway to Tunisia
       is visible in the title or description.
    Center/source names alone never determine relevance.
    """
    text = normalize_relevance_text(" ".join(
        str(item.get(k, "") or "")
        for k in ("title", "title_ar", "description")
    ))

    labels = {
        1: "تونس مباشرة",
        2: "ليبيا والجزائر",
        3: "شمال إفريقيا والبحر المتوسط والمنطقة القريبة",
        4: "قضايا عالمية قد تؤثر في تونس"
    }

    # Exclude standalone domestic politics, human-rights-only coverage,
    # labour unions and other social topics unless a clear security,
    # defence or state-strategy subject is present.
    if not has_any(text, STRATEGIC_TOPIC_TERMS):
        item["relevance_tier"] = 0
        item["relevance_label"] = "خارج نطاق الأمن والدفاع والاستراتيجية"
        return 0

    if has_any(text, TUNISIA_TERMS):
        item["relevance_tier"] = 1
        item["relevance_label"] = labels[1]
        return 1

    immediate_neighbours = [
        "libya", "libyan", "libye", "libyen", "libyenne", "ليبيا", "ليبي",
        "طرابلس", "بنغازي", "حفتر", "الدبيبة", "المنفي",
        "algeria", "algerian", "algérie", "algérien", "algiers",
        "الجزائر", "جزائري", "تبون", "شنقريحة"
    ]
    if has_any(text, immediate_neighbours):
        item["relevance_tier"] = 2
        item["relevance_label"] = labels[2]
        return 2

    regional_terms = [
        "north africa", "north african", "maghreb", "maghrib",
        "المغرب العربي", "شمال افريقيا", "شمال إفريقيا", "morocco",
        "moroccan", "maroc", "المغرب", "مغربي", "egypt", "egyptian",
        "مصر", "موريتانيا", "mauritania", "mediterranean", "méditerranée",
        "البحر المتوسط", "southern mediterranean", "western sahara",
        "الصحراء الغربية", "sahel", "الساحل الإفريقي", "الساحل الافريقي"
    ]
    regional_security_or_spillover = (
        has_any(text, SPILLOVER_TERMS)
        or has_any(text, [
            "security", "defence", "defense", "military", "arms", "conflict",
            "migration", "energy", "trade", "maritime", "naval", "terrorism",
            "cyber", "الامن", "الأمن", "الدفاع", "عسكري", "تسلح", "نزاع",
            "هجرة", "طاقة", "تجارة", "بحري", "إرهاب", "سيبراني"
        ])
    )
    if has_any(text, regional_terms) and regional_security_or_spillover:
        item["relevance_tier"] = 3
        item["relevance_label"] = labels[3]
        return 3

    global_context = has_any(text, GLOBAL_CONTEXT_TERMS)
    strategic_subject = has_any(text, GLOBAL_STRATEGIC_TERMS)
    impact_path = has_any(text, GLOBAL_IMPACT_PATH_TERMS)
    if global_context and strategic_subject and impact_path:
        item["relevance_tier"] = 4
        item["relevance_label"] = labels[4]
        return 4

    item["relevance_tier"] = 0
    item["relevance_label"] = "خارج نطاق المتابعة"
    return 0


def clean(value):
    """Normalize empty, HTML-escaped, or whitespace-heavy feed fields."""
    value = unescape(str(value or ""))
    return re.sub(r"\s+", " ", value).strip()

def parse_date(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ)
    except Exception:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(TZ)
        except Exception:
            return None

def contains_arabic(value):
    return bool(re.search(r"[\u0600-\u06FF]", str(value or "")))


KNOWN_ARABIC_TITLES = {
    "Emerging ICT Talent and the Dutch Market: Nearshoring to Morocco, Egypt, Tunisia and Nigeria - Clingendael": "المواهب الناشئة في تكنولوجيا المعلومات والاتصالات والسوق الهولندية: نقل الخدمات إلى المغرب ومصر وتونس ونيجيريا",
    "Conflict in Yemen and the Red Sea | Global Conflict Tracker - Council on Foreign Relations": "الصراع في اليمن والبحر الأحمر | متعقّب النزاعات العالمية",
    "international maritime conventions - orfonline.org": "الاتفاقيات البحرية الدولية",
    "Africa must prepare for AI-powered cyber threats at scale - ISS Africa": "ينبغي لإفريقيا الاستعداد لتهديدات سيبرانية واسعة النطاق مدعومة بالذكاء الاصطناعي",
    "How Houthi military aggression in the Red Sea threatens Africa - ISS Africa": "كيف يهدد التصعيد العسكري للحوثيين في البحر الأحمر إفريقيا",
    "Beyond AGOA: Reimagining the Future of U.S.-Africa Trade - CSIS | Center for Strategic and International Studies": "ما بعد قانون النمو والفرص في إفريقيا: إعادة تصور مستقبل التجارة بين الولايات المتحدة وإفريقيا",
    "Could Fighting in Ethiopia Spark Regional War in the Horn of Africa? - CSIS | Center for Strategic and International Studies": "هل يمكن أن يؤدي القتال في إثيوبيا إلى اندلاع حرب إقليمية في القرن الإفريقي؟",
    "October is Cybersecurity Awareness Month - CSIS | Center for Strategic and International Studies": "أكتوبر هو شهر التوعية بالأمن السيبراني",
    "Africa can see El Niño coming – will countries act in time? - ISS Africa": "إفريقيا قادرة على توقّع ظاهرة النينيو؛ فهل تتحرك الدول في الوقت المناسب؟",
    "Illicit Drug Trade Between North America and Africa: Routes, Patterns, and Responses - Africa Center for Strategic Studies": "تجارة المخدرات غير المشروعة بين أمريكا الشمالية وإفريقيا: المسارات والأنماط وسبل المواجهة",
    "Russia-Ukraine War Update and Perspectives From NATO Member Countries: Lunch and Discussion with CFR's Global Board of Advisors - Council on Foreign Relations": "تحديث بشأن الحرب الروسية الأوكرانية ووجهات نظر من دول الناتو: نقاش مع المجلس الاستشاري العالمي لمجلس العلاقات الخارجية",
    "The New Red Sea Wars - International Crisis Group": "الحروب الجديدة في البحر الأحمر",
    "Russia’s military recruitment networks exploit Western tech platforms - ISS Africa": "شبكات التجنيد العسكري الروسية تستغل منصات التكنولوجيا الغربية",
    "US Defense Spending, Russia-NATO Tensions, and China’s Military Expansion - Hudson Institute": "الإنفاق الدفاعي الأمريكي والتوترات بين روسيا والناتو والتوسع العسكري الصيني",
    "China and Africa boost law enforcement ties in crackdown on cross-border cybercrime - Royal United Services Institute (RUSI)": "الصين وإفريقيا تعززان التعاون بين أجهزة إنفاذ القانون لمكافحة الجرائم السيبرانية العابرة للحدود",
    "Africa should prepare for a new Arctic shipping era - ISS Africa": "ينبغي لإفريقيا الاستعداد لعصر جديد من الملاحة البحرية في القطب الشمالي",
    "Ethiopia’s widening war threatens the Horn of Africa - Clingendael": "اتساع رقعة الحرب في إثيوبيا يهدد القرن الإفريقي",
    "Five Energy Market Factors That Could Tip the Scales in the Middle East War - International Crisis Group": "خمسة عوامل في سوق الطاقة قد ترجّح كفة أحد الأطراف في حرب الشرق الأوسط",
    "Japan as a Maritime Great Power and the China Challenge - CSIS | Center for Strategic and International Studies": "اليابان بوصفها قوة بحرية كبرى والتحدي الصيني"
}


def translate_one_title(title):
    """
    Try a lightweight public translation endpoint first, then Google Translate.
    GoogleTranslator was returning TooManyRequests for every title in GitHub Actions.
    """
    title = str(title or "").strip()
    if not title:
        return ""
    if title in KNOWN_ARABIC_TITLES:
        return KNOWN_ARABIC_TITLES[title]

    # MyMemory fallback is called through requests, already used by this collector.
    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": title, "langpair": "en|ar"},
            timeout=15,
            headers=HEADERS
        )
        response.raise_for_status()
        payload = response.json()
        translated = clean(
            (payload.get("responseData") or {}).get("translatedText", "")
        )
        if translated and contains_arabic(translated):
            return translated
    except Exception as exc:
        print(f"MyMemory translation failed: {type(exc).__name__}")

    # Secondary fallback: Google Translate, with only two attempts to avoid
    # spending several minutes retrying a rate-limited endpoint.
    for attempt in range(1, 3):
        try:
            translated = clean(
                GoogleTranslator(source="auto", target="ar").translate(title) or ""
            )
            if translated and contains_arabic(translated):
                return translated
        except Exception as exc:
            print(f"Google translation attempt {attempt}/2 failed: {type(exc).__name__}")
        if attempt < 2:
            time.sleep(2)
    return ""


def translate_titles_to_arabic(items):
    """
    Keep original publisher titles in 'title'; populate 'title_ar' for display.
    Existing translations are retained and failed titles are retried next run.
    """
    translated_count = 0
    failed_titles = []

    for index, item in enumerate(items, start=1):
        title = clean(item.get("title", ""))
        if not title:
            continue

        # Arabic-origin titles can be used directly.
        if contains_arabic(title):
            if not clean(item.get("title_ar", "")):
                item["title_ar"] = title
            continue

        if contains_arabic(item.get("title_ar", "")):
            continue

        translated = translate_one_title(title)
        if translated:
            item["title_ar"] = translated
            translated_count += 1
        else:
            item.pop("title_ar", None)
            failed_titles.append(title)

        # Small delay between calls to reduce pressure on free endpoints.
        time.sleep(0.25)
        if index % 25 == 0:
            print(
                f"Title translation progress: {index}/{len(items)} "
                f"(translated this run: {translated_count}, failures: {len(failed_titles)})"
            )

    available = sum(1 for item in items if contains_arabic(item.get("title_ar", "")))
    print(
        f"Arabic titles available: {available}/{len(items)}; "
        f"translated this run: {translated_count}; failed: {len(failed_titles)}"
    )
    if failed_titles:
        print("Some titles could not be translated and will be retried next run.")

def normalize_publication_title(value):
    """Normalize a source title for duplicate detection across RSS locales."""
    value = unescape(str(value or "")).lower()
    value = re.sub(r"\\s*[-|:]\\s*(?:konrad-adenauer-stiftung|iss africa|nupi|real instituto elcano|institute for security & development policy)\\s*$", "", value)
    value = re.sub(r"[^\\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def is_generic_listing_title(title):
    """Reject search results that point to a center's listing/archive page, not a publication."""
    value = unescape(str(title or "")).strip().lower()
    return bool(re.match(
        r"^(?:publications|latest publications|all publications|research publications)\\s*(?:[-|:]|$)",
        value
    ))


def publication_identity(item):
    center = str(item.get("center_short") or item.get("center") or "").strip().casefold()
    title = normalize_publication_title(item.get("title") or item.get("title_ar"))
    return (center, title) if center and title else None


def direct_url(url):
    if not url:
        return ""
    try:
        response = requests.get(url, timeout=10, headers=HEADERS, allow_redirects=True, stream=True)
        result = response.url if response.url.startswith(("https://", "http://")) else url
        response.close()
        # Avoid publishing a Google News wrapper when it did not resolve.
        return result
    except Exception:
        return url

def main():
    now = datetime.now(TZ)
    rolling_cutoff = now - timedelta(days=MAX_AGE_DAYS)
    seed_start = datetime.strptime(SEED_START_DATE, "%Y-%m-%d").replace(tzinfo=TZ)
    cutoff = max(rolling_cutoff, seed_start)

    # Preserve previously collected items so a six-hour run adds new links
    # instead of replacing the whole list with a smaller search result.
    existing_items = []
    if OUTPUT.exists():
        try:
            old_data = json.loads(OUTPUT.read_text(encoding="utf-8"))
            old_items = old_data.get("publications", []) if isinstance(old_data, dict) else old_data
            if isinstance(old_items, list):
                existing_items = [item for item in old_items if isinstance(item, dict)]
        except Exception as exc:
            print(f"Could not read prior publication list: {type(exc).__name__}")

    publications = []
    seen = set()
    session = requests.Session()
    session.headers.update(HEADERS)

    for center in CENTERS:
        # Google News RSS supports after: dates; this gives the initial
        # 1–13 October backfill and then includes newer posts on every run.
        after_date = (cutoff.date() - timedelta(days=1)).isoformat()
        # Search the publisher's domain broadly, then apply the local
        # strategic-relevance classifier below. Combining long OR expressions
        # with site: filters made Google News omit many recent publisher items.
        # A domain-wide date search is more reliable for discovering today's
        # publications; the classifier still excludes irrelevant material.
        query = f"site:{center['domain']} after:{after_date}"
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        try:
            response = session.get("https://news.google.com/rss/search", params=params, timeout=22)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as exc:
            print(f"Search failed for {center['short']}: {type(exc).__name__}")
            continue

        center_items = []
        for entry in root.findall("./channel/item"):
            title = clean(entry.findtext("title"))
            wrapper = clean(entry.findtext("link"))
            published_raw = clean(entry.findtext("pubDate"))
            published_dt = parse_date(published_raw)
            if not title or not wrapper or is_generic_listing_title(title):
                continue
            if published_dt and published_dt < cutoff:
                continue
            url = direct_url(wrapper)
            if not url or url in seen:
                continue
            source_node = entry.find("source")
            source_name = clean(source_node.text if source_node is not None else center["short"])
            description = clean(entry.findtext("description"))
            item = {
                "center": center["name"],
                "center_short": center["short"],
                "title": title,
                "url": url,
                "published": published_dt.isoformat(timespec="minutes") if published_dt else published_raw,
                "date": published_dt.strftime("%Y-%m-%d") if published_dt else "",
                "source": source_name,
                "description": description[:500],
                "center_site": "https://" + center["domain"] + "/"
            }
            classify_relevance(item)
            if item.get("relevance_tier", 0) > 0:
                center_items.append(item)

        center_items.sort(key=lambda x: x.get("published", ""), reverse=True)
        for item in center_items[:MAX_PER_CENTER]:
            key = item["url"]
            if key in seen:
                continue
            seen.add(key)
            publications.append(item)
        print(f"{center['short']}: {min(len(center_items), MAX_PER_CENTER)} publications")

    # Merge the existing list after fresh results, deduplicate by URL, and
    # retain the newest item for each URL. This prevents six-hour refreshes
    # from deleting valid older entries that Google News no longer returns.
    merged = {}
    for item in existing_items + publications:
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        published_dt = parse_date(item.get("published") or item.get("date"))
        if published_dt and published_dt < cutoff:
            continue
        if url not in merged:
            merged[url] = item
        else:
            old_dt = parse_date(merged[url].get("published") or merged[url].get("date"))
            if published_dt and (not old_dt or published_dt > old_dt):
                merged[url] = item

    publications = list(merged.values())

    # Deduplicate the same publication even when Google News returns different
    # locale wrappers or dates for the same center/title. Keep the newest record.
    unique = {}
    for item in publications:
        if is_generic_listing_title(item.get("title", "")):
            continue
        if classify_relevance(item) <= 0:
            continue
        identity = publication_identity(item)
        if not identity:
            continue
        current = unique.get(identity)
        if current is None:
            unique[identity] = item
            continue
        new_dt = parse_date(item.get("published") or item.get("date"))
        old_dt = parse_date(current.get("published") or current.get("date"))
        if new_dt and (not old_dt or new_dt > old_dt):
            unique[identity] = item
    publications = list(unique.values())
    # Sort by the requested relevance hierarchy, then newest first within each tier.
    publications.sort(
        key=lambda x: (
            int(x.get("relevance_tier", 99)),
            -(parse_date(x.get("published") or x.get("date")).timestamp()
              if parse_date(x.get("published") or x.get("date")) else 0)
        )
    )
    publications = publications[:MAX_TOTAL]

    # Translate both newly discovered publications and the initial 1–13 October
    # backfill. Existing Arabic titles are preserved on each six-hour refresh.
    translate_titles_to_arabic(publications)

    payload = {
        "updated": now.isoformat(timespec="minutes"),
        "window_days": MAX_AGE_DAYS,
        "seed_start_date": SEED_START_DATE,
        "center_count": len(CENTERS),
        "centers": [
            {"name": center["name"], "short": center["short"],
             "domain": center["domain"], "site": "https://" + center["domain"] + "/"}
            for center in CENTERS
        ],
        "count": len(publications),
        "publications": publications
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(publications)} research publications to {OUTPUT.name}")

if __name__ == "__main__":
    main()
