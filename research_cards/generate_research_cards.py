import hashlib, json, os, re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from selectolax.lexbor import LexborHTMLParser
from google import genai
from google.genai import types

ROOT=Path(__file__).resolve().parent.parent
PUB=ROOT/"research_publications.json"
DATA=ROOT/"research_cards.json"
ARCHIVE=ROOT/"research_cards"/"archive"
INDEX=ARCHIVE/"index.json"
TZ=ZoneInfo("Africa/Tunis")
MODEL=os.getenv("GEMINI_MODEL","gemini-3.5-flash-lite")
MAX_PER_RUN=3
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; AfricanSecurityObserver/1.0)"}

def load(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError): return default

def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def clean(value):
    value=re.sub(r"<[^>]+>"," ",str(value or ""))
    return re.sub(r"\s+"," ",value).strip()

def get_text(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=25,allow_redirects=True); r.raise_for_status()
        p=LexborHTMLParser(r.text)
        for sel in ("script","style","nav","footer","header","aside","form","noscript"):
            for n in p.css(sel): n.decompose()
        candidates=[]
        for sel in ("article","main","[role=main]",".article-body",".entry-content",".post-content"):
            for n in p.css(sel):
                t=clean(n.text(separator=" "))
                if len(t)>max([len(x) for x in candidates] or [0]): candidates=[t]
        if not candidates: candidates=[clean(n.text(separator=" ")) for n in p.css("p")]
        return r.url,max(candidates,key=len,default="")[:26000]
    except Exception as e:
        print(f"Article extraction failed: {type(e).__name__}")
        return url,""

