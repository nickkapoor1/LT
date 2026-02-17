# Pickleball Sniper for Lifetime Fitness

Automatically registers you for pickleball sessions at Lifetime Fitness **the instant** registration opens — using their internal API directly (no browser automation).

## Why This Works

Lifetime pickleball slots fill up within seconds of opening. This tool:
- Polls the Lifetime API every 1-5 seconds
- Detects the exact moment registration becomes available
- Fires a registration API call in **milliseconds** (vs. seconds to click through a browser)
- Supports multiple accounts and members simultaneously

## Quick Start

```bash
pip install -r requirements.txt
streamlit run main.py
```

That's it. Opens in your browser at `http://localhost:8501`.

## How to Use

### 1. Add Your Account
- Go to **Accounts** tab
- Enter your Lifetime email, password, and club (default: PENN 1)
- Click **Test Login** to verify it works

### 2. Browse the Schedule
- Go to **Schedule** tab
- Select your account and date range
- Click **Load Schedule** to see all pickleball events

### 3. Pick Events to Snipe
- For each event, you can:
  - **Check Availability** — see spots, waitlist, and when registration opens
  - **Add to Watchlist** — the engine will auto-register when it opens
  - **Register NOW** — immediately register if slots are available

### 4. Start the Engine
- Click **Start Engine** in the sidebar
- The engine polls every few seconds and registers automatically
- Watch the **Dashboard** for real-time status and activity log

## Configuration

### Polling Interval
- **1-5 seconds**: For sniping (when registration is about to open)
- **30-60 seconds**: For passive monitoring

Adjust in the **Settings** tab.

## Project Structure

```
main.py                      <- Streamlit UI (run this)
app/
  config/
    settings.py              <- API endpoints, club list, timing
  backend/
    api_client.py            <- Lifetime API (auth, events, registration)
    crypto.py                <- Encrypted credential storage
    monitor.py               <- Watchlist tracking
    scheduler.py             <- Background polling engine
data/                        <- Created at runtime (encrypted credentials, logs)
```

## Security

- Passwords are encrypted at rest using Fernet (AES-128)
- The encryption key is in `data/.key` — don't share it
- **Never commit the `data/` folder to git**

## Troubleshooting

| Problem | Fix |
|---|---|
| Login fails | Check email/password. Try logging in at my.lifetime.life manually. |
| No events found | Make sure the club name matches exactly (e.g. "PENN 1"). |
| "Registration will be open on..." | The event's registration window hasn't opened yet. Add it to the watchlist and start the engine — it will register automatically when it opens. |
| API keys expired | The app fetches keys dynamically from the Lifetime website. If it fails, Lifetime may have changed their site structure. |
