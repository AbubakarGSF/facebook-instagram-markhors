# Social Markhors Bot (Facebook + Instagram)

Alag project hai `evilmarkhorsX_Bot` se. ye project sirf **Facebook aur Instagram** ke liye hai:
- cookie rework + branding (alt format support)
- live check (working/dead/suspended)
- campaign post
- Supabase db storage (per-platform)

ye project `evilmarkhorsX_Bot` se **kabhi mix nahi hoga**. dono ke alag-alag repo, alag-alag Railway deploy.

## Structure

| Folder | Purpose |
|--------|---------|
| `src/platforms/facebook.py` | FB login check, post, db |
| `src/platforms/instagram.py` | IG login check, post, db |
| `src/platforms/base.py` | Shared base class (proxy, headers, rate limit) |
| `src/database/supabase.py` | Supabase client (per-platform tables) |
| `src/utils/proxy.py` | Proxy rotation (same logic as evilmarkhors bot) |
| `src/bot.py` | Telegram bot handler |
| `src/main.py` | Entry point |

## Env Vars

copy `.env.example` to `.env` aur fill karo:

```
BOT_TOKEN=...
ADMIN_IDS=...
SUPABASE_URL=...
SUPABASE_KEY=...
PROXIES=...
PROXY_TYPE=socks5
PROXY_MODE=rotate
HEALTH_CHECK_PORT=8080
```

## Run

```bash
pip install -r requirements.txt
python src/main.py
```

## Note

ye project `evilmarkhorsX_Bot` ka **copy nahi hai**. nayi architecture hai, alag database tables hain, alag bot token hai. dono ek saath chal sakte hain bina kisi conflict ke.
