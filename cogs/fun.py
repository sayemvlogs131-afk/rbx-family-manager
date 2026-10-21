"""😂 Fun commands for RBX Family Manager.

Lighthearted, server-friendly commands only — nothing aimed at harassing
individual members. Roasts/compliments are random and generic by design.
"""

from __future__ import annotations

import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import Embeds
from utils.helpers import truncate

EIGHT_BALL_ANSWERS = [
    "It is certain.",
    "It is decidedly so.",
    "Without a doubt.",
    "Yes — definitely.",
    "You may rely on it.",
    "As I see it, yes.",
    "Most likely.",
    "Outlook good.",
    "Yes.",
    "Signs point to yes.",
    "Reply hazy, try again.",
    "Ask again later.",
    "Better not tell you now.",
    "Cannot predict now.",
    "Concentrate and ask again.",
    "Don't count on it.",
    "My reply is no.",
    "My sources say no.",
    "Outlook not so good.",
    "Very doubtful.",
]

ROASTS = [
    "You have the same energy as an empty server on a Monday.",
    "Your last brain cell just filed for overtime.",
    "You're proof that autocorrect can't fix everything.",
    "If confidence were a currency, you'd still be in debt.",
    "You bring everyone joy… when you leave the voice channel.",
    "You're like a software update — nobody asked for you.",
    "Somewhere out there is a bug report with your username on it.",
    "You run on 1% battery and 100% audacity.",
]

COMPLIMENTS = [
    "You have fantastic taste in servers.",
    "Your vibe is genuinely immaculate.",
    "You make this server a better place.",
    "You're the reason the chat is fun.",
    "Your energy is contagious — in the good way.",
    "You're smarter than a search engine with Wi-Fi.",
    "You deserve the good ending.",
    "You're lowkey carrying this server.",
]

JOKES = [
    "Why did the developer go broke? Because he used up all his cache. 💸",
    "I told my computer I needed a break… now it won't stop sending me KitKat ads.",
    "Why do programmers prefer dark mode? Because light attracts bugs. 🐛",
    "There are 10 kinds of people: those who understand binary and those who don't.",
    "A SQL query walks into a bar, approaches two tables and asks: “Can I join you?”",
    "Why did the bot cross the road? To reach the other API endpoint.",
    "I would tell you a UDP joke, but you might not get it.",
    "My favourite database is SQLite — it's got no relations, just like my ex.",
]

