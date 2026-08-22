import asyncio
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Hardcoded playlist, played in this order
PLAYLIST = [
    "https://youtu.be/KJ7ZLeWxo2A",
    "https://youtu.be/XbIeKkVrnRg",
    "https://youtu.be/b9w_paUjzKs",
]

YTDL_FORMAT_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamer 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


class GuildMusicState:
    """Per-server playback state."""

    def __init__(self):
        self.queue_index = 0
        self.voice_client: discord.VoiceClient | None = None
        self.text_channel: discord.abc.Messageable | None = None
        self.suppress_next_advance = False


states: dict[int, GuildMusicState] = {}


def get_state(guild_id: int) -> GuildMusicState:
    if guild_id not in states:
        states[guild_id] = GuildMusicState()
    return states[guild_id]


async def extract_stream(url: str):
    """Resolve a YouTube URL to a direct audio stream URL + title."""
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
    return data["url"], data.get("title", url)


async def play_track(guild: discord.Guild, index: int):
    state = get_state(guild.id)

    if state.voice_client is None or not state.voice_client.is_connected():
        return  # bot got disconnected, don't keep chaining tracks

    if index >= len(PLAYLIST):
        if state.text_channel:
            await state.text_channel.send("Playlist finished. Use `/play` to start again from the top.")
        state.queue_index = 0
        return

    state.queue_index = index
    url = PLAYLIST[index]

    try:
        stream_url, title = await extract_stream(url)
    except Exception as e:
        if state.text_channel:
            await state.text_channel.send(f"Couldn't play track {index + 1} ({e}). Skipping.")
        await play_track(guild, index + 1)
        return

    source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)

    def after_play(error):
        if error:
            print(f"Playback error: {error}")
        if state.suppress_next_advance:
            state.suppress_next_advance = False
            return
        asyncio.run_coroutine_threadsafe(play_track(guild, index + 1), bot.loop)

    state.voice_client.play(source, after=after_play)
    if state.text_channel:
        await state.text_channel.send(f"▶️ Now playing ({index + 1}/{len(PLAYLIST)}): **{title}**")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Slash command sync failed: {e}")


@bot.tree.command(name="play", description="Join your voice channel and start/resume the playlist")
async def play(interaction: discord.Interaction):
    if interaction.user.voice is None:
        await interaction.response.send_message("Join a voice channel first.")
        return

    state = get_state(interaction.guild.id)
    state.text_channel = interaction.channel

    if state.voice_client is None or not state.voice_client.is_connected():
        state.voice_client = await interaction.user.voice.channel.connect()

    if state.voice_client.is_playing():
        await interaction.response.send_message("Already playing.")
        return

    if state.voice_client.is_paused():
        state.voice_client.resume()
        await interaction.response.send_message("▶️ Resumed.")
        return

    await interaction.response.send_message("Starting playlist...")
    await play_track(interaction.guild, state.queue_index)


@bot.tree.command(name="stop", description="Stop playback and leave the voice channel")
async def stop(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if state.voice_client:
        state.voice_client.stop()
        await state.voice_client.disconnect()
        state.voice_client = None
    state.queue_index = 0
    await interaction.response.send_message("⏹️ Stopped and left the channel.")


@bot.tree.command(name="rewind", description="Restart the current track from the beginning")
async def rewind(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if state.voice_client is None or not state.voice_client.is_connected():
        await interaction.response.send_message("I'm not connected to a voice channel.")
        return
    state.text_channel = interaction.channel
    await interaction.response.send_message("⏪ Rewinding to the start of the current track.")
    state.suppress_next_advance = True  # stop the auto-advance from firing before we replay
    state.voice_client.stop()
    await play_track(interaction.guild, state.queue_index)


@bot.tree.command(name="skip", description="Skip to the next track in the playlist")
async def skip(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
        state.text_channel = interaction.channel
        state.voice_client.stop()  # after_play advances to the next track automatically
        await interaction.response.send_message("⏭️ Skipped.")
    else:
        await interaction.response.send_message("Nothing is playing.")


@bot.tree.command(name="pause", description="Pause playback")
async def pause(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if state.voice_client and state.voice_client.is_playing():
        state.voice_client.pause()
        await interaction.response.send_message("⏸️ Paused.")
    else:
        await interaction.response.send_message("Nothing is playing.")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env file before running the bot.")
    bot.run(TOKEN)
