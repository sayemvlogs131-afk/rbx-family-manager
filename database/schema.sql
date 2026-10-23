-- ---------------------------------------------------------------------------
-- RBX Family Manager - SQLite schema
-- Applied by database/migrations.py on startup (safe to run repeatedly).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ------------------------------- Guild settings ---------------------------

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id                INTEGER PRIMARY KEY,
    prefix                  TEXT    NOT NULL DEFAULT '!',
    staff_role_id           INTEGER,
    mod_log_channel         INTEGER,
    member_log_channel      INTEGER,
    server_log_channel      INTEGER,
    message_log_channel     INTEGER,
    welcome_channel         INTEGER,
    welcome_message         TEXT,
    welcome_embed           INTEGER NOT NULL DEFAULT 1,
    dm_welcome              INTEGER NOT NULL DEFAULT 0,
    leave_channel           INTEGER,
    leave_message           TEXT,
    leave_embed             INTEGER NOT NULL DEFAULT 1,
    autorole_id             INTEGER,
    ticket_category_id      INTEGER,
    ticket_staff_role_id    INTEGER,    ticket_counter          INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ------------------------------- Automod ----------------------------------

CREATE TABLE IF NOT EXISTS automod_config (
    guild_id                INTEGER PRIMARY KEY,
    enabled                 INTEGER NOT NULL DEFAULT 1,
    anti_spam               INTEGER NOT NULL DEFAULT 1,
    anti_spam_messages      INTEGER NOT NULL DEFAULT 6,
    anti_spam_window        INTEGER NOT NULL DEFAULT 5,
    duplicate_filter        INTEGER NOT NULL DEFAULT 1,
    duplicate_window        INTEGER NOT NULL DEFAULT 7,
    mention_limit           INTEGER NOT NULL DEFAULT 6,
    emoji_limit             INTEGER NOT NULL DEFAULT 12,
    caps_limit              INTEGER NOT NULL DEFAULT 70,
    caps_min_length         INTEGER NOT NULL DEFAULT 12,
    invite_filter           INTEGER NOT NULL DEFAULT 1,
    scam_filter             INTEGER NOT NULL DEFAULT 1,
    link_filter             INTEGER NOT NULL DEFAULT 0,
    banned_words            TEXT    NOT NULL DEFAULT '[]',
    warn_threshold          INTEGER NOT NULL DEFAULT 3,
    timeout_duration        INTEGER NOT NULL DEFAULT 300,
    delete_violations       INTEGER NOT NULL DEFAULT 1,
    log_violations          INTEGER NOT NULL DEFAULT 1,
    exempt_roles            TEXT    NOT NULL DEFAULT '[]',
    exempt_channels         TEXT    NOT NULL DEFAULT '[]',
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ------------------------------- Warnings ---------------------------------

CREATE TABLE IF NOT EXISTS warnings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    moderator_id    INTEGER NOT NULL,
    reason          TEXT    NOT NULL DEFAULT 'No reason provided',
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_warnings_created ON warnings(created_at);

-- --------------------------- Moderation actions ---------------------------

CREATE TABLE IF NOT EXISTS moderation_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    moderator_id    INTEGER NOT NULL,
    action          TEXT    NOT NULL,
    reason          TEXT    NOT NULL DEFAULT 'No reason provided',
    duration        TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_mod_actions_guild_user ON moderation_actions(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_mod_actions_guild_action ON moderation_actions(guild_id, action);

-- ------------------------------- Giveaways --------------------------------

CREATE TABLE IF NOT EXISTS giveaways (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id            INTEGER NOT NULL,
    channel_id          INTEGER NOT NULL,
    message_id          INTEGER,
    host_id             INTEGER NOT NULL,
    prize               TEXT    NOT NULL,
    description         TEXT,
    winners_count       INTEGER NOT NULL DEFAULT 1,
    ends_at             INTEGER NOT NULL,          -- unix timestamp (seconds)
    ended               INTEGER NOT NULL DEFAULT 0,
    cancelled           INTEGER NOT NULL DEFAULT 0,
    required_role_id    INTEGER,
    min_account_age     INTEGER NOT NULL DEFAULT 0, -- seconds
    min_server_age      INTEGER NOT NULL DEFAULT 0, -- seconds
    winners             TEXT    NOT NULL DEFAULT '[]',
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_giveaways_active ON giveaways(ended, cancelled, ends_at);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id     INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    entered_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (giveaway_id, user_id),
    FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_giveaway_entries_user ON giveaway_entries(user_id);

-- ------------------------------- Tickets ----------------------------------

CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    owner_id        INTEGER NOT NULL,
    number          INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'open',  -- open | closed
    reason          TEXT,
    subject         TEXT,
    transcript      TEXT,
    claimed_by      INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_guild_status ON tickets(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_channel ON tickets(channel_id);

CREATE TABLE IF NOT EXISTS ticket_participants (
    ticket_id   INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    added_by    INTEGER,
    PRIMARY KEY (ticket_id, user_id),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);

-- ------------------------------- Reminders --------------------------------

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    remind_at   INTEGER NOT NULL,   -- unix timestamp (seconds)
    message     TEXT    NOT NULL,
    delivered   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(delivered, remind_at);

-- --------------------------- Command permissions --------------------------

CREATE TABLE IF NOT EXISTS command_config (
    guild_id        INTEGER NOT NULL,
    command_name    TEXT    NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    role_id         INTEGER,
    PRIMARY KEY (guild_id, command_name)
);

CREATE TABLE IF NOT EXISTS ignored_channels (
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS ignored_roles (
    guild_id    INTEGER NOT NULL,
    role_id     INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

-- ------------------------------- AFK --------------------------------------

CREATE TABLE IF NOT EXISTS afk (
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    message     TEXT    NOT NULL,
    since       INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

-- ------------------------------- Leveling ---------------------------------

CREATE TABLE IF NOT EXISTS user_levels (
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    xp          INTEGER NOT NULL DEFAULT 0,
    last_xp_at  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_levels_guild_xp ON user_levels(guild_id, xp DESC);

-- ------------------------------- Auto replies -----------------------------

CREATE TABLE IF NOT EXISTS autoreply_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    trigger     TEXT    NOT NULL,
    response    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_autorereply_guild ON autoreply_entries(guild_id);

-- --------------------------- Misc / utility --------------------------------

CREATE TABLE IF NOT EXISTS guild_stats (
    guild_id    INTEGER NOT NULL,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (guild_id, key)
);

-- ------------------------------- Security (anti-nuke) ---------------------
-- Per-guild anti-nuke configuration. Only server admins may change it and
-- every change is recorded in security_events.

CREATE TABLE IF NOT EXISTS security_config (
    guild_id            INTEGER PRIMARY KEY,
    anti_nuke_enabled   INTEGER NOT NULL DEFAULT 1,
    ban_limit           INTEGER NOT NULL DEFAULT 5,
    kick_limit          INTEGER NOT NULL DEFAULT 5,
    channel_limit       INTEGER NOT NULL DEFAULT 5,
    role_limit          INTEGER NOT NULL DEFAULT 5,
    window_seconds      INTEGER NOT NULL DEFAULT 60,
    join_limit          INTEGER NOT NULL DEFAULT 8,
    join_window_seconds INTEGER NOT NULL DEFAULT 30,
    raid_action         TEXT    NOT NULL DEFAULT 'alert',    -- alert | lockdown
    min_account_age_days INTEGER NOT NULL DEFAULT 3,
    action              TEXT    NOT NULL DEFAULT 'timeout',   -- alert | timeout | kick
    lockdown_on_nuke    INTEGER NOT NULL DEFAULT 0,
    trusted_role_ids    TEXT    NOT NULL DEFAULT '[]',
    trusted_user_ids    TEXT    NOT NULL DEFAULT '[]',
    log_channel         INTEGER,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Audit trail for every sensitive security action and every anti-nuke hit.

CREATE TABLE IF NOT EXISTS security_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    event       TEXT    NOT NULL,
    actor_id    INTEGER,
    target_id   INTEGER,
    details     TEXT,
    severity    TEXT    NOT NULL DEFAULT 'info',   -- info | warning | critical
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_security_events_guild ON security_events(guild_id, id DESC);
