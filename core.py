"""
Rojgar School — Core Scraper Engine
Extracts complete structured data from SarkariResult detail pages.
Saves to Supabase with full dates, fees, eligibility, PDFs.
"""
import hashlib
import asyncio
import logging
import re
import os
from datetime import datetime
from typing import Optional
import httpx
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scraper")

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://urfzljcwduycxywyzlnt.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVyZnpsamN3ZHV5Y3h5d3l6bG50Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgzOTgyOTksImV4cCI6MjA5Mzk3NDI5OX0.63njN4bw_MAWQgobNUawXdqZeCr9_Q_egsRPCPCtn7g")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID", "@rojgarschool")
TELEGRAM_STORAGE = os.getenv("TELEGRAM_STORAGE_CHANNEL", "")

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# Rotate user agents to avoid blocks
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

import random

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")[:180]


def clean_html(text: str) -> str:
    """Remove HTML entities and tags"""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&#038;", "&").replace("&#8211;", "-").replace("&#8212;", "-")
    text = text.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#8217;", "'")
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_post_type(title: str) -> str:
    t = title.lower()
    if any(k in t for k in ["result", "final result", "merit list", "marks", "cutoff", "selected candidates"]):
        return "result"
    if any(k in t for k in ["admit card", "call letter", "hall ticket", "e-admit"]):
        return "admit_card"
    if any(k in t for k in ["answer key", "answer sheet", "provisional answer"]):
        return "answer_key"
    if any(k in t for k in ["syllabus", "exam pattern"]):
        return "syllabus"
    if any(k in t for k in ["admission", "prospectus"]):
        return "admission"
    return "job"


def extract_vacancies(text: str) -> Optional[int]:
    patterns = [
        r"(\d[\d,]+)\s*(?:posts?|vacancies|seats?)",
        r"(?:total|vacancies)[:\s]+(\d[\d,]+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return None


def parse_date(s: str) -> Optional[str]:
    """Parse DD/MM/YYYY to ISO"""
    s = clean_html(s).strip()
    m = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})", s)
    if m:
        try:
            d = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return d.isoformat()
        except ValueError:
            pass
    return None


async def fetch_page(url: str, client: httpx.AsyncClient, retries: int = 3) -> Optional[str]:
    """Fetch a page with retries and rotating headers"""
    for attempt in range(retries):
        try:
            resp = await client.get(url, headers=get_headers(), timeout=30, follow_redirects=True)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 403:
                logger.warning(f"403 blocked: {url}")
                await asyncio.sleep(5 * (attempt + 1))
            else:
                logger.warning(f"HTTP {resp.status_code}: {url}")
        except Exception as e:
            logger.error(f"Fetch error ({attempt+1}/{retries}): {url} — {e}")
            await asyncio.sleep(3 * (attempt + 1))
    return None


