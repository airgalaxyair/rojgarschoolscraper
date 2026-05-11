"""
Official Government Site Scraper
Scrapes official .gov.in and .nic.in pages directly.
These are rarely blocked since they're government domains.
Posts saved as 'published' with correct department.
"""
import asyncio
import hashlib
import logging
import re
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from core import (
    fetch_page, get_dept_by_keywords, is_duplicate,
    save_post, send_telegram_alert, clean_html,
    classify_post_type, slugify, logger, SUPABASE_URL, SB_HEADERS
)

# Official government sites to scrape
OFFICIAL_SOURCES = [
    # Central Exam Bodies
    {"slug": "ssc",         "name": "SSC",         "url": "https://ssc.nic.in/Portal/LatestNews"},
    {"slug": "upsc",        "name": "UPSC",        "url": "https://upsc.gov.in/notifications"},
    {"slug": "ibps",        "name": "IBPS",        "url": "https://www.ibps.in"},
    {"slug": "nta",         "name": "NTA",         "url": "https://nta.ac.in/AnnounNTA"},
    {"slug": "rrb",         "name": "RRB",         "url": "https://www.rrbcdg.gov.in/Notifications.aspx"},
    {"slug": "rbi",         "name": "RBI",         "url": "https://www.rbi.org.in/Scripts/Recruitments.aspx"},
    # Banking
    {"slug": "sbi",         "name": "SBI",         "url": "https://bank.sbi/web/careers/current-openings"},
    {"slug": "nabard",      "name": "NABARD",      "url": "https://www.nabard.org/careers.aspx"},
    # Defence
    {"slug": "drdo",        "name": "DRDO",        "url": "https://www.drdo.gov.in/careers"},
    {"slug": "isro",        "name": "ISRO",        "url": "https://www.isro.gov.in/Careers.html"},
    {"slug": "indian-army", "name": "Indian Army", "url": "https://joinindianarmy.nic.in"},
    {"slug": "crpf",        "name": "CRPF",        "url": "https://crpf.gov.in/recruitment.htm"},
    {"slug": "bsf",         "name": "BSF",         "url": "https://bsf.nic.in/recruitment"},
    # PSU
    {"slug": "ntpc",        "name": "NTPC",        "url": "https://www.ntpc.co.in/careers"},
    {"slug": "ongc",        "name": "ONGC",        "url": "https://ongcindia.com"},
    {"slug": "fci",         "name": "FCI",         "url": "https://fci.gov.in/fci/recruitment"},
    {"slug": "coal-india",  "name": "Coal India",  "url": "https://coalindia.in/careers"},
    {"slug": "ncl",         "name": "NCL",         "url": "https://nclcil.in/career.aspx"},
    {"slug": "secl",        "name": "SECL",        "url": "https://www.secl-cil.in"},
    {"slug": "mcl",         "name": "MCL",         "url": "https://www.mahanadicoal.in/career"},
    # Teaching / Health
    {"slug": "kvs",         "name": "KVS",         "url": "https://kvsangathan.nic.in/RecruitmentNotices"},
    {"slug": "nvs",         "name": "NVS",         "url": "https://navodaya.gov.in"},
    {"slug": "aiims",       "name": "AIIMS",       "url": "https://www.aiims.edu/en/notices/recruitment.html"},
    {"slug": "esic",        "name": "ESIC",        "url": "https://esic.nic.in/recruitment"},
    {"slug": "dsssb",       "name": "DSSSB",       "url": "https://dsssb.delhi.gov.in/RecruitmentNotice"},
    # State PSCs
    {"slug": "uppsc",       "name": "UPPSC",       "url": "https://uppsc.up.nic.in"},
    {"slug": "rpsc",        "name": "RPSC",        "url": "https://rpsc.rajasthan.gov.in"},
    {"slug": "bpsc",        "name": "BPSC",        "url": "https://bpsc.bih.nic.in"},
    {"slug": "mppsc",       "name": "MPPSC",       "url": "https://mppsc.mp.gov.in"},
    {"slug": "rsmssb",      "name": "RSMSSB",      "url": "https://rsmssb.rajasthan.gov.in"},
    {"slug": "bssc",        "name": "BSSC",        "url": "https://bssc.bihar.gov.in"},
    {"slug": "kpsc",        "name": "KPSC",        "url": "https://kpsc.kar.nic.in"},
    {"slug": "tnpsc",       "name": "TNPSC",       "url": "https://tnpsc.gov.in"},
    {"slug": "wbpsc",       "name": "WBPSC",       "url": "https://pscwb.org.in"},
    # Police
    {"slug": "up-police",   "name": "UP Police",   "url": "https://uppbpb.gov.in"},
]

