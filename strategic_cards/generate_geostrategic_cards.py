import json, os, re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from selectolax.parser import HTMLParser
from google import genai
from google.genai import types

ROOT=Path(__file__).resolve().parent.parent
NEWS_FILE=ROOT/"news.json"
DATA_FILE=ROOT/"strategic_cards.json"
ARCHIVE_DIR=ROOT/"strategic_cards"/"archive"
TZ=ZoneInfo("Africa/Tunis")
MODEL=os.getenv("GEMINI_MODEL","gemini-3.5-flash-lite")
MAX_CANDIDATES=10
MAX_AGE_DAYS=10

LABELS={
 "deals":"صفقات التسلح الكبرى",
 "systems":"المنظومات والابتكارات العسكرية",
 "geo_economics":"الأمن الاقتصادي والجيو-اقتصادي",
 "military_reform":"التحولات العسكرية والتنظيمية والتكنولوجية",
 "alliances_logistics":"الاستراتيجيات الدفاعية والتحالفات والجاهزية",
 "regional_crisis":"الأزمات الإقليمية والتقييمات الجيوسياسية",
 "defense_policy":"السياسات الدفاعية",
 "national_security":"استراتيجيات الأمن القومي",
}
TERMS={
 "geo_economics":["defense economics","economic security","economic statecraft","critical minerals","supply chain","defense industry","strategic investment","sanctions","trade restrictions","defense finance","اقتصاد الدفاع","الأمن الاقتصادي","المعادن الحيوية","سلاسل الإمداد","الصناعات الدفاعية","العقوبات الاقتصادية","النفوذ الاقتصادي","البنية التحتية","التمويل العسكري","تبييض الأموال","تمويل الجماعات","التنافس الاقتصادي"],
 "military_reform":["military reform","command restructure","military command","autonomous warfare","artificial intelligence","unmanned systems","military robotics","force posture","military leadership","military doctrine","إعادة هيكلة الجيش","إعادة هيكلة القوات المسلحة","قيادة عسكرية","القيادات العسكرية","الحرب الذاتية","الأنظمة ذاتية التشغيل","الأنظمة غير المأهولة","الذكاء الاصطناعي","العقيدة العسكرية","إصلاح عسكري","تطوير القوات المسلحة","إعادة تنظيم القيادة","التحول التكنولوجي العسكري","الروبوتات العسكرية"],
 "alliances_logistics":["military mobility","military logistics","defense alliance","defence alliance","collective defense","military corridor","strategic transport","defense readiness","nato","joint command","military exercise","الجاهزية الدفاعية","النقل العسكري","الإمداد اللوجستي","اللوجستيات العسكرية","التحالفات العسكرية","الدفاع الجماعي","حلف شمال الأطلسي","ممر عسكري","التعاون الدفاعي","المناورات العسكرية","قابلية الانتشار","التنقل العسكري"],
 "regional_crisis":["diplomatic crisis","severed diplomatic relations","break diplomatic relations","regional tensions","geopolitical crisis","armed conflict","regional escalation","proxy forces","mediation","diplomatic dispute","قطع العلاقات","أزمة دبلوماسية","أزمة إقليمية","توتر إقليمي","خلاف دبلوماسي","تضارب المصالح","الوساطة","وقف إطلاق النار","نزاع مسلح","التصعيد الإقليمي","الأزمة الليبية","الأزمة السودانية","الساحل والصحراء","الصحراء الغربية","توازنات إقليمية","التنافس الإقليمي"],
 "defense_policy":["defense policy","defence policy","defense strategy","defence strategy","defense budget","defence spending","national defense strategy","military spending","security strategy","arms control","defense minister","السياسة الدفاعية","الاستراتيجية الدفاعية","الإنفاق الدفاعي","ميزانية الدفاع","وزير الدفاع","الردع","الأمن الإقليمي","التخطيط الدفاعي","التعاون العسكري","الاستعداد للحرب","التخطيط العسكري"],
 "national_security":["national security strategy","national security council","counterterrorism strategy","intelligence cooperation","border security","maritime security","security doctrine","strategic review","security pact","counter-terrorism","استراتيجية الأمن القومي","مجلس الأمن القومي","مكافحة الإرهاب","التعاون الاستخباراتي","أمن الحدود","الأمن البحري","مراجعة استراتيجية","عقيدة الأمن القومي","اتفاق أمني","الأمن الوطني","الأمن القومي","تهديدات عابرة للحدود"]
}
DESCRIPTIONS={
 "geo_economics":"الأمن الاقتصادي والجيو-اقتصادي",
 "military_reform":"التحولات العسكرية والتنظيمية والتكنولوجية",
 "alliances_logistics":"الاستراتيجيات الدفاعية والتحالفات والجاهزية العسكرية",
 "regional_crisis":"الأزمات الإقليمية والتقييمات الجيوسياسية",
 "defense_policy":"السياسات الدفاعية وتوجهات الإنفاق والتخطيط العسكري",
 "national_security":"استراتيجيات الأمن القومي ومقاربات مواجهة التهديدات"
}

