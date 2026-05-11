"""
SarkariResult.com Scraper
Scrapes listing pages and visits each post's detail page
to extract complete structured data before saving.
"""
import asyncio
import hashlib
import logging
import re
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from core import (
    fetch_page, parse_sarkari_result_detail, get_dept_by_keywords,
    is_duplicate, save_post, send_telegram_alert, clean_html,
    classify_post_type, slugify, logger
)

SR_BASE = "https://www.sarkariresult.com"

SR_SECTIONS = {
    "latestjobs": f"{SR_BASE}/latestjob/",
    "results":    f"{SR_BASE}/result/",
    "admitcard":  f"{SR_BASE}/admitcard/",
    "answerkey":  f"{SR_BASE}/answerkey/",
    "syllabus":   f"{SR_BASE}/syllabus/",
    "admission":  f"{SR_BASE}/admission/",
}

VALID_KEYWORDS = [
    "recruitment", "vacancy", "vacancies", "notification", "result",
    "admit card", "answer key", "syllabus", "apply", "examination",
    "selection", "online form", "trainee", "apprentice", "advertisement"
]
JUNK_PATTERNS = [
    "home", "contact", "login", "register", "click here", "read more",
    "privacy", "sitemap", "android app", "ios app", "youtube", "whatsapp",
    "telegram channel", "follow us", "subscribe"
]


def is_valid_title(title: str) -> bool:
    if len(title) < 25 or len(title) > 350:
        return False
    non_ascii = sum(1 for c in title if ord(c) > 127)
    if non_ascii > len(title) * 0.3:
        return False
    tl = title.lower()
    if not any(k in tl for k in VALID_KEYWORDS):
        return False
    if any(j in tl for j in JUNK_PATTERNS):
        return False
    # Must have a year
    if not re.search(r"20\d\d", title):
        return False
    return True


def extract_post_links_from_listing(html: str) -> list[dict]:
    """Extract post title + URL from SarkariResult listing page"""
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    seen = set()
    
    # SarkariResult uses tables with links
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        title = clean_html(a.get_text())
        
        # Must be a SR post URL (has multiple path segments)
        if not href.startswith(SR_BASE):
            continue
        if href.count("/") < 4:
            continue
        if any(x in href for x in ["/page/", "/tag/", "/category/", "/?", "/feed/"]):
            continue
        
        if not is_valid_title(title):
            continue
        if title in seen:
            continue
        seen.add(title)
        
        posts.append({"title": title, "url": href})
    
    return posts[:25]  # Max 25 per page


async def scrape_section(section: str = "latestjobs", status: str = "pending_approval") -> dict:
    """
    Scrape a SarkariResult section.
    For each post:
    1. Visit listing page
    2. Get all post links
    3. Visit each detail page
    4. Extract ALL data
    5. Match department
    6. Save with full data to Supabase
    """
    section_url = SR_SECTIONS.get(section, SR_SECTIONS["latestjobs"])
    stats = {"section": section, "found": 0, "new": 0, "dupes": 0, "errors": 0, "posts": []}
    
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # Step 1: Fetch listing page
        logger.info(f"Fetching listing: {section_url}")
        listing_html = await fetch_page(section_url, client)
        if not listing_html:
            stats["errors"] += 1
            logger.error(f"Failed to fetch listing: {section_url}")
            return stats
        
        # Step 2: Extract post links
        posts = extract_post_links_from_listing(listing_html)
        stats["found"] = len(posts)
        logger.info(f"Found {len(posts)} posts on {section}")
        
        # Step 3: Process each post
        for post in posts:
            title = post["title"]
            url = post["url"]
            
            # Dedup check
            hash_val = hashlib.sha256(f"{title.lower().strip()}|sr".encode()).hexdigest()
            if await is_duplicate(hash_val, client):
                stats["dupes"] += 1
                continue
            
            try:
                # Step 4: Fetch detail page
                logger.info(f"Scraping detail: {title[:60]}")
                detail_html = await fetch_page(url, client)
                
                if not detail_html:
                    logger.warning(f"Failed to fetch detail: {url}")
                    # Save with title only as fallback
                    sr_data = {}
                else:
                    # Step 5: Extract ALL structured data
                    sr_data = parse_sarkari_result_detail(detail_html, url)
                    logger.info(
                        f"  ✓ Extracted: vac={sr_data.get('vacancies')}, "
                        f"dates={len(sr_data.get('important_dates', []))}, "
                        f"pdf={'yes' if sr_data.get('pdf_url') else 'no'}, "
                        f"elig={len(sr_data.get('eligibility', []))}"
                    )
                
                # Step 6: Match department
                dept = await get_dept_by_keywords(title, client)
                if dept:
                    logger.info(f"  ✓ Dept: {dept['name']}")
                
                # Step 7: Save
                saved = await save_post(
                    title=title,
                    source_url=url,
                    hash_val=hash_val,
                    dept=dept,
                    sr_data=sr_data,
                    status=status,
                    source_type="third_party",
                    client=client
                )
                
                if saved:
                    stats["new"] += 1
                    post_slug = slugify(title) + "-" + hash_val[:5]
                    stats["posts"].append({
                        "title": title[:70],
                        "dept": dept["name"] if dept else "unknown",
                        "vacancies": sr_data.get("vacancies"),
                        "dates": len(sr_data.get("important_dates", [])),
                        "pdf": bool(sr_data.get("pdf_url")),
                    })
                    logger.info(f"  ✓ Saved: {title[:60]}")
                else:
                    stats["errors"] += 1
                
                # Be polite — don't hammer the server
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error processing {title[:50]}: {e}")
                stats["errors"] += 1
                await asyncio.sleep(3)
    
    logger.info(f"Section {section} done: found={stats['found']}, new={stats['new']}, dupes={stats['dupes']}, errors={stats['errors']}")
    return stats


if __name__ == "__main__":
    import sys
    section = sys.argv[1] if len(sys.argv) > 1 else "latestjobs"
    result = asyncio.run(scrape_section(section, "pending_approval"))
    print(f"\nResult: {result}")