class Fun(commands.Cog, name="Fun"):
    """Harmless entertainment commands."""

    category = "😂 Fun"
    category_description = "Games, randomisers and good vibes."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="8ball")
    @app_commands.describe(question="Ask the magic 8-ball anything")
    async def eight_ball(self, ctx: commands.Context, *, question: str) -> None:
        """Ask the magic 8-ball."""
        answer = random.choice(EIGHT_BALL_ANSWERS)
        embed = Embeds.question(
            "🎱 Magic 8-Ball",
            f"**Q:** {truncate(question, 250)}\n\n**A:** {answer}",
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="coinflip")
    async def coinflip(self, ctx: commands.Context) -> None:
        """Flip a coin."""
        result = random.choice(["Heads", "Tails"])
        await ctx.send(
            embed=Embeds.info("🪙 Coin flip", f"The coin landed on **{result}**!")
        )

    @commands.hybrid_command(name="dice")
    @app_commands.describe(sides="Number of sides (default 6)", count="How many dice")
    async def dice(
        self,
        ctx: commands.Context,
        sides: app_commands.Range[int, 2, 1000] = 6,
        count: app_commands.Range[int, 1, 10] = 1,
    ) -> None:
        """Roll one or more dice."""
        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls)
        text = ", ".join(str(r) for r in rolls)
        await ctx.send(
            embed=Embeds.info(
                "🎲 Roll",
                f"{'Die' if count == 1 else 'Dice'} (d{sides}): **{text}**\n"
                + (f"Total: **{total}**" if count > 1 else ""),
            )
        )

    @commands.hybrid_command(name="roll")
    @app_commands.describe(faces="Number of faces (default 6)")
    async def roll(self, ctx: commands.Context, faces: app_commands.Range[int, 2, 1000] = 6) -> None:
        """Roll a single die."""
        await ctx.send(
            embed=Embeds.info("🎲 Roll", f"You rolled a **{random.randint(1, faces)}** (d{faces})!")
        )

    @commands.hybrid_command(name="choose")
    @app_commands.describe(options="Options separated by commas (2-10)")
    async def choose(self, ctx: commands.Context, *, options: str) -> None:
        """Let the bot pick one of your options."""
        parts = [p.strip() for p in options.split(",") if p.strip()]
        if len(parts) < 2:
            await ctx.send(
                embed=Embeds.error(
                    "Not enough options", "Give me at least two options, separated by commas."
                ),
                ephemeral=True,
            )
            return
        if len(parts) > 10:
            await ctx.send(
                embed=Embeds.error("Too many options", "Maximum of 10 options."), ephemeral=True
            )
            return
        pick = random.choice(parts)
        await ctx.send(
            embed=Embeds.info("🤔 My choice", f"I pick… **{truncate(pick, 200)}**!")
        )

    @commands.hybrid_command(name="rate")
    @app_commands.describe(subject="What should be rated?")
    async def rate(self, ctx: commands.Context, *, subject: str) -> None:
        """Rate something from 0 to 100."""
        score = random.randint(0, 100)
        bar = "🟩" * (score // 10) + "🟥" * (10 - score // 10)
        await ctx.send(
            embed=Embeds.info(
                "💯 Rating",
                f"I'd rate **{truncate(subject, 150)}** a **{score}/100**\n{bar}",
            )
        )

    @commands.hybrid_command(name="ship")
    @app_commands.describe(user_a="First person", user_b="Second person (defaults to you)")
    async def ship(
        self,
        ctx: commands.Context,
        user_a: discord.User,
        user_b: Optional[discord.User] = None,
    ) -> None:
        """Calculate a playful compatibility score."""
        second = user_b or ctx.author
        if user_a.id == second.id:
            await ctx.send(
                embed=Embeds.error("Same person", "Ship two different people!"),
                ephemeral=True,
            )
            return
        seed = (user_a.id * 31 + second.id * 17) % 10000
        score = seed % 101
        if score > 80:
            verdict = "Perfect match 💞"
        elif score > 60:
            verdict = "Very promising ✨"
        elif score > 40:
            verdict = "Worth a shot 👀"
        elif score > 20:
            verdict = "Just friends 💬"
        else:
            verdict = "Not happening 💀"
        bar = "🟩" * (score // 10) + "🟥" * (10 - score // 10)
        await ctx.send(
            embed=Embeds.info(
                "💘 Ship",
                f"**{user_a}** + **{second}** = **{score}%**\n{bar}\n_{verdict}_",
            )
        )

    @commands.hybrid_command(name="roast")
    @app_commands.describe(user="Who to roast (light-hearted only)")
    async def roast(self, ctx: commands.Context, user: Optional[discord.User] = None) -> None:
        """Get a light-hearted, harmless roast."""
        target = user or ctx.author
        await ctx.send(
            embed=Embeds.info(
                "🔥 Roast",
                f"{target.mention}, {random.choice(ROASTS)}",
            )
        )

    @commands.hybrid_command(name="compliment")
    @app_commands.describe(user="Who to compliment")
    async def compliment(
        self, ctx: commands.Context, user: Optional[discord.User] = None
    ) -> None:
        """Give someone a compliment."""
        target = user or ctx.author
        await ctx.send(
            embed=Embeds.success(
                "💛 Compliment", f"{target.mention}, {random.choice(COMPLIMENTS)}"
            )
        )

    @commands.hybrid_command(name="joke")
    async def joke(self, ctx: commands.Context) -> None:
        """Tell a random (clean) joke."""
        await ctx.send(embed=Embeds.info("😄 Joke", random.choice(JOKES)))

    @commands.hybrid_command(name="meme")
    async def meme(self, ctx: commands.Context) -> None:
        """Post a random meme from a public meme API."""
        import aiohttp

        # Network round-trip first: acknowledge so Discord's 3s deadline is safe.
        await ctx.defer()
        url = "https://meme-api.com/gimme"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"status {resp.status}")
                    data = await resp.json()
        except Exception:
            await ctx.send(
                embed=Embeds.warning(
                    "Meme unavailable",
                    "The meme service could not be reached right now. Try again in a moment.",
                )
            )
            return

        embed = Embeds.info(
            truncate(str(data.get("title", "Meme")), 250),
            f"r/{data.get('subreddit', 'memes')} • 👍 {data.get('ups', 0)}",
        )
        image_url = data.get("url")
        if image_url:
            embed.set_image(url=image_url)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun(bot))