def parse_sarkari_result_detail(html: str, url: str) -> dict:
    """
    Parse a SarkariResult.com post detail page.
    Extracts ALL structured data: dates, fees, age, qualification, PDF, apply link.
    """
    soup = BeautifulSoup(html, "html.parser")
    data = {
        "description": None,
        "vacancies": None,
        "apply_start": None,
        "last_date": None,
        "exam_date": None,
        "important_dates": [],
        "eligibility": [],
        "salary": None,
        "apply_url": None,
        "pdf_url": None,
        "image_url": None,
        "fee_general": None,
        "fee_sc": None,
        "age_limit": None,
        "qualification": None,
    }

    # ── Short Information ──────────────────────────────────────────────────
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = clean_html(cells[0].get_text()).lower()
            value = clean_html(cells[1].get_text())
            if "short information" in label or "short info" in label:
                data["description"] = value[:600]
                break

    # ── Important Dates table ─────────────────────────────────────────────
    date_patterns = {
        "application begin": "Application Begin",
        "apply online": "Last Date Apply Online",
        "last date": "Last Date",
        "pay exam fee": "Pay Exam Fee Last Date",
        "correction": "Correction Window",
        "exam date": "Exam Date",
        "cbt exam": "CBT Exam Date",
        "result date": "Result Date",
        "interview": "Interview Date",
        "admit card": "Admit Card Date",
    }
    
    for row in soup.find_all("li"):
        text = clean_html(row.get_text())
        # Match date patterns like "Application Begin : 24/04/2026"
        for key, label in date_patterns.items():
            if key in text.lower():
                date_match = re.search(r"(\d{2}/\d{2}/\d{4})", text)
                if date_match:
                    date_str = date_match.group(1)
                    data["important_dates"].append({"label": label, "date": date_str})
                    if "last date" in key or "apply online" in key:
                        data["last_date"] = parse_date(date_str)
                    elif "application begin" in key:
                        data["apply_start"] = parse_date(date_str)
                    elif "exam date" in key or "cbt" in key:
                        data["exam_date"] = parse_date(date_str)
                    break

    # ── Vacancies ─────────────────────────────────────────────────────────
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = clean_html(cells[0].get_text()).lower()
            value = clean_html(cells[1].get_text())
            if "total post" in label or "total vacanc" in label or "vacancy" in label:
                vac = extract_vacancies(value)
                if vac:
                    data["vacancies"] = vac
                    break
    
    # Also try from page text
    if not data["vacancies"]:
        page_text = clean_html(soup.get_text())
        vac = extract_vacancies(page_text)
        if vac:
            data["vacancies"] = vac

    # ── Application Fee ───────────────────────────────────────────────────
    full_text = soup.get_text()
    fee_gen = re.search(r"General[^:]*:\s*(?:Rs\.?\s*)?(\d+)/-", full_text, re.IGNORECASE)
    if fee_gen:
        data["fee_general"] = f"₹{fee_gen.group(1)}/-"
        data["eligibility"].append({"label": "Application Fee (General/OBC/EWS)", "value": f"₹{fee_gen.group(1)}/-"})

    fee_sc = re.search(r"SC\s*/\s*ST[^:]*:\s*(?:Rs\.?\s*)?(\d+/-)|(Nil|Zero|Exempted|Free)", full_text, re.IGNORECASE)
    if fee_sc:
        val = fee_sc.group(1) or fee_sc.group(2) or "Nil"
        data["eligibility"].append({"label": "Application Fee (SC/ST/PH)", "value": val})

    fee_fem = re.search(r"(?:All Category\s*)?Female[^:]*:\s*(?:Rs\.?\s*)?(\d+/-)|(Nil|Zero|Exempted|Free)", full_text, re.IGNORECASE)
    if fee_fem:
        val = fee_fem.group(1) or fee_fem.group(2) or "Nil"
        data["eligibility"].append({"label": "Application Fee (Female)", "value": val})

    # ── Age Limit ─────────────────────────────────────────────────────────
    age_min = re.search(r"Minimum\s*Age[^:]*:\s*(\d+\s*Years?)", full_text, re.IGNORECASE)
    age_max = re.search(r"Maximum\s*Age[^:]*:\s*(\d+\s*Years?)", full_text, re.IGNORECASE)
    if age_min or age_max:
        parts = [x.group(1) for x in [age_min, age_max] if x]
        age_val = " — ".join(parts)
        data["age_limit"] = age_val
        data["eligibility"].append({"label": "Age Limit", "value": age_val})

    # ── Salary ────────────────────────────────────────────────────────────
    sal = re.search(r"(?:Pay Scale|Salary)[^:]*:\s*([\₹\d,\s\-\/Level]+(?:Level|Grade)[^<\n]{0,50})", full_text, re.IGNORECASE)
    if sal:
        data["salary"] = clean_html(sal.group(1)).strip()[:100]

    # ── Useful Links ──────────────────────────────────────────────────────
    # Find the "Some Useful Important Links" table
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = clean_html(cells[0].get_text()).lower()
                links = cells[1].find_all("a", href=True)
                
                for link in links:
                    href = link.get("href", "")
                    if not href or "sarkariresult.com" in href:
                        continue
                    if not href.startswith("http"):
                        href = "https:" + href if href.startswith("//") else href
                    
                    if "apply online" in label:
                        data["apply_url"] = href
                    elif any(k in label for k in ["notification", "advertisement", "advt", "download notification"]):
                        if href.endswith(".pdf") or ".pdf" in href:
                            data["pdf_url"] = href
                    elif "official website" in label:
                        if not data["apply_url"]:
                            data["apply_url"] = href
                    elif any(k in label for k in ["short notice", "notice image"]):
                        if any(ext in href.lower() for ext in [".jpg", ".jpeg", ".png"]):
                            data["image_url"] = href

    # Also look for PDFs directly in page links
    if not data["pdf_url"]:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = clean_html(a.get_text()).lower()
            if ".pdf" in href.lower() and "sarkariresult.com" not in href:
                if any(k in text for k in ["notification", "advertisement", "advt", "download", "official"]):
                    data["pdf_url"] = href if href.startswith("http") else "https:" + href
                    break

    return data


