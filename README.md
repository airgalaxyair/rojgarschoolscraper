# Rojgar School Scraper

Dedicated scraper service for Rojgar School. Deploy on Render (free tier).

## Deploy on Render

1. Push this folder to a GitHub repo (e.g. `rojgarschool-scraper`)
2. Go to render.com → New → Web Service
3. Connect repo → select Python
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. Add environment variables (see below)
7. Deploy

## Environment Variables (add in Render)

| Variable | Value |
|---|---|
| `SUPABASE_URL` | `https://urfzljcwduycxywyzlnt.supabase.co` |
| `SUPABASE_KEY` | your supabase anon key |
| `TELEGRAM_BOT_TOKEN` | your bot token |
| `TELEGRAM_CHANNEL_ID` | `@rojgarschool` |
| `TELEGRAM_STORAGE_CHANNEL` | your private storage channel ID |
| `SCRAPER_SECRET` | `rojgar-scraper-secret-2025` |

## API Endpoints

All endpoints require header: `x-scraper-secret: rojgar-scraper-secret-2025`

### SarkariResult Scrapers (pending_approval)
- `POST /scrape/sr/latestjobs` — Latest jobs
- `POST /scrape/sr/results` — Results
- `POST /scrape/sr/admitcard` — Admit cards
- `POST /scrape/sr/answerkey` — Answer keys

### Official Govt Site Scrapers (published)
- `POST /scrape/official/batch/1` — SSC, UPSC, IBPS
- `POST /scrape/official/batch/2` — NTA, RRB, RBI
- `POST /scrape/official/batch/3` — SBI, NABARD
- ... up to batch 12

## Cron Jobs (cron-job.org)

After deploy, update your cron-job.org to point to:
`https://your-render-url.onrender.com/scrape/sr/latestjobs`

Method: POST
Header: x-scraper-secret: rojgar-scraper-secret-2025
