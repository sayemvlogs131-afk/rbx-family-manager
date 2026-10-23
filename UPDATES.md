# 🔒 RBX Family Manager — release notes

The `/botupdatesetup` announcer reads this file and posts any new version to the
configured channel automatically (checked every 15 minutes + on startup).

Format rules:

- Each release is a `# vX.Y.Z — title` heading; newest release at the top.
- Bullet lines start with one of the status emoji: 🟢 Added, 🛠️ Fixed,
  ⚡ Improved, 🔒 Security, 🔴 Critical Issue.

# v2.4.0 — Reaction roles: self-serve role panels

🟢 Added: `/rolesetup setup channel:` — creates a role panel: a message with one button per role. Members click to get the role and click again to remove it — no staff needed, no pinging mods.
🟢 Added: `/rolesetup add role: label:` (up to 25 roles per panel), `/rolesetup remove role:`, `/rolesetup list` and `/rolesetup reset`.
⚡ Improved: panels are persistent — buttons keep working after bot restarts and panel re-images because every panel is rebuilt from the database on boot. Panels also clean themselves up if a role on them gets deleted.

# v2.3.0 — Power update: leveling, scam shield, music recovery, full logger

🟢 Added: `/rank` and `/leaderboard` — members earn XP by chatting (one gain per minute), levels auto-grant configured roles via `/rankrole add level: role:`, and level-up announcements go anywhere you set with `/levelsetup channel:`. Backed by a new indexed `user_levels` table.
🟢 Added: music recovery — after a restart or crash the bot rejoins its last voice channel and re-resolves the whole saved queue (up to 20 tracks) automatically; saved volume and loop too. Music survives panel re-images and reboots.
🟢 Added: scam/phishing blocker — known scam-shop domains (fake nitro/steam/robux shops and more) are deleted the moment they're posted (3 violation points). On by default; toggle in `/automod`.
🟢 Added: even more complete server log — webhook changes, thread creation, and server settings renamed now all logged too.
⚡ Improved: `/owner backup` snapshots the database instantly (auto snapshots every 6h already).

# v2.2.3 — /join fix + database power-up

🛠️ Fixed: `/join` failed with a 404 when joining voice took longer than 3 seconds — the bot now defers the reply first, so joining always responds cleanly no matter how slow the connection.
🟢 Added: automatic database backups — a full snapshot is written to `data/backups/` right after every boot and every 6 hours after that; the newest 7 are kept, older ones auto-delete. Server data wipes can no longer lose everything.
🟢 Added: `/owner backup` — the bot owner can snapshot the database at any moment with one command.
⚡ Improved: the database now has speed indexes on warnings, tickets, giveaways, reminders, auto-replies and the security audit trail — noticeably snappy as data grows.

# v2.2.2 — Everything-on-GitHub update + /scan

🟢 Added: `/scan` — owner-only full server audit in one embed: members, channels, roles, automod/anti-nuke settings, log channels, welcome, tickets, giveaways, warnings count and an auto-generated **Problems found** list (broken config references, missing bot permissions, unreachable role position).
🛠️ Fixed: shutdown crash spam — background jobs (giveaway watcher, reminders, update announcer) touched the already-closed database during restarts; the bot now shuts tasks down first and every job bails out cleanly.
🛠️ Fixed: permission-denied errors (e.g. `/botstatus set` by non-owner) no longer dump a raw Python traceback into the console log — they're normal check failures and are only sent to the user as a clean embed.
⚡ Improved: the panel's GitHub auto-pull is now the only update path — the zip folder stays local only.

# v2.2.1 — Music fix for "Sign in to confirm you're not a bot"

🛠️ Fixed: `/play` on YouTube could fail with "Sign in to confirm you're not a bot" — that's YouTube blocking datacenter IPs, not a bug in the bot.
⚡ Improved: `/play` now automatically retries with embedded-player clients, and supports a `cookies.txt` file (export from your browser, then upload it to the bot Files panel as `cookies.txt`) to lift the block permanently.
✅ Fixed: voice join (`/join`, `/autojoin`) now includes the `davey` library required by discord.py 2.7+ — joining works on all hosts.

# v2.2.0 — Voice & music system

