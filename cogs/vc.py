"""🔊 Voice (VC) system for RBX Family Manager.

Slash commands for hanging out in a voice channel with the bot:

* ``/join`` and ``/disconnect`` — manual VC control
* ``/autojoin on|off|status`` — persistent auto-join: whenever the configured
  member connects to a voice channel, the bot quietly slides in after them
  (setting survives restarts, stored in ``guild_stats``).
* ``/play`` — queue a song by name or URL (yt-dlp resolves the audio stream,
  FFmpeg plays it). Includes queue/skip/pause/volume/loop/shuffle controls.

Design promise: the bot **never leaves a voice channel by itself**. No idle
timeouts, no "everyone left" checks — it stays until someone runs
``/disconnect`` (or the whole bot shuts down).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from collections import deque
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import Embeds
from utils.helpers import truncate

log = logging.getLogger("rbx.vc")

AUTOJOIN_KEY = "vc_autojoin"  # guild_stats key: {"user_id": ..., "channel_id": ...}
SESSION_KEY = "vc_session"   # guild_stats key: {"channel_id": ..., "volume":...,"tracks": [queries]}
RESTORE_LIMIT = 20  # max tracks re-resolved after a restart
DEFAULT_VOLUME = 0.5  # 50% keeps ears happy; /volume adjusts live
FFMPEG_BEFORE = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
    "-reconnect_on_http_error 429,502,503,504"
)
FFMPEG_OPTS = "-vn -loglevel error"


def _fmt_duration(seconds: Any) -> str:
    """Human readable duration, tolerant of yt-dlp's many response shapes."""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "live"
    if total <= 0:
        return "0:00"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class MissingMusicSource(RuntimeError):
    """yt-dlp (the music backend) is not installed on this host."""


_BOT_CHECK_WORDS = ("sign in to confirm", "confirm you\u2019re not a bot", "not a bot")


def _cookies_file() -> str:
    """Path to a cookies.txt the owner can drop in the bot folder (or via YTDLP_COOKIES env).

    YouTube blocks datacenter IPs — a signed-in browser's cookies are the usual fix.
    """
    from_env = os.environ.get("YTDLP_COOKIES", "").strip()
    if from_env and Path(from_env).is_file():
        return from_env
    root = Path(__file__).resolve().parent.parent / "cookies.txt"
    if root.is_file():
        return str(root)
    return ""


def _ydl_raw_pick(yt_dlp: Any, opts: dict[str, Any], target: str) -> dict[str, Any] | None:
    """Extract without yt-dlp's format selector and return the raw video info.

    YouTube's SABR migration makes yt-dlp's own "bestaudio" selector choke with
    "Requested format is not available" even when real media formats exist in
    the raw response (verified: 23 videoplayback formats). We bypass the
    selector and let :func:`_ydl_resolve` pick an audio stream itself.
    """
    with yt_dlp.YoutubeDL(dict(opts)) as ydl:
        raw = ydl.extract_info(target, download=False, process=False)
    if not raw:
        return None
    if "entries" in raw:
        raw = next((entry for entry in raw["entries"] if entry), None) or {}
    formats = [f for f in (raw.get("formats") or []) if f.get("url")]
    media = [
        f
        for f in formats
        if "storyboard" not in str(f.get("format_id"))
        and f.get("ext") != "mhtml"
    ]
    if not media and not raw.get("url"):
        return None
    return raw


def _ydl_extract(yt_dlp: Any, target: str) -> dict[str, Any]:
    """Attempt the extraction in-order: browser cookies (normal), then
    embedded-player clients, then plain. A selector failure triggers a raw
    re-pick so SABR-only responses still yield playable audio."""
    cookies = _cookies_file()
    base: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "default_search": "ytsearch1",
        "nocheckcertificate": True,
    }
    if cookies:
        base["cookiefile"] = cookies

    attempts: list[dict[str, Any]] = []
    if cookies:
        attempts.append({})  # cookies + plain default clients are the most reliable
    attempts.append(
        {"extractor_args": {"youtube": {"player_client": ["web_embedded", "tv_embedded"]}}}
    )
    attempts.append({})

    last_error = ""
    for extra in attempts:
        opts = {**base, **extra}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
        except yt_dlp.utils.DownloadError as exc:
            last_error = str(exc)
            if "Requested format is not available" in last_error:
                # YouTube serves SABR-era format lists the selector can't
                # name — grab the raw response and pick audio ourselves.
                info = _ydl_raw_pick(yt_dlp, opts, target)
                if info:
                    return info
            continue
        if info:
            return info
    raise LookupError(last_error or "No results")