def clean(v):
 v=re.sub(r"<[^>]+>"," ",str(v or ""))
 return re.sub(r"\s+"," ",v.replace("&nbsp;"," ").replace("&amp;","&")).strip()

def load(path,default):
 try:
  with open(path,encoding="utf-8") as f:return json.load(f)
 except Exception:return default

def save(path,obj):
 path.parent.mkdir(parents=True,exist_ok=True)
 with open(path,"w",encoding="utf-8") as f:json.dump(obj,f,ensure_ascii=False,indent=2)

def date_of(value):
 if not value:return None
 try:
  d=parsedate_to_datetime(str(value))
  if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
  return d.astimezone(TZ)
 except Exception:
  try:
   d=datetime.fromisoformat(str(value).replace("Z","+00:00"))
   if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
   return d.astimezone(TZ)
  except Exception:return None

def article_text(url):
 try:
  if not url.startswith(("https://","http://")):return ""
  r=requests.get(url,timeout=14,headers={"User-Agent":"Mozilla/5.0 (compatible; AfricanSecurityObserver/1.0)"})
  if r.status_code!=200:return ""
  p=HTMLParser(r.text)
  for sel in ("script","style","nav","header","footer","aside","form"):
   for n in p.css(sel):n.decompose()
  n=p.css_first("article") or p.css_first("main") or p.body
  return clean(n.text(separator=" ",strip=True))[:7500] if n else ""
 except Exception as e:
  print(f"Source extraction failed: {type(e).__name__}")
  return ""

def score(item):
 title=clean(item.get("original_title") or item.get("title")).lower()
 body=(title+" "+clean(item.get("original_description") or item.get("description"))).lower()
 scores={}
 for kind,terms in TERMS.items():
  scores[kind]=sum(3 if t.lower() in title else 1 if t.lower() in body else 0 for t in terms)
  if item.get("category")=="defense":scores[kind]+=1
 kind=max(scores,key=scores.get)
 url=clean(item.get("url"))
 return (kind,scores[kind]) if url.startswith(("https://","http://")) else (None,0)

def sync_archive(data):
 years={}
 for kind,label in LABELS.items():
  for card in data.get(kind,[]) if isinstance(data.get(kind),list) else []:
   if not isinstance(card,dict):continue
   d=clean(card.get("date"))[:10]
   try:
    year,month,_=d.split("-");datetime.strptime(d,"%Y-%m-%d")
   except Exception:continue
   cid=clean(card.get("id")) or d
   rel=f"{year}-{month}/{cid}.json"
   archived=dict(card);archived["kind"]=kind;archived["kind_label"]=label
   save(ARCHIVE_DIR/rel,archived)
   years.setdefault(year,{"year":year,"months":{}})
   years[year]["months"].setdefault(month,{"month":month,"cards":[]})
   years[year]["months"][month]["cards"].append({"date":d,"title":clean(card.get("title")),"path":rel,"kind":kind,"kind_label":label})
 out=[]
 for y in sorted(years,reverse=True):
  months=[]
  for m in sorted(years[y]["months"],reverse=True):
   cards=years[y]["months"][m]["cards"]
   cards.sort(key=lambda x:(x["date"],x["title"]),reverse=True)
   months.append({"month":m,"cards":cards})
  out.append({"year":y,"months":months})
 save(ARCHIVE_DIR/"index.json",{"updated":datetime.now(TZ).isoformat(),"years":out})

