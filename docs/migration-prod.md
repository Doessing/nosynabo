# nosynabo - Migration til produktion

> Oprettet: 2026-08-19
> Status: Fase 1 delvist færdig - afventer WAF-token
> Sidst opdateret: 2026-08-19

## Mål

- `nosynabo.dk` - public production server (host2, 10.0.0.142)
- `nosy.dossing.net` - dev server med auto-deploy (webhost1, 10.0.0.107)
- `wiki.dossing.net` - flyttes fra host2 til main (10.0.0.198)
- Kommerciel landingsside på nosynabo.dk med Ko-fi, features, screenshots
- Dev-banner på nosy.dossing.net så brugere vejledes til prod

## Infrastruktur overblik

| Server | IP | Rolle (nu) | Rolle (efter) |
|---|---|---|---|
| main | 10.0.0.198 | Plex, OpenCode, wiki | Plex, OpenCode, wiki (arm64) |
| host2 | 10.0.0.142 | Wiki (SilverBullet) | **nosynabo.dk prod** |
| webhost1 | 10.0.0.107 | nosy.dossing.net | **nosy.dossing.net dev** |

## DNS status (nosynabo.dk)

- Zone: Cloudflare (zone_id: `97ce3c9b85b78f369c3971687e633e12`)
- A-records (`nosynabo.dk`, `www.nosynabo.dk`, `*.nosynabo.dk`) → `138.2.175.169` (host2) ✅
- MX + DKIM + DMARC sat op via Cloudflare Email Routing - **rør ikke disse**
- SSL: wildcard ZeroSSL ECC cert (`nosynabo.dk` + `*.nosynabo.dk`) via acme.sh Cloudflare DNS-01 ✅

---

## Faser

### Fase 1 - nosynabo.dk prod-server ✅ (delvist - WAF mangler token)
*Host2 er ledig (ingen nginx, kun SilverBullet på 127.0.0.1:3000 og Docker). Prod sættes op parallelt med eksisterende dev.*

- [x] **1a. Grundopsætning på host2**
  - nginx 1.28.3, Python 3.14, python3.14-venv, acme.sh installeret
  - `/opt/nosynabo/` - git clone + venv + requirements (mcp pinnet til 1.27.0)
  - `/opt/nosynabo-landing/` - git clone fra privat repo `Doessing/nosynabo-landing`
  - `/etc/nosynabo/secrets.env` med `DATAFORSYNINGEN_TOKEN` + `NOSYNABO_ENV=prod`
  - systemd-service `nosynabo.service` (port 8000) - active ✅

- [x] **1b. nginx + SSL på host2**
  - Wildcard ZeroSSL ECC cert: `nosynabo.dk` + `*.nosynabo.dk` via acme.sh DNS-01
  - Cert installeret: `/etc/nginx/ssl/nosynabo.dk/` - auto-renewal sat op
  - nginx vhost `/etc/nginx/sites-available/nosynabo.dk`:
    - `nosynabo.dk` → landing page (statisk `/opt/nosynabo-landing/`)
    - `www.nosynabo.dk` → 301 redirect til apex
    - `nosy.nosynabo.dk` → proxy_pass 127.0.0.1:8000 (app)
    - `/mcp` → 403 på app-vhost
  - Port 80/443 åbnet i iptables + persisteret via netfilter-persistent

- [x] **1c. Cloudflare DNS**
  - `nosynabo.dk`, `www.nosynabo.dk`, `*.nosynabo.dk` → `138.2.175.169` (proxied) ✅
  - **WAF: AFVENTER** - ingen eksisterende tokens har `Zone WAF:Edit` scope på nosynabo.dk-zonen
    - Opret nyt token på https://dash.cloudflare.com/profile/api-tokens:
      - Permissions: `Zone - WAF - Edit` + `Zone - Zone - Read`
      - Zone Resources: Specific zone → `nosynabo.dk`
    - Gem token i `.secrets` som `CF_TOKEN_WAF_NOSYNABO`
    - Regel der skal oprettes: `(ip.geoip.country ne "DK" and not cf.client.bot) or cf.threat_score gt 5` → block
    - Rate limit: 4 req/10s per IP

