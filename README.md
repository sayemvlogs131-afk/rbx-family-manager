# RBX Family Manager
```
**The all-in-one management, moderation, giveaway, utility and fun bot for RBX Family.**
```-
A production-quality Discord bot built in Python 3.10+ with `discord.py`, slash commands,
buttons, persistent views and an async SQLite database. Designed to be reliable, secure
and light enough to run comfortably on a free cloud VM.
```
- 🛡️ Moderation with hierarchy safety, cases and audit logging
- ⚙️ AutoMod (spam, invites, mentions, caps, emoji, banned words) — fully configurable
- 📋 Logging for messages, members, roles, channels, voice and moderation
- 🎉 Giveaways that **survive restarts** (persistent buttons + database recovery)
- 🎫 Ticket system with transcripts
- 👋 Welcome messages, auto role, AFK, reminders, polls, embed builder
- 🤖 Owner tools, interactive `/help`, health endpoint
- 🖱️ Context menus (right-click → **User Info**, **Warn member**) and modals (ticket issue, warn reason)
```
---
```
## Table of contents
```
1. [Features at a glance](#1-features-at-a-glance)
2. [Project structure](#2-project-structure)
3. [Requirements](#3-requirements)
4. [Discord Developer Portal setup](#4-discord-developer-portal-setup)
5. [Local installation](#5-local-installation)
6. [Bot invite URL](#6-bot-invite-url)
7. [Slash command sync: global vs guild](#7-slash-command-sync-global-vs-guild)
8. [Command reference](#8-command-reference)
9. [GitHub setup](#9-github-setup)
10. [Deployment: Oracle Cloud Always Free VM (24/7)](#10-deployment-oracle-cloud-always-free-vm)
11. [systemd configuration](#11-systemd-configuration)
12. [Starting, stopping and updating the bot](#12-starting-stopping-and-updating-the-bot)
13. [Docker deployment](#13-docker-deployment)
14. [Health endpoint](#14-health-endpoint)
15. [Security notes](#15-security-notes)
16. [Testing](#16-testing)
17. [Troubleshooting](#17-troubleshooting)
18. [Free hosting limitations (read this)](#18-free-hosting-limitations-read-this)
```
---
```
## 1. Features at a glance
```
| Area | What you get |
|---|---|
| Moderation | `/ban` `/unban` `/kick` `/timeout` `/untimeout` `/mute` `/unmute` `/softban` `/warn` `/warnings` `/warnmanage` `/clear` `/purge` `/slowmode` `/lock` `/unlock` `/nick` `/massban` `/modlogs` |
| AutoMod | anti-spam, duplicate detection, mention/emoji/caps limits, invite & link filters, banned words, warning threshold, automatic timeout, per-rule cooldowns, exemptions |
| Logging | separate channels for moderation, member, server and message logs; delete/edit, joins/leaves, bans, timeouts, roles, channels, nicknames, voice |
| Giveaways | `/giveaway start end reroll list cancel info`, buttons, requirements (role/account age/server tenure), restart recovery, secure winner draw |
| Utility | `/help` `/ping` `/botinfo` `/uptime` `/invite` `/serverinfo` `/userinfo` `/avatar` `/banner` `/roleinfo` `/channelinfo` `/permissions` `/afk` `/remind` `/reminders` `/timestamp` `/calc` `/poll` `/embed` `/announce` |
| Fun | `/8ball` `/coinflip` `/dice` `/roll` `/choose` `/rate` `/ship` `/roast` `/compliment` `/joke` `/meme` |
| Server | `/setup` `/welcome` `/autorole` `/staffrole` `/ticket` `/setlogchannel` `/logchannels` `/logtest` `/config` `/command` `/ignore` `/ignores` |
| Owner | `/owner reload sync shutdown guilds status say broadcast` |
```
---
```
## 2. Project structure
```
```
RBX-Family-Manager/
│
├── bot.py                     # entry point: logging, cogs, sync, errors, /health
├── config.py                  # .env loader + branding constants (no secrets)
├── requirements.txt           # runtime dependencies
├── requirements-dev.txt       # test dependencies
├── pytest.ini                 # test configuration
├── .env.example               # template for your secrets (copy to .env)
├── .gitignore
├── README.md
├── LICENSE
├── Procfile                   # Heroku-style process runner
├── Dockerfile
├── docker-compose.yml
│
├── cogs/
│   ├── moderation.py          # ban/kick/warn/timeout/purge/lock/...
│   ├── automod.py             # configurable automoderation
│   ├── logging.py             # event logs + /setlogchannel
│   ├── giveaways.py           # persistent giveaways + auto-draw loop
│   ├── fun.py                 # harmless entertainment commands
│   ├── utility.py             # /help, info, reminders, polls, embeds
│   ├── server.py              # staff role, autorole, ignores, permissions
│   ├── welcome.py             # welcome messages + auto role assignment
│   ├── tickets.py             # ticket panel, close button, transcripts
│   └── owner.py               # owner-only maintenance commands
│
├── database/
│   ├── database.py            # async SQLite access layer
│   ├── schema.sql             # full schema (applied on first run)
│   └── migrations.py          # versioned migration runner
│
├── utils/
│   ├── embeds.py              # consistent RBX Family embed builders
│   ├── checks.py              # permission/staff/command checks
│   ├── permissions.py         # role hierarchy & safety rules
│   ├── time_parser.py         # "10m", "1h30m", "2026-01-01 18:00"
│   ├── pagination.py          # paginator, confirm dialog, menu
│   └── helpers.py             # text helpers, safe calculator, emoji counts
│
├── data/
│   └── .gitkeep               # SQLite DB + logs live here (git-ignored)
│
├── deployment/
│   ├── install.sh             # venv + deps + .env + DB + optional systemd
│   ├── start.sh               # run the bot in the foreground
│   └── rbx-family-manager.service
│
└── tests/                     # 144 automated tests
```
```
---
```
## 3. Requirements
```
- Python **3.10+** (3.11/3.12 recommended)
- A Discord **Bot** application (never a user account / self-bot)
- `git`
```
Dependencies (see `requirements.txt`):
```
```
discord.py>=2.4.0,<3.0.0
aiosqlite>=0.20.0
aiohttp>=3.9.0
python-dotenv>=1.0.0
```
```
---
```
## 4. Discord Developer Portal setup
```
1. Go to <https://discord.com/developers/applications> → **New Application** → name it
   (e.g. `RBX Family Manager`).
2. **Bot** tab → **Add Bot**.
   - Set the bot username/avatar.
   - Enable **Presence Intent**, **Server Members Intent**, and
     **Message Content Intent** (all three are required for logging, welcome,
     AutoMod and AFK features).
3. **Bot** tab → **Token** → **Reset Token** → copy it. This is your `DISCORD_TOKEN`.
   Never share it, never commit it, never paste it in a ticket.
4. **General Information** tab → copy the **Application ID** (this is your `OWNER_ID`'s
   counterpart — `OWNER_ID` is *your personal user ID*).
5. OAuth2 → **URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: see the table in the next section.
```
> To get your user ID: Discord → **User Settings → Advanced → Developer Mode ON** →
> right-click your avatar → **Copy User ID**.
```
---
```
## 5. Local installation
```
```bash
git clone https://github.com/<you>/rbx-family-manager.git
cd rbx-family-manager
```
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```
cp .env.example .env               # Windows: copy .env.example .env
nano .env                          # add DISCORD_TOKEN and OWNER_ID
```
python bot.py --init-db            # creates data/rbx_family.db from scratch
python bot.py                      # start the bot
```
```
`.env` variables:
```
| Variable | Required | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Bot token from the Developer Portal |
| `OWNER_ID` | ✅ | Your Discord user ID (space/comma separated for several) |
| `DATABASE_PATH` | ➖ | defaults to `data/rbx_family.db` |
| `DEV_GUILD_ID` | ➖ | instant slash-command sync while developing |
| `HEALTH_ENABLED` / `HEALTH_PORT` | ➖ | tiny `GET /health` endpoint (default `8080`) |
| `LOG_LEVEL` / `LOG_FILE` | ➖ | `INFO`, file at `data/rbx_family.log` |
| `SYNC_COMMANDS` | ➖ | set `false` to skip command syncing (CI) |
```
`.env` is listed in `.gitignore` — it can never be committed.
```
---
```
## 6. Bot invite URL
```
Format (replace `CLIENT_ID` with your Application ID):
```
```
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&permissions=PERMISSIONS&scope=bot%20applications.commands
```
```
Recommended permissions bitmask:
```
| Use case | Permissions | Value |
|---|---|---|
| Full setup (simplest) | Administrator | `8` |
| Least privilege | Ban, Kick, Moderate, Manage Messages, Manage Channels, Manage Roles, Read/Send, Embed Links | `1374924100678` |
```
Generate the exact URL with **OAuth2 → URL Generator → URL** in the Developer Portal.
```
**Scopes explained**
```
- `bot` — adds the bot user to the server.
- `applications.commands` — registers slash commands. Without it, `/commands` will not appear.
```
**Intent note:** the bot needs *Server Members* and *Message Content* privileged intents
enabled in the Developer Portal, otherwise welcome logging and AutoMod will silently fail.
```
---
```
## 7. Slash command sync: global vs guild
```
| | Global sync | Guild (dev) sync |
|---|---|---|
| Command | `python bot.py` with `DEV_GUILD_ID=` empty | `DEV_GUILD_ID=1234567890` in `.env` |
| Propagation | **up to 1 hour** | **instant** |
| Visible in | every server | only that server (+ global ones once they propagate) |
| Use for | production | development and testing |
```
`/owner sync` (owner only) re-syncs on demand:
```
- `/owner sync` — global
- `/owner sync guild_id:1234567890` — instant, in one guild
```
During development you can **also** register a test command in one guild without waiting:
```
```env
DEV_GUILD_ID=1234567890
```
```
---
```
## 8. Command reference
```
### 🛡️ Moderation
```
/ban user:@member reason:spam delete_days:0
/softban user:@member reason:cleanup
/unban user:123456789012345678 reason:appeal accepted
/kick user:@member reason:rule 3
/timeout user:@member duration:10m reason:spam
/mute user:@member duration:1h        (alias of timeout)
/untimeout user:@member               (alias: /unmute)
/warn user:@member reason:advertising
/warnings user:@member                (view history)
/warnmanage remove id:12
/warnmanage clear user:@member
/clear amount:50 user:@member         (alias: /purge)
/slowmode seconds:5
/lock channel:#general                (alias: /unlock)
/nick nickname:Guest user:@member
/massban users:111 222 <@333> reason:ban wave
/modlogs user:@member
```
```
Every dangerous action asks for confirmation with a button, checks role hierarchy,
DMs the target where possible, writes a case to the database and posts an embed to
the configured moderation log channel.
```
### ⚙️ AutoMod
```
/automod                                  (overview)
/automod toggle rule:anti-spam
/automod setting key:mention_limit value:6
/automod setting key:timeout_duration value:10m
/automod word word:badword action:add
/automod exempt target:@Staff kind:role action:add
/automod reset confirm:RESET
```
```
### 📋 Logging
```
/setlogchannel channel:#mod-logs type:moderation
/setlogchannel channel:#logs           (type defaults to "all")
/logchannels
/logtest
```
```
### 🎉 Giveaways
```
/giveaway start duration:1h winners:2 prize:Discord Nitro channel:#giveaways
/giveaway start duration:1d winners:1 prize:Robux role_requirement:@Active min_account_age:7d min_server_age:1d
/giveaway end id:3
/giveaway reroll id:3
/giveaway list
/giveaway cancel id:3 reason:no longer available
/giveaway info id:3
```
```
### 🎫 Tickets
```
/ticket setup category:Tickets staff_role:@Staff channel:#create-ticket
/ticket close reason:solved
/ticket add user:@Helper
/ticket remove user:@Helper
/ticket list
/ticket category category:Tickets
/ticket staff_role role:@Staff
```
```
### 👋 Welcome & roles
```
/setup logchannel:#logs welcomechannel:#welcome staffrole:@Staff autorole:@Member ticketcategory:Tickets ticketstaff:@Staff
         # one-shot: configures everything above, posts the welcome embed,
         # then shows a ✅/❌ checklist of what is still missing
/welcome setup channel:#welcome message:Welcome {user}! Member #{member_count} use_embed:true dm_welcome:false autorole:@Member
/welcome test | /welcome view | /welcome disable
/autorole set role:@Member | /autorole remove | /autorole view
/staffrole set role:@Staff | /staffrole remove | /staffrole view
```
```
Placeholders: `{user}` `{mention}` `{username}` `{server}` `{member_count}` `{id}`
```
### 🧰 Utility & 😂 Fun
```
/help | /ping | /botinfo | /uptime | /invite
/serverinfo | /userinfo | /avatar | /banner | /roleinfo | /channelinfo | /permissions
/afk message:brb
/remind duration:30m message:check the oven
/reminders | /timestamp time_text:tomorrow 18:00 style:F
/calc expression:2 + 2 * 10
/poll question:Lunch? options:Pizza, Sushi, Burgers
/embed title:News description:Hello\nWorld channel:#announcements
/announce message:Server update! role:@Everyone channel:#announcements
/8ball /coinflip /dice /roll /choose /rate /ship /roast /compliment /joke /meme
```
```
### 🖱️ Context menus (right-click a user)

```
User Info      → profile card (works for users who left the server too)
Warn member    → opens a modal for the reason, then records the warning
                (staff-only; full hierarchy safety checks apply)
```

### 🤖 Owner (OWNER_ID only)
```
/owner reload cog:moderation
/owner sync [guild_id:...]
/owner status | /owner guilds | /owner say content:... | /owner broadcast content:...
/owner shutdown
```
```
### 🔐 Per-server command permissions
```
/command name:ban action:disable
/command name:warn action:role role:@Moderators
/ignore target:#bots kind:channel action:add
/ignores
/config                (view everything configured for this server)
```
```
---
```
## 9. GitHub setup
```
```bash
git init
git add .gitignore README.md LICENSE requirements.txt .env.example \
        bot.py config.py Procfile Dockerfile docker-compose.yml \
        cogs database utils data deployment tests pytest.ini requirements-dev.txt
git status                       # confirm .env and data/*.db are NOT listed
git commit -m "RBX Family Manager v1.0"
git branch -M main
git remote add origin https://github.com/<you>/rbx-family-manager.git
git push -u origin main
```
```
`.gitignore` excludes `.env`, `data/*.db`, `data/*.log`, virtualenvs and caches.
**Never** force-add them (`git add -f .env`).
```
If you accidentally commit a token, treat it as leaked: reset the bot token in the
Developer Portal, then rewrite history (`git filter-repo`) and force-push.
```
---
```
## 10. Deployment: Oracle Cloud Always Free VM
```
> **Honest note about "24/7":** Oracle Cloud's *Always Free* tier gives you a real VM
> (e.g. `VM.Standard.E4.Flex` Ampere A1 or the older AMD shapes) that runs continuously
> — no sleep, no dyno cycling. The real limits are: a small number of OCPUs/RAM (the
> Ampere shape currently allows up to 4 OCPUs / 24 GB in total across your tenancy),
> regional capacity that can occasionally deny new instance creations, and Oracle's
> idle-policy reclamation for *some* always-free resources. A bot this size uses far
> under 0.5 OCPU / 256 MB RAM, so it fits comfortably — but **no free provider can
> promise guaranteed 100% uptime**. Keep a local fallback and monitor the health endpoint.
```
### Step 1 — Create an Oracle Cloud account
1. Visit <https://www.oracle.com/cloud/free/> and sign up (credit card is required for
   identity verification; you are not charged on Always Free).
2. Complete email/payment verification and pick a **home region** — regions differ in
   free-tier capacity, so choose one with good availability (e.g. US East/Phoenix).
```
### Step 2 — Create an Always Free VM
1. **Dashboard → Compute → Instances → Create Instance**.
2. Image: **Canonical Ubuntu 22.04/24.04 (aarch64 for Ampere shapes)**.
3. Shape: `VM.Standard.E4.Flex` (Ampere) — start with **1 OCPU / 1 GB RAM**
   (plenty for this bot; you can raise it later within the free allowance).
4. Networking: accept the default VCN/subnet (or let Oracle create one).
5. Add an **SSH key** (paste your public key, see Step 3).
6. **Create** and wait until the instance is RUNNING.
```
### Step 3 — Configure SSH
On your local machine:
```bash
ssh-keygen -t ed25519 -C "rbx-vm"
cat ~/.ssh/id_ed25519.pub        # paste this into the Oracle SSH key field
```
```
### Step 4 — Connect
```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@<PUBLIC_IP>
```
```
### Step 5 — Install Python, Git and tools
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git ufw
python3 --version        # 3.10+ required (3.12 on Ubuntu 24.04)
```
```
### Step 6 — Clone the repository
```bash
cd ~
git clone https://github.com/<you>/rbx-family-manager.git
cd rbx-family-manager
```
```
### Step 7 — Create `.env`
```bash
cp .env.example .env
nano .env          # fill in DISCORD_TOKEN and OWNER_ID, then Ctrl+O, Enter, Ctrl+X
chmod 600 .env
```
```
### Step 8 — Install requirements
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
```
### Step 9 — Initialize the database
```bash
python bot.py --init-db
# → Database ready: data/rbx_family.db (schema v1)
```
```
### Step 10 — Install the systemd service
```bash
sudo cp deployment/rbx-family-manager.service /etc/systemd/system/
sudo nano /etc/systemd/system/rbx-family-manager.service
#   • User / Group       → your Linux user (e.g. ubuntu)
#   • WorkingDirectory   → /home/ubuntu/rbx-family-manager
#   • Environment PATH   → /home/ubuntu/rbx-family-manager/.venv/bin:/usr/bin
#   • ExecStart          → /home/ubuntu/rbx-family-manager/.venv/bin/python .../bot.py
#   • ReadWritePaths     → /home/ubuntu/rbx-family-manager/data
```
sudo systemctl daemon-reload
sudo systemctl enable rbx-family-manager
sudo systemctl start rbx-family-manager
```
```
### Step 11 — Verify it is running
```bash
systemctl status rbx-family-manager
journalctl -u rbx-family-manager -f        # live logs (Ctrl+C exits the viewer)
```
```
You should see `Logged in as RBX Family Manager ...` and `Synced NN global command(s)`.
```
### Step 12 — Firewall (optional, for the health endpoint)
```bash
sudo ufw allow OpenSSH
sudo ufw enable
# Only expose the health port if you actually need external monitoring.
# sudo ufw allow 8080/tcp
```
```
### Step 13 — Update the bot from GitHub
```bash
cd ~/rbx-family-manager
git pull
source .venv/bin/activate
pip install -r requirements.txt
python bot.py --init-db        # applies any new migrations
sudo systemctl restart rbx-family-manager
```
```
---
```
## 11. systemd configuration
```
Full unit file: [`deployment/rbx-family-manager.service`](deployment/rbx-family-manager.service)
```
```ini
[Unit]
Description=RBX Family Manager Discord bot
After=network-online.target
Wants=network-online.target
```
[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/rbx-family-manager
Environment=PATH=/home/botuser/rbx-family-manager/.venv/bin:/usr/bin
ExecStart=/home/botuser/rbx-family-manager/.venv/bin/python /home/botuser/rbx-family-manager/bot.py
Restart=always
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGINT
StandardOutput=journal
StandardError=journal
SyslogIdentifier=rbx-family-manager
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/home/botuser/rbx-family-manager/data
```
[Install]
WantedBy=multi-user.target
```
```
What this gives you:
```
- **auto-start after reboot** (`WantedBy=multi-user.target` + `enable`)
- **automatic restart after a crash** (`Restart=always`, 5 s backoff)
- **clean shutdown** (SIGINT → the bot flushes and closes SQLite)
- **central logs** (`journalctl -u rbx-family-manager -f`)
```
Useful commands:
```
```bash
sudo systemctl start rbx-family-manager
sudo systemctl stop rbx-family-manager
sudo systemctl restart rbx-family-manager
sudo systemctl status rbx-family-manager
journalctl -u rbx-family-manager --since "10 min ago"
```
```
Alternatively run `./deployment/install.sh --systemd` which does venv + deps + `.env`
+ database + unit installation in one step.
```
---
```
## 12. Starting, stopping and updating the bot
```
| Action | Command |
|---|---|
| Start (foreground) | `./deployment/start.sh` |
| Start (service) | `sudo systemctl start rbx-family-manager` |
| Stop | `sudo systemctl stop rbx-family-manager` |
| Restart | `sudo systemctl restart rbx-family-manager` |
| Logs | `journalctl -u rbx-family-manager -f` |
| Update | `git pull && pip install -r requirements.txt && python bot.py --init-db && sudo systemctl restart rbx-family-manager` |
| Hot-reload a cog | `/owner reload cog:moderation` (no restart needed) |
```
### As a dedicated Linux user (recommended)
```
```bash
sudo useradd -m -s /bin/bash botuser
sudo -u botuser git clone https://github.com/<you>/rbx-family-manager.git /home/botuser/rbx-family-manager
sudo chown -R botuser:botuser /home/botuser/rbx-family-manager
sudo -u botuser nano /home/botuser/rbx-family-manager/.env
# then follow Steps 8–11 as botuser
```
```
---
```
## 13. Docker deployment
```
```bash
cp .env.example .env            # fill it in
mkdir -p data
docker compose up -d --build
docker compose logs -f
docker compose restart           # restart
docker compose pull && docker compose up -d --build   # update after git pull
```
```
The `data/` folder is bind-mounted so SQLite and logs survive container rebuilds, and
`restart: unless-stopped` brings the bot back after a crash or host reboot.
```
---
```
## 14. Health endpoint
```
When `HEALTH_ENABLED=true` (default) the bot serves:
```
```
GET /health
{
  "status": "online",
  "bot": "RBX Family Manager",
  "guilds": 3,
  "latency_ms": 42
}
```
```
Use it with UptimeRobot/BetterStack for **monitoring**. It is *not* a keep-alive trick
to defeat host sleep policies — this bot runs on a real VM that never sleeps, and the
endpoint exists only so you can be alerted if the service dies.
```
---
```
## 15. Security notes
```
- Secrets live only in `.env` (git-ignored, `chmod 600`). Nothing is hardcoded, and the
  token is never printed.
- Permission checks guard every command; hierarchy checks prevent punishing the owner,
  higher/equal roles, the bot itself, or a configured bot owner account.
- Owner commands are double-gated and only usable by `OWNER_ID`.
- `/calc` uses a strict AST walk — **no `eval`, no `exec`, no arbitrary Python**.
- User input is length-limited and control characters are stripped before it reaches embeds.
- Role/channel IDs are validated; role hierarchy is verified before assigning roles.
- Cooldowns are applied to spammy/dangerous commands; Discord rate limits are respected
  (`massban` paces its requests).
- Allowed mentions are restricted (`everyone=False`, no role pings unless you ask for one).
- Errors are logged server-side with tracebacks; Discord users only see a friendly embed.
```
---
```
## 16. Testing
```
```bash
pip install -r requirements-dev.txt
pytest
```
```
Coverage (144 tests):
```
| Area | Tests |
|---|---|
| Bot startup | every cog loads, all 64 commands + subcommands register, both context menus and all 3 persistent views register |
| Database | fresh empty DB creates all 16 tables, reopening is idempotent |
| Configuration | per-guild isolation, automod defaults, command/ignore settings |
| Command checks | ignored channels, admin bypass, per-command disable/role gates, staff role |
| Warnings | create, list, remove, clear, cross-guild isolation |
| Hierarchy | owner/self/equal-role/bot-owner/bot-hierarchy rejection, role assignment |
| Time parsing | `10m`, `1h30m`, `28d` limit, invalid input, formatting |
| AutoMod | spam, duplicates, invites, mentions, caps, emoji, exemptions (pure function) |
| Giveaways | creation, restart recovery, entries, duplicate rejection, winner drawing |
| Error handling | safe calculator input validation, embed truncation, `/health` JSON |
```
---
```
## 17. Troubleshooting
```
| Symptom | Cause / fix |
|---|---|
| `DISCORD_TOKEN is missing` | `.env` not created or empty — copy `.env.example` → `.env` |
| `LoginFailure: Improper token has been passed` | wrong/reset token — regenerate in Developer Portal and update `.env` |
| `Privileged intents required` | enable **Server Members** + **Message Content** (+ Presence) intents on the Bot tab |
| Slash commands don't show up | global sync takes up to 1 hour; set `DEV_GUILD_ID` for instant sync, or run `/owner sync` |
| `Missing Access` when inviting | the bot lacks a channel's permissions, or the invite is missing `applications.commands` scope |
| `My highest role is not above...` | drag the bot's role above the target's role in **Server Settings → Roles** |
| `I need Manage Roles` (autorole/tickets) | grant the bot **Manage Roles** and place its role above the assigned role |
| AutoMod never fires | check `/automod` master switch, exemptions, and that the channel isn't ignored |
| Welcome message not sent | `/welcome view` — confirm channel is set and the bot can send/embed there |
| Giveaway button dead after restart | restart is not needed; if it persists, run `/owner sync` then check channel permissions |
| `database is locked` | another process is writing the DB — run only one instance (`systemctl status`) |
| `Address already in use` (health port) | change `HEALTH_PORT` or set `HEALTH_ENABLED=false` |
| Bot is silent in logs | `journalctl -u rbx-family-manager -f`; also check `data/rbx_family.log` |
| Commands fail with "Not allowed" | per-server restriction — `/command name:x action:enable` or check `/config` |
| `PermissionError` writing logs | `mkdir -p data && chown` the folder to the service user |
```
Still stuck? Check `data/rbx_family.log` (full stack traces) and open an issue with the
exact command, the log excerpt and your Python/Discord versions.
```
---
```
## 18. Free hosting limitations (read this)
```
- **Oracle Cloud Always Free** is a genuine always-on VM — the correct free choice for a
  24/7 Discord bot. It does **not** sleep or cycle.
- Free tiers change. Shapes, OCPU/RAM allowances and regional capacity vary over time and
  per tenancy; confirm the current limits on Oracle's Always Free page before you rely on them.
- Idle/unused Always Free resources may be reclaimed by the provider in some programs;
  a running bot normally counts as usage, but check the current policy.
- Free tiers carry **no SLA**. Outages, maintenance and capacity denials happen.
- Free host alternatives (Render/Railway/Replit free tiers) usually **do** sleep or limit
  hours — they are a fallback, not a true 24/7 solution, and using keep-alive pings to
  defeat sleep policies violates some providers' terms. This project does not do that.
- Cheapest reliable upgrade if you outgrow free: a ~$5/mo VPS (Hetzner, DigitalOcean, Vultr).
```
---
```
## License
```
MIT — see [LICENSE](LICENSE).
```
**RBX Family Manager** — built for RBX Family.