class Track:
    """One queued audio source."""

    __slots__ = ("title", "page_url", "stream_url", "duration", "requester_id", "query")

    def __init__(
        self,
        title: str,
        page_url: str,
        stream_url: str,
        duration: Any,
        requester_id: int,
        query: str = "",
    ) -> None:
        self.title = title or "Unknown title"
        self.page_url = page_url or ""
        self.stream_url = stream_url
        self.duration = _fmt_duration(duration)
        self.requester_id = requester_id
        self.query = query  # original search text/URL, used for restart recovery


def _ydl_resolve(query: str) -> dict:
    """Blocking yt-dlp lookup — always call through ``asyncio.to_thread``.

    Returns playable title/urls/duration for the first match.
    Raises :class:`MissingMusicSource` when yt-dlp is not installed.
    """
    import re

    try:
        import yt_dlp
    except ImportError as exc:  # dependency missing on this host
        raise MissingMusicSource() from exc

    target = query.strip()
    if not re.match(r"^https?://", target):
        target = f"ytsearch1:{target}"

    info = _ydl_extract(yt_dlp, target)
    if not info:
        raise LookupError("No results")
    if "entries" in info:
        info = next((entry for entry in info["entries"] if entry), None) or {}
    if not info:
        raise LookupError("No results")
    if info.get("is_live"):
        raise LookupError("Live streams are not supported — pick a normal video")

    stream_url = info.get("url")
    if stream_url is None:
        # Some extractors only expose the stream on individual formats. Prefer
        # real audio entries; storyboards (thumbnail strips) are not playable.
        formats = [
            f
            for f in (info.get("formats") or [])
            if f.get("url")
            and "storyboard" not in str(f.get("format_id"))
            and f.get("ext") != "mhtml"
        ]
        audio = [f for f in formats if (f.get("acodec") or "none") != "none"]
        preferred = audio or formats
        if preferred:
            stream_url = preferred[-1].get("url")
    if stream_url is None:
        raise LookupError("No audio stream found")

    return {
        "title": info.get("title") or "Unknown title",
        "page_url": info.get("webpage_url") or "",
        "stream_url": stream_url,
        "duration": info.get("duration"),
    }


class GuildVC:
    """Per-guild voice session state (all in-memory; queue resets on reboot)."""

    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.vc: discord.VoiceClient | None = None
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.loop = False
        self.volume = DEFAULT_VOLUME
        self.session_channel_id: int | None = None  # last voice channel (restart recovery)
        self.text_channel_id: int | None = None  # where now-playing announcements go
        self.skip_requested = False
        self.stop_requested = False
        self.autojoin_user_id: int | None = None
        self.lock = asyncio.Lock()  # serialises connect/play state changes


