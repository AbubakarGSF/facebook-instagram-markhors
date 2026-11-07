# Social Markhors Bot (Facebook + Instagram)

Alag project hai `evilmarkhorsX_Bot` se. ye project sirf **Facebook aur Instagram** ke liye hai:
- cookie rework + branding (Netscape / `key=value` / `key: value` support)
- live check (LIVE / DEAD / UNKNOWN)
- campaign post (zip ya DB source)
- Supabase storage (alag tables: `fb_accounts`, `ig_accounts`)

ye project `evilmarkhorsX_Bot` se **kabhi mix nahi hoga**. dono ke alag-alag repo, alag-alag Railway deploy, alag bot token, alag Supabase tables.

## Structure

| Folder | Purpose |
|--------|---------|
| `src/platforms/facebook.py` | FB live check, Graph API post |
| `src/platforms/instagram.py` | IG live check (private API), Graph API media post |
| `src/platforms/base.py` | Shared base: proxy client, strict cookie parser, retries |
| `src/database/supabase.py` | Supabase client (per-platform tables, upsert + patch status) |
| `src/utils/proxy.py` | Proxy load/add/test/on-off + mask |
| `src/utils/crypto.py` | Fernet cookie encryption at rest |
| `src/utils/audit.py` | Async admin action audit log (rotating) |
| `src/bot.py` | Telegram handlers, file processing, campaign loop |
| `src/main.py` | Entry point, JSON logging, health server |

## Env Vars

`.env.example` copy karke `.env` banao:

```
BOT_TOKEN=...
ADMIN_IDS=123,456
SUPABASE_URL=...
SUPABASE_KEY=service_role_key
ENCRYPTION_KEY=...            # mandatory — bot iske bina start nahi hota
PROXIES=host:port:user:pass
PROXY_TYPE=socks5
```

Optional: `HEALTH_CHECK_PORT` / Railway `PORT`, `CONCURRENCY`, `TIMEOUT`,
`ACC_TIMEOUT`, `MAX_FILES`, `VERIFY_SLOW_RETRY_SEC`, `VERIFY_CONFIRM_DEAD`,
`PROGRESS_UPDATE_SEC`, `NETCHECK_ON_START`, `STATE_DIR`, `ZIP_MAX_*`.

`ENCRYPTION_KEY` generate karne ke liye:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Commands

**Modes**
- `/rework` — cookie `.txt` ya `.zip` bhejo, branded output (working/unknown/errors)
- `/verify` — zip bhejo, LIVE/DEAD/UNKNOWN check (proxy + direct fallback)
- `/live on|off` — rework ke saath live check
- `/cancel`

**Campaign**
- `/camp new <name>` → `/camp platform facebook|instagram`
- `/camp text` → variants bhejo → `/camp idle`
- `/camp media`, `/camp filter country PK,US|followers 500`, `/camp delay 45-110`
- `/camp limit 50`, `/camp dry`, `/camp source zip|db`, `/camp preview`
- `/camp tag #EvilMarkhors`, `/camp branding on|off`
- `/camp start`, `/camp reset`, `/stopcamp`

## Branding

Reworked cookie file ka header aur campaign posts dono branded hote hain.
Default links ye hain (env se override ho sakte hain):

```
Evil Markhors
https://evilmarkhors.me, https://evilmarkhors.tech
Config by Evil Markhors
– WhatsApp: https://whatsapp.com/channel/0029Vb5qjwcG8l5CaqjMSU0K
– Telegram: https://t.me/+IGM63Xe1A-43ZTdk
– Twitter:  https://x.com/EvilMarkhors
– Facebook: https://www.facebook.com/share/19FyzDCNe6/
```

Campaign post ke end me `DEFAULT_TAG` + website + social links ka footer
automatically lagta hai. Band karna ho to `/camp branding off`
(ya `POST_BRANDING=0`).

**Admin**
- `/db stats | /db export fb|ig | /db reverify fb|ig [limit]`
- `/proxy status|add|test|on|off|detect`
- `/netcheck`, `/diag`, `/status`

## Platform detection (verify)

Har cookie file ka platform **cookie keys se** decide hota hai, filename se nahi:

- Facebook: `c_user` / `xs`
- Instagram: `sessionid` / `ds_user_id`

Priority: cookie keys > campaign platform > filename. Agar file me FB aur IG
dono ke keys milen, ya campaign platform cookie se match na kare, to file
verify nahi hoti — status `UNKNOWN` milta hai (false `DEAD` nahi). Is liye
`1.txt`, numeric user-id files aur galat naam wali files bhi sahi site se
check hoti hain.

## DB behaviour (important)

- Verify ke baad **LIVE, DEAD aur UNKNOWN teeno** rows insert/update hoti hain.
- `last_check` har verify par set hota hai; `last_ok` sirf LIVE par.
- Cookies `ENCRYPTION_KEY` se encrypt hokar save hoti hain.
- Rework-only run (live check OFF) DB me kuch save nahi karta.
- Campaign post success par row `status=live`, `last_post` ke saath upsert hoti hai.

## Posting reality

Cookie-only posting reliable nahi hai, is liye bot jhoot nahi bolta:

- **Facebook**: Graph API `access_token` chahiye. Token na ho to result `UNSUPPORTED`.
- **Instagram**: `access_token` + IG business id + **public image URL** chahiye
  (Graph API text-only post support nahi karta). Warna `UNSUPPORTED`.
- Campaign summary me `Unsupported (token/media missing)` alag count hota hai,
  failed posts me mix nahi hota.

## Run

```bash
pip install -r requirements.txt
python src/main.py
```

Railway par `PORT` automatically health server ke liye use hota hai
(`/healthz` ya `/` → `200 {"status":"ok"}`).

## Note

ye project `evilmarkhorsX_Bot` ka **copy nahi hai**. nayi architecture hai, alag database tables hain, alag bot token hai. dono ek saath chal sakte hain bina kisi conflict ke.