def prompt_for(material):
 return f"""
أنت رئيس تحرير ومحلل استراتيجي في «المرصد الإفريقي للأمن والدفاع». أنشئ بطاقة عربية رسمية عميقة ومقنعة انطلاقًا حصريًا من المواد المرفقة. هذه البطاقة مخصصة للتحولات الجيوسياسية والأمنية والدفاعية، وليست بطاقة مواصفات سلاح أو صفقة تسلح؛ لهما مولّد مستقل.
تاريخ تونس اليوم: {datetime.now(TZ).strftime('%Y-%m-%d')}
اختر تصنيفًا واحدًا:
{json.dumps(DESCRIPTIONS,ensure_ascii=False)}

منهج التحليل:
- ابدأ بعنوان دقيق وملخص يحدد الحدث وأهميته.
- انتقل منطقيًا من الوقائع إلى السياق والدوافع ثم التحليل والتداعيات والخلاصة.
- اكتب عادة 4 إلى 7 أقسام مفيدة، وتكيّف مع الموضوع بدل تكرار قالب جامد.
- الأمن الاقتصادي: حلل أدوات النفوذ والتمويل والتجارة والموارد والبنية التحتية وسلاسل الإمداد وفق ما تثبته المصادر.
- التحولات العسكرية: حلل الهيكل القيادي والمهام والموارد والعقيدة والتكنولوجيا والتطبيق العملياتي والقيود ومراحل التنفيذ.
- التحالفات والجاهزية: تناول الالتزامات والآليات والقدرات واللوجستيات والتمويل، وميّز الإعلان من التنفيذ.
- الأزمات الإقليمية: حلل الخلفية ومصالح الأطراف وتضاربها والتداعيات السياسية والاقتصادية والأمنية والسيناريوهات. انسب الاتهامات لأصحابها ولا تعرضها كحقائق.
- السياسات الدفاعية والأمن القومي: اربط الأهداف المعلنة بالأدوات والموارد والقيود ومؤشرات التنفيذ.
- وضح ما تغير فعلا وما لم يتغير، والافتراضات غير المحسومة والمؤشرات التي ينبغي متابعتها. لا تستخدم توقعات جازمة دون أدلة.
- اختم بقسم «خلاصة استراتيجية»، ثم فقرة قصيرة ضمنه عن دلالات الموضوع لتونس وخياراتها الممكنة إذا كانت ذات صلة فعلية؛ لا تجعل تونس عنوان قسم مستقلًا ولا تفرض توصيات عامة.
- اشرح اختصارات وأسماء المؤسسات والآليات الأجنبية بين قوسين عند أول ورود.
- استخدم «أورو» لا «يورو»، وصيغًا مالية مختصرة ودقيقة.
- افصل بين الحقيقة الموثقة، والتصريح المنسوب، والاتهام، والتقدير التحليلي. لا تخترع أرقامًا أو تواريخ أو اتفاقيات أو روابط أو نوايا. لا تسد النقص بمعرفة غير موجودة في المواد.
- لا تنشر إن كانت المصادر ضعيفة أو متناقضة أو لا تكفي لتحليل مفيد. إذا لم تتوفر أدلة كافية أعد publish=false.
- source_urls يجب أن تحتوي فقط على روابط من المواد المرفقة استُخدمت فعليًا. لا تكتب أي نص خارج JSON.
المواد:
{json.dumps(material,ensure_ascii=False)}
"""