def make_index(cards):
    years={}
    for c in cards:
        d=clean(c.get("date"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",d): continue
        y,m,_=d.split("-")
        years.setdefault(y,{}).setdefault(m,[]).append({k:c.get(k,"") for k in ("date","title","subject","path","center","source_title")}|{"slot":"research","kind_label":"بطاقة استراتيجية من مراكز البحث"})
    out={"updated":datetime.now(TZ).isoformat(timespec="seconds"),"years":[]}
    for y in sorted(years,reverse=True):
        months=[]
        for m in sorted(years[y],reverse=True):
            months.append({"month":m,"cards":sorted(years[y][m],key=lambda x:(x["date"],x["title"]),reverse=True)})
        out["years"].append({"year":y,"months":months})
    return out

def main():
    key=os.getenv("GEMINI_API_KEY")
    if not key: raise RuntimeError("GEMINI_API_KEY is missing.")
    pd=load(PUB,{}); publications=pd.get("publications",[]) if isinstance(pd,dict) else []
    data=load(DATA,{"updated":None,"cards":[],"processed_urls":[]})
    if not isinstance(data,dict): data={"updated":None,"cards":[],"processed_urls":[]}
    data.setdefault("cards",[]); data.setdefault("processed_urls",[])
    known=set(data["processed_urls"])
    known.update(c.get("source_url","") for c in data["cards"] if isinstance(c,dict))
    candidates=[p for p in publications if isinstance(p,dict) and p.get("url") and p["url"] not in known]
    # Process highest relevance first, newest first within each relevance tier.
    ordered=[]
    for tier in sorted({int(p.get("relevance_tier",9) or 9) for p in candidates}):
        ordered.extend(sorted(
            [p for p in candidates if int(p.get("relevance_tier",9) or 9)==tier],
            key=lambda p:str(p.get("published") or p.get("date") or ""),
            reverse=True
        ))
    candidates=ordered
    if not candidates:
        print("No new research publications to convert."); return
    client=genai.Client(api_key=key,http_options=types.HttpOptions(timeout=180000))
    schema={"type":"object","properties":{
      "publish":{"type":"boolean"},"introduction":{"type":"string"},
      "analysis_one":{"type":"array","items":{"type":"string"}},
      "analysis_two":{"type":"array","items":{"type":"string"}},
      "analysis_three":{"type":"array","items":{"type":"string"}},
      "strategic_conclusion":{"type":"string"},"summary":{"type":"string"}},
      "required":["publish","introduction","analysis_one","analysis_two","analysis_three","strategic_conclusion","summary"]}
    count=0
    for p in candidates:
        if count>=MAX_PER_RUN: break
        title=clean(p.get("title")); title_ar=clean(p.get("title_ar")); center=clean(p.get("center") or p.get("source") or "مركز بحثي")
        url=clean(p.get("url")); desc=clean(p.get("description")); published=clean(p.get("published") or p.get("date"))
        resolved,body=get_text(url)
        if len(body)<650 and len(desc)<220:
            print("Insufficient text; marking source as checked:",title[:100])
            data["processed_urls"].append(url); count+=1; continue
        prompt=f"""
أنت محرر بطاقة استراتيجية عربية في المرصد الإفريقي للأمن والدفاع. أنشئ بطاقة واحدة تلخص هذه النشرية وحدها.
العنوان الأصلي الذي نشره المركز يجب أن يبقى محفوظاً كما هو: {title}
العنوان العربي المتاح: {title_ar}
المركز: {center}; تاريخ النشر: {published}; الرابط: {resolved}
أعد JSON:
publish boolean; introduction string; analysis_one, analysis_two, analysis_three مصفوفات نصوص؛ strategic_conclusion string؛ summary string.
الشكل خمسة عناصر فقط: (1) مقدمة عربية تذكر الموضوع والمركز وتنتهي حرفياً «وفيما يلي أهم ما ورد فيها.»؛
(2)(3)(4) ثلاثة عناصر تحليلية، كل عنصر مصفوفة من أربع نقاط بالضبط، كل نقطة نحو 45-65 كلمة عربية، تشرح جوانب مختلفة من النشرية؛
(5) حوصلة استراتيجية 120-170 كلمة، تستند إلى المادة، وتنتهي بجملة تربط الموضوع بتونس وتحتوي كلمة «تونس».
لا تخترع حقائق أو أرقاماً أو دوافع. ميّز بين وقائع الكاتب وتحليله. اشرح الاختصارات عند أول ذكر. لا تكرر المعنى. أعد publish=false إذا كانت المادة سطحية أو لا تسمح ببطاقة موثوقة.
وصف المصدر: {desc[:2200]}
النص المستخرج من صفحة المصدر:
{body[:22000]}
"""
        try:
            response=client.interactions.create(model=MODEL,input=prompt,response_format={"type":"text","mime_type":"application/json","schema":schema},generation_config={"temperature":0.2,"thinking_level":"low"})
            out=json.loads(response.output_text or "{}")
        except Exception as e:
            print("Generation failed; will retry later:",type(e).__name__); continue
        if not out.get("publish"):
            data["processed_urls"].append(url); count+=1; print("Source rejected as insufficient:",title[:100]); continue
        intro=clean(out.get("introduction")); groups=[out.get("analysis_one",[]),out.get("analysis_two",[]),out.get("analysis_three",[])]
        if not intro.endswith("وفيما يلي أهم ما ورد فيها."): intro=re.sub(r"[.!؟؛،\s]+$","",intro)+". وفيما يلي أهم ما ورد فيها."
        if any(not isinstance(g,list) or len(g)!=4 for g in groups):
            print("Invalid bullet structure; will retry later:",title[:100]); continue
        sections=[{"heading":"المقدمة","text":intro}]
        for h,g in zip(["أولاً: محاور النشرية ومرتكزاتها","ثانياً: النتائج والتفاصيل الرئيسية","ثالثاً: التداعيات والقيود"],groups):
            sections.append({"heading":h,"text":"\n".join("• "+clean(x) for x in g)})
        conclusion=clean(out.get("strategic_conclusion"))
        conclusion=conclusion.rstrip(" .،؛")+". وتبرز أهمية هذه المسائل بالنسبة إلى تونس في متابعة انعكاساتها على أمنها ومصالحها الاستراتيجية."
        if len(intro)<80 or len(conclusion)<180 or any(len(clean(x))<180 for g in groups for x in g):
            print("Card failed length validation; will retry later:",title[:100]); continue
        slug=hashlib.sha1(url.encode()).hexdigest()[:12]
        now=datetime.now(TZ); date=published[:10] if re.match(r"\d{4}-\d{2}-\d{2}",published) else now.strftime("%Y-%m-%d")
        path=f"{date[:7]}/{date.replace('-','')}_{slug}.json"
        card={"id":slug,"kind":"research_center","kind_label":"بطاقة استراتيجية من مراكز البحث",
          "title":title_ar or title,"source_title":title,"subject":title_ar or title,"center":center,
          "date":date,"published":published,"region":clean(p.get("relevance_label")),"summary":clean(out.get("summary")),
          "sections":sections,"sources":[{"title":f"{center} — {title}","url":resolved if resolved.startswith("http") else url}],
          "source_url":url,"source_original_url":resolved if resolved.startswith("http") else url,"path":path}
        save(ARCHIVE/path,card); data["cards"].append(card); data["processed_urls"].append(url); count+=1
        print("Published research card:",card["title"][:150])
    data["cards"]=sorted(data["cards"],key=lambda c:(c.get("date",""),c.get("title","")),reverse=True)[:800]
    data["processed_urls"]=list(dict.fromkeys(data["processed_urls"]))[-2000:]
    data["updated"]=datetime.now(TZ).isoformat(timespec="seconds")
    save(DATA,data); save(INDEX,make_index(data["cards"]))
    print(f"Research cards total={len(data['cards'])}; processed this run={count}")

if __name__=="__main__": main()
