import hashlib, json, os, re, io
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo
import requests
from googlenewsdecoder import gnewsdecoder
from selectolax.lexbor import LexborHTMLParser
from pypdf import PdfReader
from google import genai
from google.genai import types

ROOT=Path(__file__).resolve().parent.parent
PUB=ROOT/"research_publications.json"
DATA=ROOT/"research_cards.json"
ARCHIVE=ROOT/"research_cards"/"archive"
INDEX=ARCHIVE/"index.json"
TZ=ZoneInfo("Africa/Tunis")
MODEL=os.getenv("GEMINI_MODEL","gemini-3.5-flash-lite")
# Process every newly discovered, in-scope publication; do not select only a daily quota.
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

def fetch_url(url):
    """Fetch a page or PDF with bounded timeouts and redirects."""
    response = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response


def extract_pdf_text(response):
    """Extract readable text from a PDF; return empty text for scanned/unreadable files."""
    try:
        reader = PdfReader(io.BytesIO(response.content))
        pages = []
        for page in reader.pages[:100]:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
        return clean(" ".join(pages))[:60000]
    except Exception as exc:
        print(f"PDF text extraction failed: {type(exc).__name__}")
        return ""


def html_main_text(html):
    parser = LexborHTMLParser(html)
    for sel in ("script", "style", "nav", "footer", "header", "aside", "form", "noscript"):
        for node in parser.css(sel):
            node.decompose()
    candidates = []
    for sel in ("article", "main", "[role=main]", ".article-body", ".entry-content",
                ".post-content", ".report-content", ".publication-content"):
        for node in parser.css(sel):
            text = clean(node.text(separator=" "))
            if len(text) > max([len(x) for x in candidates] or [0]):
                candidates = [text]
    if not candidates:
        candidates = [clean(node.text(separator=" ")) for node in parser.css("p")]
    return max(candidates, key=len, default="")[:60000]


