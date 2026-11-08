# Social Markhors Bot (Facebook + Instagram)

Current version: **v2.3.5**

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
`ACC_TIMEOUT`, `RETRIES`, `POST_MIN`, `POST_MAX`, `MAX_FILES`, `FILE_TIMEOUT`,
`CONTENT_COOLDOWN`, `VERIFY_SLOW_RETRY_SEC`, `VERIFY_CONFIRM_DEAD`,
`PROGRESS_UPDATE_SEC`, `NETCHECK_ON_START`, `STATE_DIR`, `ZIP_MAX_*`,
`IG_MEDIA_POLLS`, `IG_MEDIA_POLL_SEC`, `MAX_STATE_ENTRIES`, `MAX_TEXT_VARIANTS`,
`DB_EXPORT_MAX_ROWS`.

`ENCRYPTION_KEY` sirf non-empty nahi, **valid Fernet key** honi chahiye; invalid
key par bot startup par hi exit karta hai (plaintext cookies save nahi hotin).

`PROXY_MODE`: `rotate` (default, random per pick) ya `first` / `single` /
`sticky` (pehla configured proxy har baar).

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
- `/camp media`, `/camp mediaurl https://public/image.jpg`, `/camp mediaclear`
- `/camp filter country PK,US | year 2015,2016 | followers 500`
- `/camp delay 45-110`
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

### Accuracy guards (false DEAD se bachao)

- Proxy se `DEAD` aaye aur direct route confirm na kare to result `UNKNOWN`.
- FB/IG `403` (IP/checkpoint block) → `UNKNOWN`, `401` → `DEAD`.
- FB login/JS page par sirf weak marker (`notifications` / `"userid"`) mile to
  `LIVE` claim nahi hota — strong session marker ya do weak markers chahiye.
- IG empty `200` → `UNKNOWN`; sirf explicit `login_required` / challenge → `DEAD`.
- Missing session key wali file network par nahi jati — seedha `UNKNOWN`.
- File ka processing time `FILE_TIMEOUT` se capped hai; hang hone par file
  `errors/` me jati hai aur baqi processing chalti rehti hai.

### Campaign filters

`/camp filter country PK,US` aur `/camp filter year 2015,2016` **strict** hain:
account me country/year missing ho to wo skip hota hai (filter bypass nahi
hota). `min_followers` bhi usi tarah apply hota hai.

`/camp mediaurl` sirf public `http(s)` host accept karta hai — localhost,
private IP aur single-label intranet hosts reject hote hain (Graph API unhe
fetch nahi kar sakta).

DB row ke liye owner id session cookie se bhi li jati hai (`c_user` FB,
`ds_user_id` IG) — HTML metadata na mile to bhi row skip nahi hoti.

## DB behaviour (important)

- Verify ke baad **LIVE, DEAD aur UNKNOWN teeno** rows insert/update hoti hain.
- `last_check` har verify par set hota hai; `last_ok` sirf LIVE par.
- Cookies `ENCRYPTION_KEY` se encrypt hokar save hoti hain.
- Rework-only run (live check OFF) DB me kuch save nahi karta.
- Campaign post success par row `status=live`, `last_post` ke saath upsert hoti hai.
- `year` aur `media_url` optional columns hain (`schema.sql` me included).
  Purane deployment me `schema.sql` dobara chalao. Bot in columns ke bina bhi
  chalta hai: upsert missing-column error par sirf ye fields strip karke retry
  karta hai.
- DB source campaign ka `year` filter aur per-account media URL inhi columns se
  aata hai (zip source me file header/filename se).

## Posting reality

Cookie-only posting reliable nahi hai, is liye bot jhoot nahi bolta:

- **Facebook**: Graph API `access_token` chahiye. Token na ho to result `UNSUPPORTED`.
- **Instagram**: `access_token` + IG business id + **public image URL** chahiye
  (Graph API text-only post support nahi karta). Warna `UNSUPPORTED`.
  Public URL `/camp mediaurl https://public/image.jpg` se set hota hai; media
  container ready hone ka wait (`IG_MEDIA_POLLS` / `IG_MEDIA_POLL_SEC`) phir
  publish hota hai.
- Campaign summary me `Unsupported (token/media missing)` alag count hota hai,
  failed posts me mix nahi hota.
- Per-account `media_url` campaign-level URL ko override karta hai.

## Hardening (v2.3.5)

- **Python 3.12 compatibility**: `Tuple` import fix (Docker `python:3.12-slim`
  par bot ab start hota hai; pehle annotation evaluation startup crash deti thi).
- **Metadata/token parsing**: plain `Access Token:` / `Token:` headers ab
  campaign token ke tor par parse hote hain aur cookie parser unhe cookie nahi
  banata. File se aaya token DB me persist hota hai (empty token existing token
  ko overwrite nahi karta).
- **Upload limits**: single `.txt` bhi `ZIP_MAX_FILE` se capped hai; declared
  file size pehle check hoti hai. Cookie parser max 200 cookies / 4KB value
  leta hai (huge header abuse se bachao).
- **Campaign validation**: `/camp delay` sirf `0-3600` range accept karta hai,
  reversed values sort hote hain; `/camp limit` `MAX_FILES` par clamp hota hai.
  Corrupt `camp_*.json` `_safe_camp()` se normalize hota hai.
- **Accurate campaign progress/summary**: verify `DEAD`, verify `UNKNOWN`,
  post failures aur internal errors alag count hote hain (pehle sab "dead" dikhte the).
- **DB export**: paginated (`DB_EXPORT_MAX_ROWS`, default 20000) aur `year` /
  `media_url` columns include karta hai.
- **Netcheck**: Supabase `401` ko "reachable; auth required" dikhata hai,
  FB/IG `4xx` ko warning (`⚠`) — sab kuch green tick nahi.
- **State safety**: corrupt timestamps (`nan`/text) crash nahi karte; text/media
  add karne par campaign TTL refresh hota hai.
- **Output zip hardening**: `.` / `..` aur control characters wali entry names
  reject hoti hain (zip-slip se bachao).
- **Platform accuracy**: FB page body me aane wala `"checkpoint"` word ab
  false `DEAD` nahi banata (sirf URL path checkpoint/login par DEAD).

## Run

```bash
pip install -r requirements.txt
python src/main.py
```

Railway par `PORT` automatically health server ke liye use hota hai
(`/healthz` ya `/` → `200 {"status":"ok"}`).

## Note

ye project `evilmarkhorsX_Bot` ka **copy nahi hai**. nayi architecture hai, alag database tables hain, alag bot token hai. dono ek saath chal sakte hain bina kisi conflict ke.