- [x] **1d. Landingsside (nosynabo.dk frontend)**
  - Eksisterende landing fra `/home/ubuntu/nosynabo-landing/index.html` deployeret
  - Privat repo oprettet: `Doessing/nosynabo-landing` (https://github.com/Doessing/nosynabo-landing)
  - Footer tilføjet: `Dossing Net — self-hosted with ♥ on Oracle Cloud`
  - Ko-fi-link, features, tagline allerede i siden fra tidligere

- [x] **1e. Verificér prod fungerer** ✅ 2026-08-19
  - `https://nosynabo.dk` → 200 (landing page)
  - `https://nosy.nosynabo.dk` → 200 (app)
  - `https://www.nosynabo.dk` → 301 → `https://nosynabo.dk`
  - `/api/version` → `6439f62 main`
  - Lookup test (Drachmannsvej 12, 9300 Sæby): statuskode 0, 1 ejer, 4 hæftelser ✅

---

### Fase 2 - nosy.dossing.net som dev-server
*Starter EFTER fase 1 er verificeret - så prod er oppe inden vi piller ved dev*

- [ ] **2a. Dev-banner i UI**
  - Tilføj `NOSYNABO_ENV` check i `server.py` → eksponér i template-kontekst
  - Gul/amber banner øverst i `index.html`: *"Dev-server - fejl og nedetid kan forekomme. Brug [nosynabo.dk](https://nosynabo.dk) ved problemer."*
  - Banner vises KUN når `NOSYNABO_ENV=dev` - prod-serveren viser den ikke
  - Sæt `NOSYNABO_ENV=dev` i webhost1's `/etc/nosynabo/secrets.env`

- [ ] **2b. Webhook-modtager på webhost1**
  - Lille Python-service (`nosynabo-webhook.service`) på `127.0.0.1:9000`
  - Validerer GitHub HMAC-secret på alle indgående payloads
  - Trigger: push til branch med præfix `dev/` eller direkte til en `dev`-branch
  - Handling: `git fetch && git checkout <branch> && systemctl restart nosynabo`
  - nginx: `/webhook` på `nosy.dossing.net` → proxy_pass 127.0.0.1:9000
  - Cloudflare WAF: kun GitHub's IP-ranges tilladt på `/webhook`-stien

- [ ] **2c. GitHub webhook opsætning**
  - Tilføj webhook i repo-settings: `https://nosy.dossing.net/webhook`
  - Secret genereres og gemmes i `/home/ubuntu/.secrets` + webhook-servicen
  - Event: `push` (filtrer på branch-navn i webhook-handleren)

---

### Fase 3 - Wiki migration (host2 → main)
*Kan køre parallelt med eller efter fase 1 - host2 behøver ikke at være fri FØR prod er sat op, da SilverBullet kun lytter på 127.0.0.1*

- [ ] **3a. SilverBullet på main (arm64)**
  - Verificér arm64-kompatibilitet (Node.js + SilverBullet kører på arm64)
  - Installer på main under `/opt/silverbullet/`
  - Test tomt space virker inden data-migration

- [ ] **3b. Migrer space-data**
  - `rsync -av ubuntu@10.0.0.142:/opt/silverbullet/space/ /opt/silverbullet/space/`
  - Verificér alle sider er intakte på main

- [ ] **3c. DNS + Cloudflare Access**
  - `wiki.dossing.net` A-record → main's Oracle IP
  - Verificér Cloudflare Access policy stadig beskytter wiki (policy er zone-bundet, ikke IP-bundet)
  - Test adgang bag Access

- [ ] **3d. Afvikl SilverBullet på host2**
  - Stop + disable service
  - Tag fuld backup af space før nedlukning
  - Opdater context-fil: `~/.config/opencode/context/projects/nosynabo.md` + wiki

---

### Fase 4 - README opdatering
*Sidst - efter alt er live og stabilt*

- [ ] **4a. README.md i repo**
  - Tilføj kort bruger-afsnit øverst (2-3 linjer): link til nosynabo.dk + Ko-fi
  - Behold resten teknisk/uændret

---

## Beslutninger logget

| Emne | Beslutning |
|---|---|
| Prod deploy-trigger | **Manuelt** - `git pull` på host2 styres af ejeren. Auto-deploy kun på dev. |
| Dev auto-deploy trigger | Push til branch med `dev/`-præfix ELLER direkte til `dev`-branch |
| Wiki destination | **main** (10.0.0.198) |
| Domæner | Begge beholdes - ingen redirect mellem nosynabo.dk og nosy.dossing.net |
| App-subdomain | `nosy.nosynabo.dk` (spejler navngivning på nosy.dossing.net) |
| SSL-strategi | Wildcard cert (`*.nosynabo.dk`) - dækker alle subdomæner uden genudstedelse |
| Webhook sikkerhed | HMAC-valideret, dedikeret minimal Python-service, Cloudflare IP-filtrering |
| Landing page repo | Privat `Doessing/nosynabo-landing` - ikke del af open source-projektet |
| mcp version på host2 | Pinnet til `mcp==1.27.0` (v2.0.0 brød `mcp.server.fastmcp`) |

## Tekniske noter

### host2 (10.0.0.142) - prod-server
- Oracle public IP: `138.2.175.169`
- nginx vhost: `/etc/nginx/sites-available/nosynabo.dk`
- SSL cert: `/etc/nginx/ssl/nosynabo.dk/` (wildcard, auto-renewal via acme.sh)
- App: `/opt/nosynabo/` - systemd `nosynabo.service` port 8000
- Landing: `/opt/nosynabo-landing/` - statisk HTML
- Secrets: `/etc/nosynabo/secrets.env`
- iptables: port 80/443 åbnet + persisteret via netfilter-persistent
- Deploy: `ssh ubuntu@10.0.0.142 "cd /opt/nosynabo && git pull && sudo systemctl restart nosynabo"`

### nosynabo.dk Cloudflare zone-id
`97ce3c9b85b78f369c3971687e633e12`

### WAF token mangler (udestående)
Ingen eksisterende tokens har `Zone WAF:Edit` på nosynabo.dk-zonen.
Opret på https://dash.cloudflare.com/profile/api-tokens:
- Permissions: `Zone - WAF - Edit` + `Zone - Zone - Read`
- Zone: Specific zone → `nosynabo.dk`
- Gem som `CF_TOKEN_WAF_NOSYNABO` i `/home/ubuntu/.secrets`

### webhost1 nginx sites (rør ikke under fase 2)
`00-default`, `blocked.dossing.net`, `dossing.it`, `dossing.net`, `fallback.dossing.net`,
`nosy.dossing.net`, `sdossing.dk`, `xn--dssing-bya.net`

### Kendte gotchas
- `mcp` v2.0.0 bryder `from mcp.server.fastmcp import FastMCP` - pin til `mcp==1.27.0`
- host2 iptables: port 80/443 var ikke åbnet som standard (Oracle Cloud default)
- root-ejede filer i `/opt/nosynabo` blokerer `git fetch` - brug aldrig `sudo git pull`