async def get_dept_by_keywords(title: str, client: httpx.AsyncClient) -> Optional[dict]:
    """Match post title to department using keyword matching"""
    resp = await client.get(
        f"{SUPABASE_URL}/rest/v1/departments?select=id,name,official_site,keywords&keywords=not.eq.{{}}",
        headers=SB_HEADERS,
        timeout=10
    )
    if resp.status_code != 200:
        return None
    
    depts = resp.json()
    title_lower = title.lower()
    best_dept = None
    best_len = 0
    
    for dept in depts:
        keywords = dept.get("keywords") or []
        for kw in keywords:
            if kw.lower() in title_lower and len(kw) > best_len:
                best_len = len(kw)
                best_dept = dept
    
    return best_dept


async def is_duplicate(hash_val: str, client: httpx.AsyncClient) -> bool:
    resp = await client.get(
        f"{SUPABASE_URL}/rest/v1/scraper_raw_items?raw_hash=eq.{hash_val}&select=id&limit=1",
        headers=SB_HEADERS,
        timeout=10
    )
    data = resp.json()
    return bool(data)


async def upload_to_telegram(file_url: str, caption: str, client: httpx.AsyncClient, is_image: bool = False) -> Optional[str]:
    """Upload PDF/image to Telegram private channel for permanent storage"""
    if not TELEGRAM_TOKEN or not TELEGRAM_STORAGE:
        return None
    
    try:
        # Download the file
        resp = await client.get(file_url, headers=get_headers(), timeout=60, follow_redirects=True)
        if resp.status_code != 200:
            return None
        
        content = resp.content
        if len(content) > 50 * 1024 * 1024:  # Skip if > 50MB
            return None
        
        # Upload to Telegram
        field = "photo" if is_image else "document"
        method = "sendPhoto" if is_image else "sendDocument"
        mime = "image/jpeg" if is_image else "application/pdf"
        fname = "notice.jpg" if is_image else "notification.pdf"
        
        files = {field: (fname, content, mime)}
        data = {"chat_id": TELEGRAM_STORAGE, "caption": caption[:1024]}
        
        tg_resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
            data=data,
            files=files,
            timeout=120
        )
        tg_data = tg_resp.json()
        
        if not tg_data.get("ok"):
            logger.warning(f"Telegram upload failed: {tg_data.get('description')}")
            return None
        
        result = tg_data.get("result", {})
        if is_image:
            photos = result.get("photo", [])
            file_id = photos[-1]["file_id"] if photos else None
        else:
            file_id = result.get("document", {}).get("file_id")
        
        if not file_id:
            return None
        
        # Get permanent file URL
        gf_resp = await client.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}", timeout=10)
        gf_data = gf_resp.json()
        file_path = gf_data.get("result", {}).get("file_path")
        
        if file_path:
            return f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        return file_id
        
    except Exception as e:
        logger.error(f"Telegram upload error: {e}")
        return None


