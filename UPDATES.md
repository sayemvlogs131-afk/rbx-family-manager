# 🔒 RBX Family Manager — release notes

The `/botupdatesetup` announcer reads this file and posts any new version to the
configured channel automatically (checked every 15 minutes + on startup).

Format rules:

- Each release is a `# vX.Y.Z — title` heading; newest release at the top.
- Bullet lines start with one of the status emoji: 🟢 Added, 🛠️ Fixed,
  ⚡ Improved, 🔒 Security, 🔴 Critical Issue.

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
