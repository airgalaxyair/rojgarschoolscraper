"""
Rojgar School Scraper API
FastAPI server exposing scraper endpoints.
Deployed on Render (different IPs from Railway - not blocked).
"""
import os
import asyncio
from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from typing import Optional

app = FastAPI(title="Rojgar School Scraper", version="1.0.0")

SCRAPER_SECRET = os.getenv("SCRAPER_SECRET", "rojgar-scraper-secret-2025")


def verify(secret: Optional[str]):
    if secret != SCRAPER_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")


@app.get("/")
async def root():
    return {"status": "running", "service": "Rojgar School Scraper"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── SarkariResult Scrapers ─────────────────────────────────────────────────

@app.post("/scrape/sr/{section}")
async def scrape_sarkari_result(
    section: str,
    background_tasks: BackgroundTasks,
    x_scraper_secret: Optional[str] = Header(None)
):
    """
    Scrape a SarkariResult section with full detail page extraction.
    Sections: latestjobs, results, admitcard, answerkey, syllabus, admission
    """
    verify(x_scraper_secret)
    valid = ["latestjobs", "results", "admitcard", "answerkey", "syllabus", "admission"]
    if section not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid section. Use: {valid}")
    
    background_tasks.add_task(_run_sr, section)
    return {"message": f"SarkariResult scraper started: {section}", "status": "pending_approval"}


@app.post("/scrape/sr/all")
async def scrape_all_sr(
    background_tasks: BackgroundTasks,
    x_scraper_secret: Optional[str] = Header(None)
):
    """Scrape all SarkariResult sections"""
    verify(x_scraper_secret)
    background_tasks.add_task(_run_all_sr)
    return {"message": "All SarkariResult sections started"}


# ── Official Site Scrapers ─────────────────────────────────────────────────

@app.post("/scrape/official/batch/{batch_num}")
async def scrape_official_batch(
    batch_num: int,
    background_tasks: BackgroundTasks,
    x_scraper_secret: Optional[str] = Header(None)
):
    """
    Scrape a batch of official govt sites.
    Batch 1: SSC, UPSC, IBPS
    Batch 2: NTA, RRB, RBI
    ... up to batch 12
    """
    verify(x_scraper_secret)
    if batch_num < 1 or batch_num > 15:
        raise HTTPException(status_code=400, detail="Batch 1-15")
    
    background_tasks.add_task(_run_official_batch, batch_num)
    return {"message": f"Official scraper batch {batch_num} started"}


@app.post("/scrape/official/all")
async def scrape_all_official(
    background_tasks: BackgroundTasks,
    x_scraper_secret: Optional[str] = Header(None)
):
    """Run all official scrapers"""
    verify(x_scraper_secret)
    background_tasks.add_task(_run_all_official)
    return {"message": "All official scrapers started"}


# ── Background tasks ───────────────────────────────────────────────────────

async def _run_sr(section: str):
    from sr_scraper import scrape_section
    result = await scrape_section(section, "pending_approval")
    import logging
    logging.getLogger("scraper").info(f"SR {section} done: {result}")


async def _run_all_sr():
    from sr_scraper import scrape_section, SR_SECTIONS
    for section in SR_SECTIONS:
        await scrape_section(section, "pending_approval")
        await asyncio.sleep(5)


async def _run_official_batch(batch_num: int):
    from official_scraper import scrape_batch
    results = await scrape_batch(batch_num)
    import logging
    logging.getLogger("scraper").info(f"Official batch {batch_num} done: {results}")


async def _run_all_official():
    from official_scraper import scrape_batch, OFFICIAL_SOURCES
    total_batches = len(OFFICIAL_SOURCES) // 3 + 1
    for i in range(1, total_batches + 1):
        await scrape_batch(i)
        await asyncio.sleep(3)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