🟢 Added: `/join` — the bot hops into the voice channel you're in (or any channel you pick).
🟢 Added: `/autojoin on|off|status` — the bot automatically follows you into voice channels, and the setting survives restarts.
🟢 Added: `/play` — play any song by name or link (YouTube, SoundCloud and more). Works as a queue so friends can add tracks too.
🟢 Added: full music controls — `/skip`, `/pause`, `/resume`, `/stop`, `/volume`, `/loop`, `/shuffle`, `/queue`, `/nowplaying`.
🟢 Added: the bot **never leaves voice by itself** — it stays in the channel (even when idle or alone) until you use `/disconnect`.
⚡ Improved: music playback now uses yt-dlp + the container's FFmpeg, with automatic skip over broken tracks so the queue never jams.

# v2.1.1 — Server icon

🟢 Added: `/servericon` — see any server's icon in full size (`/servericon full:yes` also shows the server banner).

# v2.1.0 — Goodbyes, raids & web dashboard

🟢 Added: `/leave setup channel:#welcome message:...` — a full goodbye system: every leaver is announced with their mention, avatar, account age and time-in-server; the message is fully customizable, and `/leave test` previews it.
🟢 Added: `/changelog` — anyone can run it to see exactly what was added to the bot in the latest update.
🟢 Added: anti-raid protection — sudden floods of joins (e.g. 8+ within 30 seconds, configurable in `/securitysetup`) warn the owner and can auto-lockdown the server.
🟢 Added: suspicious-account detection — brand-new accounts that join are flagged to staff so alt/raid accounts dont slip through.
🟢 Added: alerts when anyone adds a **bot** to the server — the owner is DM'd with who invited it and is told to review its permissions.
🟢 Added: alerts when someone creates a **webhook** — the owner is told who created it and in which channel.
🟢 Added: mass-**kick** protection — nuking a server with kicks is now caught just like mass bans/deletes (own limit in `/securitysetup`).
🟢 Added: the web dashboard now shows the live bot status (online / maintenance), version, changelog, per-server **anti-nuke settings** and the full security **audit log** — one place to see everything.
⚡ Improved: the bot's **Discord presence** now honestly shows its state — 🟢 normal, 🛠 Do Not Disturb during maintenance, ⚠ idle when degraded.
⚡ Improved: `/botstatus` and update announcements look cleaner with a status note, mention-free layout and better grouping.
⚡ Improved: `/serverinfo` now shows humans vs bots, emojis, stickers and verification level.
🔒 Security: if the bot itself has problems (a cog fails to load, a command crashes), the **bot owner gets a DM** with what happened.

# v2.0.0 — Security hardening & anti-nuke release

🟢 Added: `/botupdatesetup` — pick a channel and the bot posts every new release there automatically with 🟢 Added / 🛠️ Fixed / ⚡ Improved / 🔒 Security / 🔴 Critical Issue categories.
🟢 Added: `/botstatus` — live status embed (🟢 Online / 🟡 Maintenance / 🔴 Issues) with version, uptime and the latest update; status settable by the bot owner.
🟢 Added: `/securitysetup` — interactive anti-nuke panel: enable/disable, action limits, breach action (alert / timeout / kick), trusted roles & users, security log channel.
🟢 Added: `/permissionscheck` — one-tap audit of bot permissions, role reach, dangerous-permission holders and configuration gaps.
🟢 Added: `/auditlogs` — browsable log of every sensitive security action and anti-nuke event.
🟢 Added: `/emergencylockdown` — owner-verified full-server lockdown with one click, and instant release.
🟢 Added: catch-all anti-nuke protection with audit-log attribution: mass bans, mass channel deletes and mass role deletes are detected, the actor is identified, the server owner is alerted and (configurable) the abuser is timed out or kicked.
🛠️ Fixed: `/autoreply add|remove|toggle` could previously be used by **any member** — they now require administrator access.
🛠️ Fixed: `/massban` was reachable by anyone with Ban Members — it now requires administrator access.
⚡ Improved: owner verification now re-checks the *actual* Discord server owner ID instead of trusting cached/check-time data.
⚡ Improved: command cooldowns tightened on destructive commands; purge/lock actions rate-limited.
🔒 Security: the admin web dashboard now requires a token (`Authorization: Bearer …`) — it no longer exposes your server list publicly.
🔒 Security: all dangerous configuration commands are default-deny; only server admins (or the owners) can change them and every change is audit-logged.
🔴 Critical Issue: none outstanding.