def get_text(url, center_site=""):
    """
    Make a best effort to locate the full publication, not just an abstract:
    decode Google News links, inspect the source page for official full-text/PDF
    links, and extract PDFs linked by that page. Never use a search snippet as
    the body of a strategic card.
    """
    original_url = url
    try:
        if "news.google.com" in urlparse(url).netloc.lower():
            try:
                decoded = gnewsdecoder(url, interval=0.5, timeout=15)
                if decoded.get("success") and decoded.get("decoded_url"):
                    url = decoded["decoded_url"]
            except Exception as exc:
                print(f"Google News URL decode failed: {type(exc).__name__}")

        response = fetch_url(url)
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or response.url.lower().split("?")[0].endswith(".pdf"):
            pdf_text = extract_pdf_text(response)
            if len(pdf_text) >= 1800:
                print(f"Full text extracted directly from PDF: {response.url}")
                return response.url, pdf_text
            return response.url, pdf_text

        html = response.text
        page_url = response.url
        page_text = html_main_text(html)
        if len(page_text) >= 1800:
            print(f"Extracted substantial page text: {page_url} ({len(page_text)} chars)")
            return page_url, page_text

        # If the search result led to a Google News wrapper, find a direct link
        # to the relevant research center before looking for a report download.
        expected_host = urlparse(center_site).netloc.lower().removeprefix("www.")
        parser = LexborHTMLParser(html)
        if expected_host and ("news.google.com" in urlparse(page_url).netloc.lower()):
            for node in parser.css("a[href]"):
                href = urljoin(page_url, node.attributes.get("href", ""))
                host = urlparse(href).netloc.lower().removeprefix("www.")
                if href.startswith("http") and (host == expected_host or host.endswith("." + expected_host)):
                    try:
                        response = fetch_url(href)
                        page_url, html = response.url, response.text
                        content_type = response.headers.get("content-type", "").lower()
                        if "pdf" in content_type or page_url.lower().split("?")[0].endswith(".pdf"):
                            body = extract_pdf_text(response)
                            if len(body) >= 1800:
                                return page_url, body
                        page_text = html_main_text(html)
                        if len(page_text) >= 1800:
                            return page_url, page_text
                        parser = LexborHTMLParser(html)
                        break
                    except Exception:
                        continue

        # Try official download/full-text links on the source publication page.
        # Prefer PDF/download/report links and reject unrelated navigation links.
        parser = LexborHTMLParser(html)
        link_candidates = []
        seen_links = set()
        page_host = urlparse(page_url).netloc.lower().removeprefix("www.")
        expected_host = expected_host or page_host
        positive = re.compile(
            r"full.?text|full.?report|download|pdf|read.?report|report|publication|"
            r"النص الكامل|تحميل|تنزيل|الدراسة كاملة|التقرير الكامل|الدراسة|التقرير",
            re.I
        )
        negative = re.compile(
            r"privacy|cookie|subscribe|newsletter|donate|login|register|"
            r"سياسة الخصوصية|اشترك|تبرع|تسجيل الدخول",
            re.I
        )
        for node in parser.css("a[href]"):
            href = urljoin(page_url, node.attributes.get("href", "").strip())
            label = clean(node.text(separator=" "))
            combined = label + " " + href
            if not href.startswith("http") or href in seen_links or negative.search(combined):
                continue
            host = urlparse(href).netloc.lower().removeprefix("www.")
            # Trust download links embedded on the official center site, including
            # its report/CDN subdomains; don't follow unrelated third-party links.
            if not (host == expected_host or host.endswith("." + expected_host)):
                continue
            if positive.search(combined) or href.lower().split("?")[0].endswith(".pdf"):
                seen_links.add(href)
                score = 0
                if href.lower().split("?")[0].endswith(".pdf"): score += 5
                if re.search(r"full.?text|full.?report|النص الكامل|الدراسة كاملة|التقرير الكامل", combined, re.I): score += 6
                if re.search(r"download|تحميل|تنزيل|pdf", combined, re.I): score += 3
                link_candidates.append((score, href, label))

        link_candidates.sort(key=lambda item: item[0], reverse=True)
        for score, candidate_url, label in link_candidates[:12]:
            try:
                linked = fetch_url(candidate_url)
                ctype = linked.headers.get("content-type", "").lower()
                if "pdf" in ctype or linked.url.lower().split("?")[0].endswith(".pdf"):
                    body = extract_pdf_text(linked)
                else:
                    body = html_main_text(linked.text)
                if len(body) >= 1800:
                    print(
                        f"Found full-text/download link on center site: {label[:80]} "
                        f"-> {linked.url} ({len(body)} chars)"
                    )
                    return linked.url, body
            except Exception as exc:
                print(f"Official full-text candidate failed ({candidate_url}): {type(exc).__name__}")

        # Preserve the page's extracted content for diagnostics only. The caller
        # will reject it if it is merely an abstract or short summary.
        print(
            f"No substantial full text found after checking official download links: "
            f"{page_url}; page chars={len(page_text)}"
        )
        return page_url or original_url, page_text
    except Exception as exc:
        print(f"Article/full-text extraction failed: {type(exc).__name__}")
        return original_url, ""

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
    failed_count=0
    for p in candidates:
        title=clean(p.get("title")); title_ar=clean(p.get("title_ar")); center=clean(p.get("center") or p.get("source") or "مركز بحثي")
        url=clean(p.get("url")); desc=clean(p.get("description")); published=clean(p.get("published") or p.get("date"))
        resolved,body=get_text(url,clean(p.get("center_site")))

        # Hard rule: a strategic card may only be written from the publication's
        # accessible full text. A title, snippet, search-result extract, or short
        # description is not enough evidence for a multi-section analytical card.
        # Leave incomplete items unprocessed so a later six-hour run can retry.
        MIN_FULL_TEXT_CHARS = 1800
        if len(body.strip()) < MIN_FULL_TEXT_CHARS:
            print(
                "Full publication text unavailable after checking the source page "
                "and linked official full-text/PDF downloads; skipping card and "
                "leaving publication pending for retry:",
                title[:140],
                f"(extracted_chars={len(body.strip())}, source={resolved})"
            )
            failed_count += 1
            continue
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
            print("Generation failed; will retry later:",type(e).__name__)
            failed_count+=1
            continue
        if not out.get("publish"):
            # Never mark a relevant publication as permanently processed just
            # because one model response judged its content insufficient.
            # It stays pending and is retried on a later six-hour run.
            print("Model could not yet produce a reliable card; leaving publication pending:",title[:100])
            failed_count+=1
            continue
        intro=clean(out.get("introduction")); groups=[out.get("analysis_one",[]),out.get("analysis_two",[]),out.get("analysis_three",[])]
        if not intro.endswith("وفيما يلي أهم ما ورد فيها."): intro=re.sub(r"[.!؟؛،\s]+$","",intro)+". وفيما يلي أهم ما ورد فيها."
        if any(not isinstance(g,list) or len(g)!=4 for g in groups):
            print("Invalid bullet structure; will retry later:",title[:100])
            failed_count+=1
            continue
        sections=[{"heading":"المقدمة","text":intro}]
        for h,g in zip(["أولاً: محاور النشرية ومرتكزاتها","ثانياً: النتائج والتفاصيل الرئيسية","ثالثاً: التداعيات والقيود"],groups):
            sections.append({"heading":h,"text":"\n".join("• "+clean(x) for x in g)})
        conclusion=clean(out.get("strategic_conclusion"))
        conclusion=conclusion.rstrip(" .،؛")+". وتبرز أهمية هذه المسائل بالنسبة إلى تونس في متابعة انعكاساتها على أمنها ومصالحها الاستراتيجية."
        if len(intro)<80 or len(conclusion)<180 or any(len(clean(x))<180 for g in groups for x in g):
            print("Card failed length validation; will retry later:",title[:100])
            failed_count+=1
            continue
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
    data["cards"]=sorted(data["cards"],key=lambda c:(c.get("date",""),c.get("title","")),reverse=True)
    data["processed_urls"]=list(dict.fromkeys(data["processed_urls"]))
    data["updated"]=datetime.now(TZ).isoformat(timespec="seconds")
    save(DATA,data); save(INDEX,make_index(data["cards"]))
    print(f"Research cards total={len(data['cards'])}; generated this run={count}; pending for retry={failed_count}; new candidates seen={len(candidates)}")

if __name__=="__main__": main()