def main():
 key=os.getenv("GEMINI_API_KEY")
 if not key:raise RuntimeError("GEMINI_API_KEY is missing; configure the existing GitHub Actions secret.")
 news=load(NEWS_FILE,{})
 items=news.get("items",[]) if isinstance(news,dict) else []
 data=load(DATA_FILE,{})
 if not isinstance(data,dict):data={}
 for k in LABELS:
  if not isinstance(data.get(k),list):data[k]=[]
 data.setdefault("checked_strategic_urls",[])
 checked=set(data["checked_strategic_urls"])
 cutoff=datetime.now(TZ)-timedelta(days=MAX_AGE_DAYS)
 candidates=[];seen=set()
 for item in (items if isinstance(items,list) else []):
  if not isinstance(item,dict):continue
  url=clean(item.get("url"))
  if not url or url in checked or url in seen:continue
  pub=date_of(item.get("published"))
  if pub and pub<cutoff:continue
  kind,s=score(item)
  if not kind or s<3:continue
  x=dict(item);x["_score"]=s;x["_kind_hint"]=kind;x["_url"]=url
  candidates.append(x);seen.add(url)
 candidates.sort(key=lambda x:(x["_score"],date_of(x.get("published")) or datetime.min.replace(tzinfo=TZ)),reverse=True)
 candidates=candidates[:MAX_CANDIDATES]
 if not candidates:
  sync_archive(data);save(DATA_FILE,data)
  print("No new relevant geopolitical/strategic source candidates.");return
 material=[]
 for i,x in enumerate(candidates,1):
  material.append({"id":i,"title":clean(x.get("original_title") or x.get("title"))[:500],
   "description":clean(x.get("original_description") or x.get("description"))[:2200],
   "published":clean(x.get("published")),"source":clean(x.get("source")),"url":x["_url"],
   "category_hint":x["_kind_hint"],"article_text":article_text(x["_url"])})
 schema={"type":"object","properties":{
  "publish":{"type":"boolean"},"type":{"type":"string","enum":list(DESCRIPTIONS)},
  "title":{"type":"string"},"region":{"type":"string"},"summary":{"type":"string"},
  "sections":{"type":"array","items":{"type":"object","properties":{"heading":{"type":"string"},"text":{"type":"string"}},"required":["heading","text"]}},
  "source_urls":{"type":"array","items":{"type":"string"}}},
  "required":["publish","type","title","region","summary","sections","source_urls"]}
 client=genai.Client(api_key=key,http_options=types.HttpOptions(timeout=180000))
 result=client.interactions.create(model=MODEL,input=prompt_for(material),
  response_format={"type":"text","mime_type":"application/json","schema":schema},
  generation_config={"temperature":0.2,"thinking_level":"low"})
 try:out=json.loads(result.output_text or "{}")
 except Exception:out={}
 data["checked_strategic_urls"]=list(dict.fromkeys(data["checked_strategic_urls"]+[x["_url"] for x in candidates]))[-1500:]
 if not out.get("publish"):
  sync_archive(data);save(DATA_FILE,data);print("Model rejected candidates; no card published.");return
 valid={x["url"] for x in material}
 urls=[]
 for u in out.get("source_urls",[]):
  if u in valid and u not in urls:urls.append(u)
 sections=[]
 if isinstance(out.get("sections"),list):
  for s in out["sections"]:
   if isinstance(s,dict) and clean(s.get("heading")) and clean(s.get("text")):
    sections.append({"heading":clean(s["heading"]),"text":clean(s["text"])})
 kind=out.get("type")
 if (kind not in DESCRIPTIONS or not urls or len(clean(out.get("title")))<12
     or len(clean(out.get("summary")))<80 or len(sections)<4
     or not any("خلاصة استراتيجية" in s["heading"] for s in sections)):
  sync_archive(data);save(DATA_FILE,data);print("Validation failed; no card published.");return
 by_url={x["url"]:x for x in material}
 sources=[{"title":by_url[u]["source"] or by_url[u]["title"],"url":u} for u in urls]
 card={"id":datetime.now(TZ).strftime("%Y%m%d%H%M%S"),"title":clean(out.get("title")),
  "date":datetime.now(TZ).strftime("%Y-%m-%d"),"region":clean(out.get("region")),
  "summary":clean(out.get("summary")),"sections":sections,"sources":sources,
  "kind":kind,"kind_label":LABELS[kind]}
 titles={clean(c.get("title")).casefold() for c in data[kind] if isinstance(c,dict)}
 if card["title"].casefold() in titles:
  sync_archive(data);save(DATA_FILE,data);print("Duplicate title skipped.");return
 data[kind].append(card);data[kind]=data[kind][-150:]
 sync_archive(data);save(DATA_FILE,data)
 print(f"Published strategic card: {card['title']} ({LABELS[kind]})")

if __name__=="__main__":main()