VALID_KEYWORDS = [
    "recruitment", "vacancy", "vacancies", "notification", "result",
    "admit card", "answer key", "syllabus", "apply", "examination",
    "selection", "online form", "trainee", "apprentice", "advertisement",
    "interview", "merit list", "cutoff"
]

def is_valid_title(title: str) -> bool:
    if len(title) < 20 or len(title) > 300:
        return False
    non_ascii = sum(1 for c in title if ord(c) > 127)
    if non_ascii > len(title) * 0.3:
        return False
    tl = title.lower()
    if not any(k in tl for k in VALID_KEYWORDS):
        return False
    junk = ["home", "contact", "login", "about us", "sitemap", "privacy", "helpdesk"]
    if any(j in tl for j in junk):
        return False
    return True


async def get_dept(slug: str, client: httpx.AsyncClient) -> Optional[dict]:
    resp = await client.get(
        f"{SUPABASE_URL}/rest/v1/departments?slug=eq.{slug}&select=id,name,official_site&limit=1",
        headers=SB_HEADERS,
        timeout=10
    )
    data = resp.json()
    return data[0] if data else None


async def scrape_official_source(source: dict) -> dict:
    """Scrape one official govt source"""
    stats = {"source": source["name"], "found": 0, "new": 0, "dupes": 0, "errors": 0}
    
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        dept = await get_dept(source["slug"], client)
        if not dept:
            logger.warning(f"Dept not found: {source['slug']}")
            return stats
        
        html = await fetch_page(source["url"], client)
        if not html:
            stats["errors"] += 1
            return stats
        
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        
        for a in soup.find_all("a", href=True):
            title = clean_html(a.get_text())
            href = a.get("href", "")
            
            if not is_valid_title(title) or title in seen:
                continue
            seen.add(title)
            
            # Build full URL
            if href and not href.startswith("http"):
                base = source["url"].split("/")[0] + "//" + source["url"].split("/")[2]
                href = base + href if href.startswith("/") else base + "/" + href
            
            # Dedup
            hash_val = hashlib.sha256(f"{title.lower().strip()}|{source['slug']}".encode()).hexdigest()
            if await is_duplicate(hash_val, client):
                stats["dupes"] += 1
                continue
            
            stats["found"] += 1
            
            # Build basic sr_data from title
            sr_data = {}
            
            # Try to get PDF if the link itself is a PDF
            if href and href.lower().endswith(".pdf"):
                sr_data["pdf_url"] = href
            
            saved = await save_post(
                title=title,
                source_url=href or source["url"],
                hash_val=hash_val,
                dept=dept,
                sr_data=sr_data,
                status="published",
                source_type="official",
                client=client
            )
            
            if saved:
                stats["new"] += 1
                post_slug = slugify(title) + "-" + hash_val[:5]
                await send_telegram_alert(title, post_slug, dept, sr_data)
                await asyncio.sleep(0.5)
            else:
                stats["errors"] += 1
        
        logger.info(f"{source['name']}: found={stats['found']}, new={stats['new']}")
    
    return stats


async def scrape_batch(batch_num: int) -> list[dict]:
    """
    Scrape a batch of official sources.
    Batches of 3 sources each to stay fast.
    """
    batch_size = 3
    start = (batch_num - 1) * batch_size
    end = start + batch_size
    sources = OFFICIAL_SOURCES[start:end]
    
    if not sources:
        return [{"error": f"Invalid batch {batch_num}. Max: {len(OFFICIAL_SOURCES) // batch_size + 1}"}]
    
    results = []
    for source in sources:
        result = await scrape_official_source(source)
        results.append(result)
        await asyncio.sleep(2)
    
    return results


if __name__ == "__main__":
    import sys
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    results = asyncio.run(scrape_batch(batch))
    for r in results:
        print(r)