async def send_telegram_alert(title: str, post_slug: str, dept: Optional[dict], sr_data: dict):
    """Send public Telegram alert for new published post"""
    if not TELEGRAM_TOKEN:
        return
    
    emoji_map = {
        "job": "💼", "result": "📊", "admit_card": "🎫",
        "answer_key": "🔑", "syllabus": "📚", "admission": "🎓"
    }
    post_type = classify_post_type(title)
    emoji = emoji_map.get(post_type, "🔔")
    
    caption = f"{emoji} *{title[:200]}*\n\n"
    if sr_data.get("vacancies"):
        caption += f"📋 *Posts:* {sr_data['vacancies']:,}\n"
    if sr_data.get("important_dates"):
        last_date = next((d for d in sr_data["important_dates"] if "last date" in d["label"].lower()), None)
        if last_date:
            caption += f"⏰ *Last Date:* {last_date['date']}\n"
    
    caption += f"\n🌐 [Full Details](https://rojgarresult.vercel.app/jobs/{post_slug})"
    if dept:
        caption += f"\n📎 [Official Site]({dept['official_site']})"
    caption += f"\n\n#RojgarSchool #GovtJobs"
    if dept:
        caption += f" #{dept['name'].replace(' ', '')}"
    
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHANNEL, "text": caption, "parse_mode": "Markdown"},
                timeout=15
            )
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")


async def save_post(
    title: str,
    source_url: str,
    hash_val: str,
    dept: Optional[dict],
    sr_data: dict,
    status: str,
    source_type: str,
    client: httpx.AsyncClient
) -> bool:
    """Save enriched post to Supabase"""
    
    # Save dedup hash
    await client.post(
        f"{SUPABASE_URL}/rest/v1/scraper_raw_items",
        json={"source_site_id": 50, "raw_hash": hash_val, "raw_data": {"title": title, "url": source_url}, "status": "processed"},
        headers=SB_HEADERS,
        timeout=10
    )
    
    post_slug = slugify(title) + "-" + hash_val[:5]
    post_type = classify_post_type(title)
    
    description = sr_data.get("description") or (
        f"{dept['name'] if dept else 'Government'} has released: {title}. "
        f"Visit the official website for complete details, eligibility criteria, "
        f"important dates, and application process."
    )
    
    # Official URL — always dept's official site, never SR URL
    official_url = dept["official_site"] if dept else None
    apply_url = sr_data.get("apply_url") or official_url
    
    payload = {
        "slug": post_slug,
        "title": title,
        "post_type": post_type,
        "status": status,
        "source_type": source_type,
        "source_url": official_url,
        "department_id": dept["id"] if dept else None,
        "description": description,
        "seo_title": f"{title[:60]} — Apply Online".strip()[:80],
        "seo_description": description[:155],
        "is_featured": False,
        "is_trending": (sr_data.get("vacancies") or 0) > 5000,
        "published_at": datetime.utcnow().isoformat() if status == "published" else None,
    }
    
    if sr_data.get("vacancies"):
        payload["total_vacancies"] = sr_data["vacancies"]
    if sr_data.get("apply_start"):
        payload["application_start"] = sr_data["apply_start"]
    if sr_data.get("last_date"):
        payload["application_end"] = sr_data["last_date"]
    if sr_data.get("exam_date"):
        payload["exam_date"] = sr_data["exam_date"]
    if sr_data.get("important_dates"):
        payload["important_dates"] = sr_data["important_dates"]
    if sr_data.get("eligibility"):
        payload["eligibility"] = sr_data["eligibility"]
    if sr_data.get("salary"):
        payload["salary_range"] = {"text": sr_data["salary"]}
    
    # Upload PDF to Telegram storage
    pdf_urls = []
    if sr_data.get("pdf_url"):
        pdf_urls.append(sr_data["pdf_url"])
        tg_url = await upload_to_telegram(sr_data["pdf_url"], f"📄 {title[:200]}", client)
        if tg_url:
            pdf_urls.append(tg_url)
            logger.info(f"PDF uploaded to Telegram: {title[:50]}")
    
    if pdf_urls:
        payload["pdf_urls"] = pdf_urls
    
    # Upload short notice image
    if sr_data.get("image_url"):
        tg_url = await upload_to_telegram(sr_data["image_url"], f"🖼️ {title[:200]}", client, is_image=True)
        if tg_url:
            payload["notice_image_url"] = tg_url
    
    resp = await client.post(
        f"{SUPABASE_URL}/rest/v1/posts",
        json=payload,
        headers=SB_HEADERS,
        timeout=15
    )
    
    if resp.status_code in (200, 201):
        return True
    else:
        logger.error(f"Save failed: {resp.status_code} — {resp.text[:200]}")
        return False