class VCSystem(commands.Cog, name="VC System"):
    """Voice channel + music system (stays connected until /disconnect)."""

    category = "🔊 Voice"
    category_description = "Join voice channels, autofollow and music playback."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.states: dict[int, GuildVC] = {}

    # ------------------------------------------------------------- helpers
    def _state(self, guild: discord.Guild) -> GuildVC:
        state = self.states.get(guild.id)
        if state is None:
            state = GuildVC(guild)
            self.states[guild.id] = state
        return state

    def _vc(self, guild: discord.Guild) -> discord.VoiceClient | None:
        """The live voice client for a guild, or None (defensive vs stale refs)."""
        state = self._state(guild)
        if state.vc is not None and state.vc.is_connected():
            return state.vc
        state.vc = None
        return None

    # -------------------------------------------------- restart recovery
    async def _save_session(self, guild: discord.Guild, state: GuildVC) -> None:
        """Persist the queue (as resolvable queries) so a restart keeps music."""
        vc = self._vc(guild)
        channel_id = getattr(getattr(vc, "channel", None), "id", None) or state.session_channel_id
        if channel_id is None and not state.queue and state.current is None:
            try:
                await self.bot.db.set_stat(guild.id, SESSION_KEY, "")
            except Exception:
                log.debug("Could not clear session for guild %s", guild.id, exc_info=True)
            return
        tracks = []
        if state.current is not None and state.current.query:
            tracks.append(state.current.query)
        tracks.extend(t.query for t in list(state.queue)[:RESTORE_LIMIT] if t.query)
        payload: dict[str, Any] = {
            "channel_id": channel_id,
            "volume": round(state.volume, 2),
            "loop": state.loop,
            "tracks": tracks,
        }
        try:
            await self.bot.db.set_stat(guild.id, SESSION_KEY, json.dumps(payload))
        except Exception:
            log.debug("Could not save session for guild %s", guild.id, exc_info=True)

    async def _restore_session(self, guild: discord.Guild) -> None:
        """After a restart: rejoin the saved channel and replay the saved queue."""
        state = self._state(guild)
        try:
            raw = await self.bot.db.get_stat(guild.id, SESSION_KEY, "")
        except Exception:  # db not ready yet
            return
        if not raw:
            return
        try:
            payload = json.loads(raw)
            channel_id = int(payload["channel_id"])
            raw_tracks = list(payload.get("tracks") or [])[:RESTORE_LIMIT]
            if not channel_id or not self._is_voice_channel(guild, channel_id):
                return
            state.volume = float(payload.get("volume") or DEFAULT_VOLUME)
            state.loop = bool(payload.get("loop"))
        except (ValueError, TypeError, KeyError):
            log.debug("Bad session data for guild %s", guild.id)
            return

        # Rejoin + resolve tracks in the background so on_ready is not blocked.
        state.session_channel_id = channel_id
        asyncio.create_task(self._resume_playback(guild, state, channel_id, raw_tracks))

    def _is_voice_channel(self, guild: discord.Guild, channel_id: int) -> bool:
        return isinstance(guild.get_channel(channel_id), discord.VoiceChannel)

    async def _resume_playback(
        self, guild: discord.Guild, state: GuildVC, channel_id: int, queries: list[str]
    ) -> None:
        """Background task: reconnect and re-resolve the saved queue."""
        try:
            async with state.lock:
                if self._vc(guild) is None:
                    channel = guild.get_channel(channel_id)
                    if not isinstance(channel, discord.VoiceChannel):
                        return
                    await self._connect(state, channel)
                    state.session_channel_id = channel_id
            for index, query in enumerate(queries):
                if not self._vc(guild):  # disconnected while restoring
                    return
                try:
                    entry = await asyncio.to_thread(_ydl_resolve, query)
                except Exception:
                    log.debug("Restore lookup failed for %r", query, exc_info=True)
                    continue
                track = Track(
                    entry["title"],
                    entry["page_url"],
                    entry["stream_url"],
                    entry["duration"],
                    0,  # requester unknown after restart
                    query=query,
                )
                async with state.lock:
                    if index == 0 and not (self._vc(guild) and (self._vc(guild).is_playing() or self._vc(guild).is_paused())):
                        state.queue.appendleft(track)
                        state.current = None
                        await self._play_next(state, announce=True)
                    else:
                        state.queue.append(track)
                        self.bot.loop.create_task(self._save_session(guild, state))
            log.info("Session restored in guild %s (%s track(s))", guild.id, len(queries))
        except Exception:
            log.exception("Session restore failed in guild %s", guild.id)

    async def _notify(self, state: GuildVC, embed: discord.Embed) -> None:
        """Best-effort announcement into the channel /play was used from."""
        channel_id = state.text_channel_id
        if not channel_id:
            return
        channel = state.guild.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:  # pragma: no cover - best effort
                log.debug("Could not announce in %s", channel_id)

    async def _move(self, vc: discord.VoiceClient, channel: discord.abc.Snowflake) -> None:
        """move_to with self-deafen; older discord.py versions ignore the flag."""
        try:
            await vc.move_to(channel, self_deaf=True)  # type: ignore[call-arg]
        except TypeError:  # pragma: no cover - older discord.py
            await vc.move_to(channel)

    async def _connect(self, state: GuildVC, channel) -> None:
        state.vc = await channel.connect(timeout=20, self_deaf=True)

    async def _ensure_voice(self, ctx: commands.Context, channel=None) -> tuple[GuildVC, str]:
        """Connect/move the bot into ``channel`` (defaults to the caller's VC).

        Returns the per-guild state plus an error message ("" when fine).
        """
        guild = ctx.guild
        if guild is None:
            return self._state(guild or ctx), "This command only works inside a server."
        if channel is None:
            member_voice = getattr(ctx.author, "voice", None)
            channel = member_voice and member_voice.channel
        if channel is None:
            return self._state(guild), "You must be in a voice channel first (or name one)."

        state = self._state(guild)
        async with state.lock:
            vc = self._vc(guild)
            if vc is not None and vc.channel.id == channel.id:
                state.text_channel_id = ctx.channel.id
                state.session_channel_id = channel.id
                return state, ""
            if vc is not None and vc.is_playing():
                return state, (
                    f"I'm busy playing music in **{vc.channel.name}** — "
                    "use `/disconnect` there first."
                )
            try:
                if vc is not None:
                    await self._move(vc, channel)
                else:
                    await self._connect(state, channel)
            except asyncio.TimeoutError:
                return state, "Connecting to voice timed out — try again in a moment."
            except (discord.ClientException, RuntimeError):
                # RuntimeError covers "davey library needed" (missing voice deps).
                log.warning("Voice connect failed in %s", guild.id, exc_info=True)
                return state, (
                    "I couldn't join that voice channel — the voice backend may be "
                    "missing on this host. Check my permissions or the hosting logs."
                )
            state.text_channel_id = ctx.channel.id
            state.session_channel_id = channel.id
        return state, ""

    def _can_control(self, ctx: commands.Context) -> tuple[GuildVC | None, str]:
        """Shared guard: guild only + caller in the bot's voice channel."""
        guild = ctx.guild
        if guild is None:
            return None, "This command only works inside a server."
        state = self._state(guild)
        vc = self._vc(guild)
        if vc is None:
            return state, "I'm not in a voice channel right now."
        member_voice = getattr(ctx.author, "voice", None)
        same_channel = (
            member_voice and member_voice.channel and member_voice.channel.id == vc.channel.id
        )
        perms = getattr(ctx.author, "guild_permissions", None)
        if not same_channel and not (perms and perms.manage_guild):
            return state, (
                f"Join **{vc.channel.name}** first (or have Manage Server) to control the music."
            )
        return state, ""

    async def _apply_autojoin(self, guild: discord.Guild, state: GuildVC, channel_id: int) -> None:
        """Silently connect to the autojoin channel (used on boot and /autojoin on)."""
        if self._vc(guild) is not None:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            log.debug("Autojoin channel %s missing in guild %s", channel_id, guild.id)
            return
        try:
            async with state.lock:
                if self._vc(guild) is None:
                    await self._connect(state, channel)
            state.session_channel_id = channel_id
            log.info("Autojoin: connected to %s in guild %s", channel_id, guild.id)
        except (discord.ClientException, RuntimeError, asyncio.TimeoutError):
            log.debug("Autojoin connect failed for guild %s", guild.id, exc_info=True)

    # ------------------------------------------------------- playback core
    async def _play_next(self, state: GuildVC, announce: bool = True) -> None:
        """Pop the next queued track and start it (skips broken tracks)."""
        vc = self._vc(state.guild)
        if vc is None:
            return

        attempts = 0
        while attempts < 5:
            attempts += 1
            if vc.is_playing() or vc.is_paused():
                # Another caller (e.g. a track ending while /play runs) already
                # started the next track — don't steal the queue from under it.
                return
            track = state.queue.popleft() if state.queue else None
            if track is None:
                state.current = None
                return
            try:
                audio = discord.FFmpegPCMAudio(
                    track.stream_url,
                    before_options=FFMPEG_BEFORE,
                    options=FFMPEG_OPTS,
                )
                source = discord.PCMVolumeTransformer(audio, volume=state.volume)
            except Exception:
                log.exception("Failed to open audio stream in guild %s", state.guild.id)
                await self._notify(
                    state,
                    Embeds.error("Track skipped", f"**{track.title}** could not be loaded."),
                )
                continue

            state.current = track
            state.skip_requested = False
            state.stop_requested = False

            def _finished(_err: Exception | None, _state: GuildVC = state) -> None:
                fut = asyncio.run_coroutine_threadsafe(self._on_track_end(_state), self.bot.loop)
                try:
                    fut.result(timeout=10)
                except Exception:  # pragma: no cover - defensive
                    log.exception("Track-end handler crashed")

            try:
                vc.play(source, after=_finished)
            except (discord.ClientException, KeyError, RuntimeError) as exc:
                log.exception("Playback start failed in guild %s", state.guild.id)
                state.current = None
                await self._notify(
                    state,
                    Embeds.error("Track skipped", f"**{track.title}** failed to start ({exc})."),
                )
                continue

            if announce:
                await self._notify(
                    state,
                    Embeds.info(
                        "▶️ Now playing",
                        f"**{track.title}**\n`{track.duration}` • <@{track.requester_id}>\n{track.page_url}",
                    ),
                )
            return

        state.current = None
        state.queue.clear()
        await self._notify(
            state,
            Embeds.error(
                "Playback stopped",
                "Several tracks in a row failed to load — I'm staying connected; try `/play` again.",
            ),
        )

    async def _on_track_end(self, state: GuildVC) -> None:
        """Runs after each track finishes/skips: honour loop/skip/stop, continue."""
        if state.stop_requested:
            state.current = None
            state.stop_requested = False
            state.skip_requested = False
            await self._notify(
                state, Embeds.info("🛑 Stopped", "Queue cleared — I'm still in the channel.")
            )
            return
        finished = state.current
        state.current = None
        if state.loop and finished is not None and not state.skip_requested:
            state.queue.appendleft(finished)
        state.skip_requested = False
        try:
            async with state.lock:
                await self._play_next(state)
            await self._save_session(state.guild, state)
        except Exception:
            log.exception("Queue continuation failed in %s", state.guild.id)

    # ------------------------------------------------------------- commands
    async def _rejoin_after_bounce(self, guild: discord.Guild, state: GuildVC) -> None:
        """Get back into the session channel a few times when bounced out."""
        for attempt in range(3):
            await asyncio.sleep(4 * (attempt + 1))
            if self.bot.is_closed() or state.stop_requested or self._vc(guild) is not None:
                return
            try:
                channel = guild.get_channel(state.session_channel_id)
                if not isinstance(channel, discord.VoiceChannel):
                    return
                async with state.lock:
                    if self._vc(guild) is None:
                        await self._connect(state, channel)
                if self._vc(guild) is not None and not self._vc(guild).is_playing() and not self._vc(guild).is_paused():
                    if state.current or state.queue:
                        # Re-resolve the queue from the saved session (stream URLs
                        # expire after a disconnect).
                        await self._restore_session(guild)
                    elif state.loop:
                        await self._restore_session(guild)
                log.info("Rejoined voice in guild %s after being bounced out.", guild.id)
                return
            except Exception:
                log.warning("Rejoin attempt %s failed in guild %s", attempt + 1, guild.id, exc_info=True)
        log.warning("Giving up rejoining voice in guild %s — run /join to get me back.", guild.id)

    @commands.hybrid_command(name="join")
    @app_commands.describe(channel="Voice channel to join (defaults to the one you're in)")
    async def join_command(
        self,
        ctx: commands.Context,
        channel: Optional[discord.VoiceChannel] = None,
    ) -> None:
        """Join your current voice channel (or any channel you pick)."""
        # Connecting to voice can take a few seconds; without a defer Discord
        # expires the interaction at 3s and the reply 404s (Unknown interaction).
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.send(embed=Embeds.error("Servers only", "Use this inside a server."))
            return
        state, err = await self._ensure_voice(ctx, channel)
        if err:
            # Deferred above: send as followup (ephemeral flag rides the defer).
            await ctx.send(embed=Embeds.error("Can't join", err), ephemeral=True)
            return
        voice = self._vc(ctx.guild)
        target = (channel and channel.name) or (voice and voice.channel and voice.channel.name) or "?"
        await ctx.send(
            embed=Embeds.success(
                "Joined voice",
                f"I'm in **{target}** and I'll stay until you use `/disconnect`.",
            )
        )

    @commands.hybrid_group(name="autojoin")
    async def autojoin(self, ctx: commands.Context) -> None:
        """Make the bot follow you into voice automatically."""
        if ctx.invoked_subcommand is None:
            await ctx.send(
                embed=Embeds.info("Autojoin", "Use `/autojoin on`, `/autojoin off` or `/autojoin status`.")
            )

    @autojoin.command(name="on")
    async def autojoin_on(self, ctx: commands.Context) -> None:
        """Enable autojoin: I follow YOU into voice automatically."""
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.send(embed=Embeds.error("Servers only", "Use this inside a server."))
            return
        member_voice = getattr(ctx.author, "voice", None)
        channel = member_voice and member_voice.channel
        if channel is None:
            await ctx.send(
                embed=Embeds.error("Join a voice channel", "Hop into a voice channel, then run this."),
                ephemeral=True,
            )
            return
        state = self._state(ctx.guild)
        state.autojoin_user_id = ctx.author.id
        state.session_channel_id = channel.id
        payload = json.dumps({"user_id": ctx.author.id, "channel_id": channel.id})
        try:
            await self.bot.db.set_stat(ctx.guild.id, AUTOJOIN_KEY, payload)
        except Exception:
            log.exception("Could not save autojoin for guild %s", ctx.guild.id)
            await ctx.send(embed=Embeds.error("Not saved", "Autojoin couldn't be stored right now."), ephemeral=True)
            return
        await ctx.send(
            embed=Embeds.success(
                "Autojoin enabled",
                f"From now on I follow <@{ctx.author.id}> into **{channel.name}** automatically.\n"
                "This survives restarts — `/autojoin off` disables it.",
            )
        )
        await self._apply_autojoin(ctx.guild, state, channel.id)

    @autojoin.command(name="off")
    async def autojoin_off(self, ctx: commands.Context) -> None:
        """Disable autojoin (persists until you switch it back on)."""
        if ctx.guild is None:
            await ctx.send(embed=Embeds.error("Servers only", "Use this inside a server."), ephemeral=True)
            return
        state = self._state(ctx.guild)
        state.autojoin_user_id = None
        try:
            await self.bot.db.set_stat(ctx.guild.id, AUTOJOIN_KEY, "")
        except Exception:
            log.exception("Could not clear autojoin for guild %s", ctx.guild.id)
            await ctx.send(embed=Embeds.error("Not saved", "Autojoin couldn't be switched off right now."), ephemeral=True)
            return
        await ctx.send(embed=Embeds.success("Autojoin disabled", "I'll stop auto-following — you're in control again."))

    @autojoin.command(name="status")
    async def autojoin_status(self, ctx: commands.Context) -> None:
        """Show who/where autojoin is bound to right now."""
        if ctx.guild is None:
            await ctx.send(embed=Embeds.error("Servers only", "Use this inside a server."), ephemeral=True)
            return
        raw = ""
        try:
            raw = await self.bot.db.get_stat(ctx.guild.id, AUTOJOIN_KEY, "")
        except Exception:  # pragma: no cover - defensive
            log.exception("Could not read autojoin for guild %s", ctx.guild.id)
        if not raw:
            await ctx.send(embed=Embeds.info("Autojoin status", "Autojoin is **off**. Turn it on with `/autojoin on`."))
            return
        try:
            data = json.loads(raw)
            uid = int(data["user_id"])
            cid = int(data["channel_id"])
        except (ValueError, TypeError, KeyError):
            await ctx.send(
                embed=Embeds.error("Autojoin status", "Stored autojoin config is corrupt — run `/autojoin on` again.")
            )
            return
        channel = ctx.guild.get_channel(cid)
        name = getattr(channel, "name", "unknown channel")
        await ctx.send(embed=Embeds.info("Autojoin status", f"I follow <@{uid}> into **{name}** automatically."))

    @commands.hybrid_command(name="play")
    @commands.cooldown(3, 15, commands.BucketType.user)
    @app_commands.describe(query="Song name or URL (YouTube/SoundCloud/direct links)")
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        """Play a song by link or search text (queues when one is already playing)."""
        if ctx.guild is None:
            await ctx.send(embed=Embeds.error("Servers only", "Use this inside a server."), ephemeral=True)
            return
        await ctx.defer()
        state, err = await self._ensure_voice(ctx, None)
        if err:
            # We already deferred, so this is a followup to a public message —
            # the ephemeral flag is not honoured there.
            await ctx.send(embed=Embeds.error("Can't play", err))
            return

        try:
            entry = await asyncio.to_thread(_ydl_resolve, query)
        except MissingMusicSource:
            await ctx.send(
                embed=Embeds.error(
                    "Music backend missing",
                    "yt-dlp is not installed on this host. Add `yt-dlp` to requirements.txt and restart.",
                )
            )
            return
        except LookupError as exc:
            msg = str(exc).lower()
            if "sign in to confirm" in msg or "not a bot" in msg:
                await ctx.send(
                    embed=Embeds.error(
                        "YouTube wants me to sign in 🔒",
                        "YouTube is blocking this server's IP (I already tried embedded-player fallbacks). "
                        "Fix: export a `cookies.txt` from your browser (use the \u201cGet cookies.txt LOCALLY\u201d "
                        "extension on youtube.com), then upload the file to the bot Files panel as `cookies.txt`. "
                        "No restart needed — just run /play again and I'll pick it up instantly.",
                    )
                )
            else:
                await ctx.send(embed=Embeds.error("Not found", f"Could not resolve that song: {exc}"))
            return
        except Exception:
            log.exception("yt-dlp lookup failed for %r", query)
            await ctx.send(embed=Embeds.error("Lookup failed", "The song lookup crashed — try a different search or link."))
            return

        track = Track(
            entry["title"],
            entry["page_url"],
            entry["stream_url"],
            entry["duration"],
            ctx.author.id,
            query=query,
        )
        state.queue.append(track)
        # Persist the queue so a restart/crash keeps the music session alive.
        try:
            await self._save_session(ctx.guild, state)
        except Exception:  # pragma: no cover - defensive
            log.debug("Session save failed in guild %s", ctx.guild.id, exc_info=True)

        vc = self._vc(ctx.guild)
        if vc is not None and (vc.is_playing() or vc.is_paused()):
            await ctx.send(
                embed=Embeds.success(
                    "Added to queue",
                    f"**{track.title}** (`{track.duration}`) — position **{len(state.queue)}**.",
                )
            )
            return
        try:
            async with state.lock:
                await self._play_next(state)
        except Exception:
            log.exception("Playback failed in guild %s", ctx.guild.id)
            await ctx.send(embed=Embeds.error("Playback failed", "Something went wrong while starting the track."))
            return

    @commands.hybrid_command(name="skip")
    async def skip(self, ctx: commands.Context) -> None:
        """Skip the current song (also breaks out of a looping song)."""
        state, err = self._can_control(ctx)
        if err:
            await ctx.send(embed=Embeds.error("Can't skip", err), ephemeral=True)
            return
        vc = state.vc
        if vc is None or (not vc.is_playing() and not vc.is_paused()):
            await ctx.send(embed=Embeds.error("Nothing to skip", "No track is playing right now."), ephemeral=True)
            return
        state.skip_requested = True
        vc.stop()
        await ctx.send(embed=Embeds.success("Skipped ⏭️", "Playing the next queued track."))

    @commands.hybrid_command(name="pause")
    async def pause(self, ctx: commands.Context) -> None:
        """Pause the music (I stay connected)."""
        state, err = self._can_control(ctx)
        if err:
            await ctx.send(embed=Embeds.error("Can't pause", err), ephemeral=True)
            return
        vc = state.vc
        if vc is None or not vc.is_playing() or vc.is_paused():
            await ctx.send(embed=Embeds.error("Nothing playing", "There's no track to pause."), ephemeral=True)
            return
        vc.pause()
        await ctx.send(embed=Embeds.success("Paused ⏸️", "Music paused — I'm still here."))

    @commands.hybrid_command(name="resume")
    async def resume(self, ctx: commands.Context) -> None:
        """Resume paused music."""
        state, err = self._can_control(ctx)
        if err:
            await ctx.send(embed=Embeds.error("Can't resume", err), ephemeral=True)
            return
        vc = state.vc
        if vc is None or not vc.is_paused():
            await ctx.send(embed=Embeds.error("Nothing paused", "No paused track right now."), ephemeral=True)
            return
        vc.resume()
        await ctx.send(embed=Embeds.success("Resumed ▶️", "Music is back on."))

    @commands.hybrid_command(name="stop")
    async def stop(self, ctx: commands.Context) -> None:
        """Stop playing and empty the queue — but I stay in the voice channel."""
        state, err = self._can_control(ctx)
        if err:
            await ctx.send(embed=Embeds.error("Can't stop", err), ephemeral=True)
            return
        state.queue.clear()
        state.current = None
        vc = state.vc
        if vc is not None and (vc.is_playing() or vc.is_paused()):
            state.stop_requested = True
            vc.stop()
        else:
            state.current = None
        await self._save_session(ctx.guild, state)
        await ctx.send(embed=Embeds.success("Stopped 🛑", "Queue cleared. I'm staying connected until `/disconnect`."))

    @commands.hybrid_command(name="disconnect")
    async def disconnect(self, ctx: commands.Context) -> None:
        """Disconnect me from voice (the only way I leave unless kicked)."""
        state, err = self._can_control(ctx)
        if err:
            await ctx.send(embed=Embeds.error("Can't disconnect", err), ephemeral=True)
            return
        vc = state.vc
        state.queue.clear()
        state.current = None
        state.loop = False
        state.stop_requested = True  # the bounce handler must not rejoin
        state.vc = None
        try:
            await self.bot.db.set_stat(ctx.guild.id, SESSION_KEY, "")
        except Exception:  # pragma: no cover - defensive
            log.debug("Could not clear session in guild %s", ctx.guild.id, exc_info=True)
        if vc is not None:
            try:
                await vc.disconnect()
            except Exception:  # pragma: no cover - best effort cleanup
                log.exception("Disconnect failed in guild %s", ctx.guild.id)
        await ctx.send(embed=Embeds.success("Disconnected 👋", "Left the voice channel and cleared the queue."))

    @commands.hybrid_command(name="volume")
    @app_commands.describe(level="Player volume from 1 to 150 (default 50)")
    async def volume(self, ctx: commands.Context, level: app_commands.Range[int, 1, 150]) -> None:
        """Change my playback volume."""
        state, err = self._can_control(ctx)
        if err:
            await ctx.send(embed=Embeds.error("Can't change volume", err), ephemeral=True)
            return
        state.volume = level / 100
        vc = state.vc
        if vc is not None and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = state.volume
        await ctx.send(embed=Embeds.success("Volume set 🔊", f"Playback volume is now **{level}%**"))

    @commands.hybrid_command(name="loop")
    @app_commands.describe(enabled="on/off (leave empty to toggle)")
    async def loop_command(self, ctx: commands.Context, enabled: Optional[bool] = None) -> None:
        """Loop the current song until it's stopped."""
        state, err = self._can_control(ctx)
        if err:
            await ctx.send(embed=Embeds.error("Can't toggle loop", err), ephemeral=True)
            return
        state.loop = (not state.loop) if enabled is None else bool(enabled)
        word = "on 🔁" if state.loop else "off ➡️"
        await ctx.send(embed=Embeds.success("Loop set", f"Looping is {word}"))

    @commands.hybrid_command(name="shuffle")
    async def shuffle(self, ctx: commands.Context) -> None:
        """Shuffle the queued tracks."""
        state, err = self._can_control(ctx)
        if err:
            await ctx.send(embed=Embeds.error("Can't shuffle", err), ephemeral=True)
            return
        if len(state.queue) < 2:
            await ctx.send(embed=Embeds.error("Not enough songs", "Queue at least two tracks to shuffle."), ephemeral=True)
            return
        items = list(state.queue)
        random.shuffle(items)
        state.queue = deque(items)
        await ctx.send(embed=Embeds.success("Shuffled 🔀", f"{len(state.queue)} tracks mixed up."))

    @commands.hybrid_command(name="queue")
    async def queue_list(self, ctx: commands.Context) -> None:
        """Show what's playing and what's next."""
        if ctx.guild is None:
            await ctx.send(embed=Embeds.error("Servers only", "Use this inside a server."), ephemeral=True)
            return
        state = self._state(ctx.guild)
        lines: list[str] = []
        if state.current is not None:
            lines.append(
                f"▶️ **{truncate(state.current.title, 60)}** (`{state.current.duration}`)"
                + (" *(looped)*" if state.loop else "")
            )
        for idx, track in enumerate(list(state.queue)[:10], start=1):
            lines.append(f"{idx}. **{truncate(track.title, 60)}** (`{track.duration}`)")
        if len(state.queue) > 10:
            lines.append(f"…and {len(state.queue) - 10} more")
        if not lines:
            await ctx.send(embed=Embeds.info("Music queue", "Nothing playing — use `/play` to start something!"))
            return
        await ctx.send(embed=Embeds.info("Music queue", "\n".join(lines)))

    @commands.hybrid_command(name="nowplaying")
    async def nowplaying(self, ctx: commands.Context) -> None:
        """Show the current song details."""
        if ctx.guild is None:
            await ctx.send(embed=Embeds.error("Servers only", "Use this inside a server."), ephemeral=True)
            return
        state = self._state(ctx.guild)
        track = state.current
        if track is None:
            await ctx.send(embed=Embeds.info("Now playing", "Nothing is playing right now — `/play` something!"))
            return
        vc = self._vc(ctx.guild)
        status = "paused ⏸️" if vc is not None and vc.is_paused() else "playing ▶️"
        description = (
            f"**{track.title}**\n`{track.duration}` • <@{track.requester_id}> • {status}"
            + (" • loop 🔁" if state.loop else "")
        )
        if track.page_url:
            description += f"\n{track.page_url}"
        await ctx.send(embed=Embeds.info("Now playing", description))

    # ------------------------------------------------------------ listeners
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Restore persisted autojoin settings and music sessions after restart."""
        try:
            await self._load_autojoins()
        except Exception:
            log.exception("Autojoin restore failed", exc_info=True)
        # Music recovery: rejoin + re-resolve the saved queue (background).
        for guild in self.bot.guilds:
            try:
                await self._restore_session(guild)
            except Exception:  # pragma: no cover - defensive
                log.exception("Session restore failed for guild %s", guild.id)

    async def _load_autojoins(self) -> None:
        """Read every guild's saved autojoin config and reconnect."""
        for guild in self.bot.guilds:
            try:
                raw = await self.bot.db.get_stat(guild.id, AUTOJOIN_KEY, "")
            except Exception:  # pragma: no cover - db not ready yet
                return
            if not raw:
                continue
            try:
                data = json.loads(raw)
                state = self._state(guild)
                state.autojoin_user_id = int(data["user_id"])
                await self._apply_autojoin(guild, state, int(data["channel_id"]))
            except (ValueError, TypeError, KeyError):
                log.debug("Bad autojoin config for guild %s", guild.id)
                continue

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Autojoin follow + auto-rejoin when the bot is bounced out of voice."""
        guild = member.guild
        if member.id == self.bot.user.id:  # type: ignore[union-attr]
            if after.channel is None:
                state = self.states.get(guild.id)
                if state is not None and state.vc is not None and not state.vc.is_connected():
                    stopped_ourselves = state.stop_requested
                    state.vc = None
                    if stopped_ourselves:
                        state.current = None
                        state.queue.clear()
                        state.stop_requested = False
                        try:
                            await self.bot.db.set_stat(guild.id, SESSION_KEY, "")
                        except Exception:
                            log.debug("Could not clear session in guild %s", guild.id, exc_info=True)
                    elif state.session_channel_id:
                        # Kicked/bounced out (network glitch, region move): slide
                        # back in and keep the queue alive instead of dying.
                        self.bot.loop.create_task(self._rejoin_after_bounce(guild, state))
            return

        state: GuildVC | None = self.states.get(guild.id)
        if state is None or state.autojoin_user_id is None or member.id != state.autojoin_user_id:
            return
        if after.channel is None or after.channel == before.channel:
            return
        vc = self._vc(guild)
        if vc is not None and vc.channel.id == after.channel.id:
            return
        try:
            if vc is not None:
                await self._move(vc, after.channel)
            else:
                async with state.lock:
                    if self._vc(guild) is None:
                        await self._connect(state, after.channel)
        except (discord.ClientException, RuntimeError, asyncio.TimeoutError):
            log.debug("Autojoin into %s failed in %s", after.channel.id, guild.id, exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VCSystem(bot))
