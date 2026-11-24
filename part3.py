

@client.tree.command(name="commands", description="Show a quick list of every command")
async def commands_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="All commands", color=discord.Color.blurple())
    embed.add_field(
        name="Profile",
        value=(
            "`/profile set` `/profile create` `/profile use` `/profile list` `/profile delete` "
            "`/profile view` `/profile random` `/profile reset`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Voice / TTS",
        value="`/tts` `/ttslang` `/mytts` `/ttsmaxlen` `/messagemute` `/leave`",
        inline=False,
    )
    embed.add_field(
        name="Message relay",
        value="`/edit` `/snipe` `/pin` `/relaytoggle`  —  plus auto-relay and a ❌ reaction to delete",
        inline=False,
    )
    embed.add_field(
        name="Verification",
        value=(
            "`/verifypanel` `/verify lookup` `/verify list` `/verify stats` `/verify export` `/verifyuser` "
            "`/verifyrolesetup` `/setverify` `/setlog` `/unverify`  —  plus Approve/Deny buttons"
        ),
        inline=False,
    )
    embed.add_field(
        name="Moderation",
        value=(
            "`/purge` `/fullpurge` `/kick` `/ban` `/timeout` `/untimeout` `/warn` `/warnings` "
            "`/clearwarn` `/modlogs` `/unban` `/lockdown` `/unlock` "
            "`/slowmode` `/rolemessage` `/rolemessageoff` `/blockword` `/blockinvites`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Utility",
        value=(
            "`/setstorage` `/imagelink` `/avatar` `/userinfo` `/serverinfo` `/ping` `/remindme` `/afk` "
            "`/poll` `/suggest` `/setsuggestions` `/starboard` `/welcomemsg` `/leavemsg` "
            "`/8ball` `/coinflip` `/roll`"
        ),
        inline=False,
    )
    embed.set_footer(text="Use /help for an explanation of what each one does.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ================================================================== image link
@app_commands.command(name="imagelink", description="Get a direct link for an uploaded image")
@app_commands.describe(image="The image to get a link for")
async def imagelink(interaction: discord.Interaction, image: discord.Attachment):
    if not (image.content_type or "").startswith("image/"):
        await interaction.response.send_message("That attachment isn't an image.", ephemeral=True)
        return

    # Discord attachment URLs on the CDN expire; re-hosting it in a channel the
    # bot controls gives back a link that stays valid indefinitely.
    try:
        ch = await get_storage_channel(interaction.guild_id)
        data = await image.read()
        stored = await ch.send(file=discord.File(io.BytesIO(data), filename=image.filename))
        link = stored.attachments[0].url
    except RuntimeError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(f"Couldn't process that image: {e}", ephemeral=True)
        return

    await interaction.response.send_message(f"Here's your image's valid link:\n{link}")


# ================================================================== events
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.webhook_id:
        return

    if message.guild is None:
        # A DM reply to the emoji-panel verify prompt
        guild_id = pending_dm_verify.get(message.author.id)
        if guild_id:
            result = await submit_tiktok_verification(guild_id, message.author, message.content)
            pending_dm_verify.pop(message.author.id, None)
            try:
                await message.channel.send(result)
            except discord.HTTPException:
                pass
        return

    # AFK: clear the sender's own status if they talk again
    own_key = pkey(message.guild.id, message.author.id)
    if own_key in afk_users:
        del afk_users[own_key]
        try:
            await message.channel.send(
                f"Welcome back {message.author.mention}, I've removed your AFK status.", delete_after=5
            )
        except discord.HTTPException:
            pass

    # AFK: let the sender know if anyone they @mentioned is away
    if message.mentions:
        notes = []
        for mentioned in message.mentions:
            m_key = pkey(message.guild.id, mentioned.id)
            if m_key in afk_users:
                notes.append(f"{mentioned.display_name} is AFK: {afk_users[m_key]['note']}")
        if notes:
            try:
                await message.channel.send("\n".join(notes), delete_after=8)
            except discord.HTTPException:
                pass

    # Branch 1: a voice channel's built-in text chat with TTS switched on
    if isinstance(message.channel, discord.VoiceChannel):
        if message.channel.id not in tts_channels or not message.content:
            return
        if not message.author.voice or message.author.voice.channel.id != message.channel.id:
            return
        if message.content.lstrip().startswith("-"):
            return  # "-" prefix: send the message but don't speak it

        voice_client = voice_clients.get(message.guild.id)
        if not voice_client or not voice_client.is_connected():
            return

        maxlen = tts_maxlen.get(str(message.guild.id), 200)
        text = message.content[:maxlen]
        lang = mytts_lang.get(pkey(message.guild.id, message.author.id)) or tts_lang.get(message.guild.id, "en")
        loop = asyncio.get_running_loop()

        def make_mp3():
            path = f"/tmp/tts_{message.id}.mp3"
            gTTS(text=text, lang=lang).save(path)
            return path

        try:
            path = await loop.run_in_executor(None, make_mp3)
        except Exception as e:
            print(f"TTS generation failed: {e}")
            return

        while voice_client.is_playing():
            await asyncio.sleep(0.5)
        voice_client.play(discord.FFmpegPCMAudio(path))
        return

    # Branch 2: a normal text channel/thread -> automod, then proxy through the webhook
    if message.type not in (discord.MessageType.default, discord.MessageType.reply):
        return

    guild_key = str(message.guild.id)
    content_lower = message.content.lower()

    if blockinvites.get(guild_key) and INVITE_RE.search(message.content):
        try:
            await message.delete()
            notice = await message.channel.send(f"{message.author.mention} invite links aren't allowed here.", delete_after=6)
        except discord.HTTPException:
            pass
        return

    for word in blockwords.get(guild_key, []):
        if word in content_lower:
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} that message contained a blocked word.", delete_after=6)
            except discord.HTTPException:
                pass
            return

    blocked_channels = relay_blocklist.get(guild_key, [])
    base_channel_id = message.channel.parent.id if isinstance(message.channel, discord.Thread) else message.channel.id
    if base_channel_id in blocked_channels:
        return  # relay disabled in this channel; leave the message as-is

    await proxy_message(message)


@client.event
async def on_member_join(member: discord.Member):
    config = welcome_config.get(str(member.guild.id))
    if config:
        await send_join_leave_message(member, config)


@client.event
async def on_member_remove(member: discord.Member):
    config = leavemsg_config.get(str(member.guild.id))
    if config:
        await send_join_leave_message(member, config)


@client.event
async def on_member_update(before: discord.Member, after: discord.Member):
    config = role_announce_config.get(after.guild.id)
    if not config:
        return

    added_role_ids = {r.id for r in after.roles} - {r.id for r in before.roles}
    if config["role_id"] not in added_role_ids:
        return

    channel = after.guild.get_channel(config["channel_id"])
    if not channel:
        return

    role = after.guild.get_role(config["role_id"])
    text = (
        config["message"]
        .replace("{user}", after.mention)
        .replace("{role}", role.mention if role else "that role")
        .replace("{server}", after.guild.name)
    )
    try:
        await channel.send(text)
    except discord.HTTPException:
        pass


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.member is None or payload.member.bot:
        return

    panel = verify_emoji_panels.get(str(payload.message_id))
    if panel and panel["guild_id"] == payload.guild_id and str(payload.emoji) == panel["emoji"]:
        try:
            channel = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
            msg = await channel.fetch_message(payload.message_id)
            await msg.remove_reaction(payload.emoji, payload.member)
        except discord.HTTPException:
            pass
        try:
            await payload.member.send(
                f"Reply here with your TikTok username (e.g. `@yourname`) to verify for "
                f"**{payload.member.guild.name}**."
            )
            pending_dm_verify[payload.member.id] = payload.guild_id
        except discord.Forbidden:
            pass  # their DMs are closed; nothing more we can do
        return

    if str(payload.emoji) == "⭐":
        config = starboard_config.get(str(payload.guild_id))
        if config and payload.message_id not in starboard_posted:
            try:
                channel = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
                msg = await channel.fetch_message(payload.message_id)
                star_reaction = discord.utils.get(msg.reactions, emoji="⭐")
                count = star_reaction.count if star_reaction else 0
                if count >= config["threshold"]:
                    board = client.get_channel(config["channel_id"]) or await client.fetch_channel(config["channel_id"])
                    embed = discord.Embed(description=msg.content or "*attachment*", color=discord.Color.gold())
                    embed.set_author(name=str(msg.author), icon_url=msg.author.display_avatar.url)
                    embed.add_field(name="Source", value=f"[Jump to message]({msg.jump_url})", inline=False)
                    if msg.attachments:
                        embed.set_image(url=msg.attachments[0].url)
                    posted = await board.send(content=f"⭐ {count}", embed=embed)
                    starboard_posted[payload.message_id] = posted.id
            except discord.HTTPException:
                pass
        return

    if str(payload.emoji) not in CANCEL_EMOJIS:
        return

    author_id = relay_authors.get(payload.message_id)
    if author_id is None:
        return  # not a relayed message we're tracking (or already handled)

    # Only the original sender, or someone who can manage messages, may delete it.
    if payload.user_id != author_id and not payload.member.guild_permissions.manage_messages:
        return

    content_record = relay_content.pop(payload.message_id, None)
    if content_record:
        last_snipe[payload.channel_id] = {
            "author_id": content_record["author_id"],
            "content": content_record["content"],
            "deleted_at": discord.utils.utcnow(),
        }

    try:
        channel = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
        msg = await channel.fetch_message(payload.message_id)
        await msg.delete()
    except discord.HTTPException:
        pass
    relay_authors.pop(payload.message_id, None)


client.tree.add_command(verifypanel)
client.tree.add_command(imagelink)
client.tree.add_command(unverify)
client.tree.add_command(verifyuser)
client.tree.add_command(verifyrolesetup)
client.tree.add_command(setverify)
client.tree.add_command(setlog)
client.tree.add_command(setstorage)
client.tree.add_command(setsuggestions)

# ================================================================== economy / leveling / games
# This file is loaded by main.py via exec(), sharing its globals (client, discord,
# app_commands, pkey, load_json_file, save_json_file, etc. are all already available).

ECONOMY_FILE = "economy.json"
DAILY_FILE = "daily.json"
XP_FILE = "xp.json"
SHOP_FILE = "shop.json"

economy_data: dict = load_json_file(ECONOMY_FILE, {})  # "guild:user" -> balance (int)
daily_claims: dict = load_json_file(DAILY_FILE, {})  # "guild:user" -> iso timestamp of last claim
xp_data: dict = load_json_file(XP_FILE, {})  # "guild:user" -> {"xp": int, "level": int}
shop_items: dict = load_json_file(SHOP_FILE, {})  # "guild_id" -> {item_name: {"price": int, "role_id": int|None}}

xp_cooldown: dict[str, float] = {}  # "guild:user" -> last XP-award timestamp (in-memory only)
XP_COOLDOWN_SECONDS = 60
DAILY_COOLDOWN_SECONDS = 24 * 3600


def get_balance(guild_id: int, user_id: int) -> int:
    return economy_data.get(pkey(guild_id, user_id), 0)


def add_balance(guild_id: int, user_id: int, amount: int) -> int:
    key = pkey(guild_id, user_id)
    economy_data[key] = economy_data.get(key, 0) + amount
    save_json_file(ECONOMY_FILE, economy_data)
    return economy_data[key]


def xp_for_level(level: int) -> int:
    return 100 * level * level  # simple increasing curve


# ---------------------------------------------------------------- economy commands
@client.tree.command(name="balance", description="Check your (or someone else's) coin balance")
@app_commands.describe(user="Whose balance to check (defaults to you)")
async def balance_cmd(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    target = user or interaction.user
    bal = get_balance(interaction.guild_id, target.id)
    await interaction.response.send_message(f"💰 {target.mention} has **{bal}** coins.")


@client.tree.command(name="daily", description="Claim your daily coins")
async def daily_cmd(interaction: discord.Interaction):
    key = pkey(interaction.guild_id, interaction.user.id)
    last = daily_claims.get(key)
    if last:
        elapsed = time.time() - last
        if elapsed < DAILY_COOLDOWN_SECONDS:
            remaining = DAILY_COOLDOWN_SECONDS - elapsed
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            await interaction.response.send_message(
                f"You've already claimed today. Try again in {hours}h {minutes}m.", ephemeral=True
            )
            return

    amount = random.randint(50, 150)
    new_balance = add_balance(interaction.guild_id, interaction.user.id, amount)
    daily_claims[key] = time.time()
    save_json_file(DAILY_FILE, daily_claims)
    await interaction.response.send_message(f"💰 You claimed **{amount}** coins! Balance: **{new_balance}**.")


@client.tree.command(name="gamble", description="Bet coins on a 50/50 coin flip")
@app_commands.describe(amount="How many coins to bet")
async def gamble_cmd(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1000000]):
    bal = get_balance(interaction.guild_id, interaction.user.id)
    if amount > bal:
        await interaction.response.send_message(f"You only have **{bal}** coins.", ephemeral=True)
        return

    won = random.random() < 0.5
    delta = amount if won else -amount
    new_balance = add_balance(interaction.guild_id, interaction.user.id, delta)
    if won:
        await interaction.response.send_message(f"🪙 Heads! You won **{amount}** coins. Balance: **{new_balance}**.")
    else:
        await interaction.response.send_message(f"🪙 Tails. You lost **{amount}** coins. Balance: **{new_balance}**.")


shop_group = app_commands.Group(name="shop", description="Buy roles and items with your coins", guild_only=True)


@shop_group.command(name="list", description="See what's for sale")
async def shop_list(interaction: discord.Interaction):
    items = shop_items.get(str(interaction.guild_id), {})
    if not items:
        await interaction.response.send_message("The shop is empty.", ephemeral=True)
        return
    lines = [f"**{name}** — {data['price']} coins" for name, data in items.items()]
    await interaction.response.send_message("🛒 **Shop:**\n" + "\n".join(lines))


@shop_group.command(name="buy", description="Buy an item from the shop")
@app_commands.describe(name="The item's name, exactly as shown in /shop list")
async def shop_buy(interaction: discord.Interaction, name: str):
    items = shop_items.get(str(interaction.guild_id), {})
    item = items.get(name)
    if not item:
        await interaction.response.send_message("No item with that name.", ephemeral=True)
        return
    bal = get_balance(interaction.guild_id, interaction.user.id)
    if bal < item["price"]:
        await interaction.response.send_message(f"That costs {item['price']} coins — you have {bal}.", ephemeral=True)
        return

    add_balance(interaction.guild_id, interaction.user.id, -item["price"])
    if item.get("role_id"):
        role = interaction.guild.get_role(item["role_id"])
        if role:
            try:
                await interaction.user.add_roles(role, reason=f"Bought {name} from /shop")
            except discord.Forbidden:
                pass
    await interaction.response.send_message(f"✅ Bought **{name}** for {item['price']} coins.")


@shop_group.command(name="add", description="Add an item to the shop")
@app_commands.describe(name="Item name", price="Cost in coins", role="A role to grant on purchase (optional)")
@app_commands.checks.has_permissions(administrator=True)
async def shop_add(interaction: discord.Interaction, name: str, price: app_commands.Range[int, 1, 1000000], role: Optional[discord.Role] = None):
    items = shop_items.setdefault(str(interaction.guild_id), {})
    items[name] = {"price": price, "role_id": role.id if role else None}
    save_json_file(SHOP_FILE, shop_items)
    await interaction.response.send_message(f"Added **{name}** ({price} coins) to the shop.", ephemeral=True)


@shop_add.error
async def shop_add_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You need Administrator to do that.", ephemeral=True)
    else:
        print(f"/shop add error: {error}")


@shop_group.command(name="remove", description="Remove an item from the shop")
@app_commands.describe(name="Item name to remove")
@app_commands.checks.has_permissions(administrator=True)
async def shop_remove(interaction: discord.Interaction, name: str):
    items = shop_items.get(str(interaction.guild_id), {})
    if name not in items:
        await interaction.response.send_message("No item with that name.", ephemeral=True)
        return
    del items[name]
    save_json_file(SHOP_FILE, shop_items)
    await interaction.response.send_message(f"Removed **{name}**.", ephemeral=True)


@shop_remove.error
async def shop_remove_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You need Administrator to do that.", ephemeral=True)
    else:
        print(f"/shop remove error: {error}")


client.tree.add_command(shop_group)


# ---------------------------------------------------------------- leveling
@client.tree.command(name="rank", description="Check your (or someone else's) level and XP")
@app_commands.describe(user="Whose rank to check (defaults to you)")
async def rank_cmd(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    target = user or interaction.user
    data = xp_data.get(pkey(interaction.guild_id, target.id), {"xp": 0, "level": 0})
    next_needed = xp_for_level(data["level"] + 1)
    await interaction.response.send_message(
        f"📊 {target.mention} is **level {data['level']}** with **{data['xp']} XP** "
        f"({next_needed - data['xp']} XP to next level)."
    )


@client.tree.command(name="leaderboard", description="Show the top members by coins or XP")
@app_commands.describe(board="Which leaderboard to show")
@app_commands.choices(
    board=[
        app_commands.Choice(name="Coins", value="coins"),
        app_commands.Choice(name="XP", value="xp"),
    ]
)
async def leaderboard_cmd(interaction: discord.Interaction, board: Optional[app_commands.Choice[str]] = None):
    which = board.value if board else "coins"
    prefix = f"{interaction.guild_id}:"

    if which == "coins":
        rows = sorted(
            ((k, v) for k, v in economy_data.items() if k.startswith(prefix)), key=lambda kv: kv[1], reverse=True
        )[:10]
        title = "💰 Coin leaderboard"
        lines = [f"{i+1}. <@{k.split(':',1)[1]}> — {v} coins" for i, (k, v) in enumerate(rows)]
    else:
        rows = sorted(
            ((k, v) for k, v in xp_data.items() if k.startswith(prefix)), key=lambda kv: kv[1]["xp"], reverse=True
        )[:10]
        title = "📊 XP leaderboard"
        lines = [f"{i+1}. <@{k.split(':',1)[1]}> — level {v['level']} ({v['xp']} XP)" for i, (k, v) in enumerate(rows)]

    if not lines:
        await interaction.response.send_message("No one's on the board yet.", ephemeral=True)
        return
    await interaction.response.send_message(f"**{title}**\n" + "\n".join(lines))


async def award_xp_on_message(message: discord.Message):
    """Separate listener (not @client.event) so it doesn't clobber the main on_message handler."""
    if message.author.bot or not message.guild or not message.content:
        return
    key = pkey(message.guild.id, message.author.id)
    last = xp_cooldown.get(key, 0)
    if time.time() - last < XP_COOLDOWN_SECONDS:
        return
    xp_cooldown[key] = time.time()

    data = xp_data.setdefault(key, {"xp": 0, "level": 0})
    data["xp"] += random.randint(10, 20)
    leveled_up = False
    while data["xp"] >= xp_for_level(data["level"] + 1):
        data["level"] += 1
        leveled_up = True
    save_json_file(XP_FILE, xp_data)

    if leveled_up:
        try:
            await message.channel.send(f"🎉 {message.author.mention} leveled up to **level {data['level']}**!")
        except discord.HTTPException:
            pass


# (hooked in near the bottom of this file, chained onto the existing on_message handler)


# ---------------------------------------------------------------- guess the number
active_guess_games: dict[int, dict] = {}  # channel_id -> {"number", "max", "starter_id"}


@client.tree.command(name="guessnumber", description="Start a guess-the-number game in this channel")
@app_commands.describe(max_number="Upper bound for the number (default 100)")
async def guessnumber(interaction: discord.Interaction, max_number: app_commands.Range[int, 10, 1000000] = 100):
    if interaction.channel_id in active_guess_games:
        await interaction.response.send_message("There's already a game running in this channel.", ephemeral=True)
        return
    active_guess_games[interaction.channel_id] = {
        "number": random.randint(1, max_number),
        "max": max_number,
        "starter_id": interaction.user.id,
    }
    await interaction.response.send_message(
        f"🔢 I'm thinking of a number between 1 and {max_number}. Type your guess in chat!"
    )


async def guess_number_listener(message: discord.Message):
    game = active_guess_games.get(message.channel.id)
    if not game or message.author.bot:
        return
    if not message.content.strip().lstrip("-").isdigit():
        return
    guess = int(message.content.strip())
    if guess == game["number"]:
        reward = max(10, game["max"] // 10)
        add_balance(message.guild.id, message.author.id, reward)
        del active_guess_games[message.channel.id]
        try:
            await message.channel.send(f"🎉 {message.author.mention} got it — the number was **{game['number']}**! +{reward} coins.")
        except discord.HTTPException:
            pass
    elif guess < game["number"]:
        try:
            await message.add_reaction("⬆️")
        except discord.HTTPException:
            pass
    else:
        try:
            await message.add_reaction("⬇️")
        except discord.HTTPException:
            pass


# (hooked in near the bottom of this file, chained onto the existing on_message handler)


# ---------------------------------------------------------------- hangman
HANGMAN_WORDS = [
    "python", "discord", "keyboard", "internet", "pizza", "guitar", "rocket", "forest",
    "shadow", "diamond", "elephant", "battery", "dolphin", "volcano", "castle",
]
HANGMAN_STAGES = [
    "```\n  +---+\n      |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n  |   |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|   |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n /    |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n / \\  |\n      |\n=========```",
]
active_hangman_games: dict[int, dict] = {}  # channel_id -> {"word", "guessed": set, "wrong": int}


def hangman_display(game: dict) -> str:
    word_display = " ".join(c if c in game["guessed"] else "_" for c in game["word"])
    wrong_letters = ", ".join(sorted(c for c in game["guessed"] if c not in game["word"]))
    return f"{HANGMAN_STAGES[game['wrong']]}\n**{word_display}**\nWrong guesses: {wrong_letters or 'none'}"


@client.tree.command(name="hangman", description="Start a game of hangman in this channel")
async def hangman_start(interaction: discord.Interaction):
    if interaction.channel_id in active_hangman_games:
        await interaction.response.send_message("There's already a hangman game running here.", ephemeral=True)
        return
    word = random.choice(HANGMAN_WORDS)
    active_hangman_games[interaction.channel_id] = {"word": word, "guessed": set(), "wrong": 0}
    await interaction.response.send_message(
        "🔡 Hangman started! Type single letters (or guess the whole word) in chat.\n"
        + hangman_display(active_hangman_games[interaction.channel_id])
    )


async def hangman_listener(message: discord.Message):
    game = active_hangman_games.get(message.channel.id)
    if not game or message.author.bot:
        return
    guess = message.content.strip().lower()
    if not guess or not guess.isalpha():
        return

    if len(guess) > 1:
        if guess == game["word"]:
            del active_hangman_games[message.channel.id]
            add_balance(message.guild.id, message.author.id, 50)
            try:
                await message.channel.send(f"🎉 {message.author.mention} guessed the word **{game['word']}**! +50 coins.")
            except discord.HTTPException:
                pass
        return  # wrong full-word guesses don't cost a life

    if guess in game["guessed"]:
        return
    game["guessed"].add(guess)

    if guess not in game["word"]:
        game["wrong"] += 1

    solved = all(c in game["guessed"] for c in game["word"])
    failed = game["wrong"] >= len(HANGMAN_STAGES) - 1

    if solved:
        del active_hangman_games[message.channel.id]
        add_balance(message.guild.id, message.author.id, 30)
        await message.channel.send(f"🎉 Solved! The word was **{game['word']}**. +30 coins for {message.author.mention}.")
    elif failed:
        word = game["word"]
        del active_hangman_games[message.channel.id]
        await message.channel.send(f"💀 Out of guesses! The word was **{word}**.")
    else:
        await message.channel.send(hangman_display(game))


# (hooked in near the bottom of this file, chained onto the existing on_message handler)


# ---------------------------------------------------------------- hook into on_message safely
# This bot's client class is a plain discord.Client (not commands.Bot), which has no
# add_listener(). Instead, chain these three onto whatever on_message is already set to
# (the relay/TTS/automod handler from part2.py) so nothing gets replaced or skipped.
_prior_on_message = client.on_message


async def _part3_on_message(message: discord.Message):
    await _prior_on_message(message)
    await award_xp_on_message(message)
    await guess_number_listener(message)
    await hangman_listener(message)


client.on_message = _part3_on_message


# ================================================================== recurring custom message ("autopost")
AUTOPOST_FILE = "autopost.json"
autopost_config: dict = load_json_file(AUTOPOST_FILE, {})  # "guild_id" -> {channel_id, message, interval_minutes,
                                                            #  title, color, banner_url, thumbnail_url, use_embed,
                                                            #  last_sent (iso str or None)}


async def send_autopost(guild: discord.Guild, config: dict) -> None:
    channel = guild.get_channel(config["channel_id"])
    if not channel:
        return
    text = config["message"].replace("{server}", guild.name).replace("{membercount}", str(guild.member_count))

    try:
        if config.get("use_embed", True):
            color = parse_hex_color(config["color"]) if config.get("color") else discord.Color.blurple()
            embed = discord.Embed(title=config.get("title") or None, description=text, color=color or discord.Color.blurple())
            if config.get("banner_url"):
                embed.set_image(url=config["banner_url"])
            if config.get("thumbnail_url"):
                embed.set_thumbnail(url=config["thumbnail_url"])
            await channel.send(embed=embed)
        else:
            await channel.send(text)
    except discord.HTTPException:
        return

    config["last_sent"] = discord.utils.utcnow().isoformat()
    save_json_file(AUTOPOST_FILE, autopost_config)


async def autopost_loop():
    while True:
        await asyncio.sleep(60)
        now = discord.utils.utcnow()
        for guild_id_str, config in list(autopost_config.items()):
            try:
                guild = client.get_guild(int(guild_id_str))
            except ValueError:
                continue
            if not guild:
                continue
            last_sent = config.get("last_sent")
            due = True
            if last_sent:
                elapsed_minutes = (now - datetime.datetime.fromisoformat(last_sent)).total_seconds() / 60
                due = elapsed_minutes >= config["interval_minutes"]
            if due:
                await send_autopost(guild, config)


autopost_group = app_commands.Group(name="autopost", description="A fully customisable recurring message", guild_only=True)


@autopost_group.command(name="set", description="Set up (or update) a recurring custom message")
@app_commands.describe(
    channel="Where to post it",
    message="Use {server} and {membercount} as placeholders",
    interval_minutes="How often to post, in minutes (minimum 5)",
    title="Embed title (ignored if use_embed is off)",
    color="Hex color for the embed, e.g. #57F287",
    banner_url="A wide image shown at the bottom",
    thumbnail_url="A small corner image",
    use_embed="Post as a styled embed instead of plain text (default: yes)",
)
@app_commands.checks.has_permissions(administrator=True)
async def autopost_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message: str,
    interval_minutes: app_commands.Range[int, 5, 525600],
    title: Optional[str] = None,
    color: Optional[str] = None,
    banner_url: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    use_embed: bool = True,
):
    if color and not parse_hex_color(color):
        await interaction.response.send_message("That doesn't look like a valid hex color. Try `#57F287`.", ephemeral=True)
        return
    for label, url in (("banner_url", banner_url), ("thumbnail_url", thumbnail_url)):
        if url and not url.startswith(("http://", "https://")):
            await interaction.response.send_message(f"`{label}` must be a link starting with http(s)://", ephemeral=True)
            return

    autopost_config[str(interaction.guild_id)] = {
        "channel_id": channel.id,
        "message": message,
        "interval_minutes": interval_minutes,
        "title": title,
        "color": color,
        "banner_url": banner_url,
        "thumbnail_url": thumbnail_url,
        "use_embed": use_embed,
        "last_sent": None,  # fires once right away, then every interval after
    }
    save_json_file(AUTOPOST_FILE, autopost_config)
    await interaction.response.send_message(
        f"Set! I'll post that in {channel.mention} every {interval_minutes} minute(s), starting now.", ephemeral=True
    )


@autopost_set.error
async def autopost_set_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You need Administrator to do that.", ephemeral=True)
    else:
        print(f"/autopost set error: {error}")


@autopost_group.command(name="off", description="Turn off the recurring message")
@app_commands.checks.has_permissions(administrator=True)
async def autopost_off(interaction: discord.Interaction):
    had = autopost_config.pop(str(interaction.guild_id), None)
    save_json_file(AUTOPOST_FILE, autopost_config)
    await interaction.response.send_message("Turned off." if had else "It wasn't set up.", ephemeral=True)


@autopost_group.command(name="now", description="Send the recurring message immediately (doesn't reset its timer)")
@app_commands.checks.has_permissions(administrator=True)
async def autopost_now(interaction: discord.Interaction):
    config = autopost_config.get(str(interaction.guild_id))
    if not config:
        await interaction.response.send_message("Nothing is set up — use /autopost set first.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await send_autopost(interaction.guild, config)
    await interaction.followup.send("Sent.", ephemeral=True)


client.tree.add_command(autopost_group)

_prior_on_ready = client.on_ready


async def _part3_on_ready():
    await _prior_on_ready()
    client.loop.create_task(autopost_loop())


client.on_ready = _part3_on_ready


# ================================================================== join ping (auto-deletes after 30s)
JOINPING_FILE = "joinping.json"
joinping_config: dict = load_json_file(JOINPING_FILE, {})  # "guild_id" -> {"channel_id", "message"}


@client.tree.command(name="joinping", description="Ping new members in a channel; the ping deletes itself after 30s")
@app_commands.describe(channel="Where to post the ping", message="Use {user} and {server} as placeholders")
@app_commands.checks.has_permissions(administrator=True)
async def joinping_set(interaction: discord.Interaction, channel: discord.TextChannel, message: str = "{user} joined {server}!"):
    joinping_config[str(interaction.guild_id)] = {"channel_id": channel.id, "message": message}
    save_json_file(JOINPING_FILE, joinping_config)
    await interaction.response.send_message(f"Join pings will post in {channel.mention} and self-delete after 30s.", ephemeral=True)


@joinping_set.error
async def joinping_set_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You need Administrator to do that.", ephemeral=True)
    else:
        print(f"/joinping error: {error}")


@client.tree.command(name="joinpingoff", description="Turn off join pings")
@app_commands.checks.has_permissions(administrator=True)
async def joinping_off(interaction: discord.Interaction):
    had = joinping_config.pop(str(interaction.guild_id), None)
    save_json_file(JOINPING_FILE, joinping_config)
    await interaction.response.send_message("Turned off." if had else "It wasn't set up.", ephemeral=True)


@joinping_off.error
async def joinping_off_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You need Administrator to do that.", ephemeral=True)
    else:
        print(f"/joinpingoff error: {error}")


_prior_on_member_join = client.on_member_join


async def _part3_on_member_join(member: discord.Member):
    await _prior_on_member_join(member)
    config = joinping_config.get(str(member.guild.id))
    if not config:
        return
    channel = member.guild.get_channel(config["channel_id"])
    if not channel:
        return
    text = config["message"].replace("{user}", member.mention).replace("{server}", member.guild.name)
    try:
        await channel.send(text, delete_after=30)
    except discord.HTTPException:
        pass


client.on_member_join = _part3_on_member_join


# ---------------------------------------------------------------- tic-tac-toe
def tictactoe_winner(board: list[str]) -> Optional[str]:
    lines = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


class TicTacToeView(discord.ui.View):
    def __init__(self, player_x: discord.abc.User, player_o: Optional[discord.abc.User]):
        super().__init__(timeout=300)
        self.board = [""] * 9
        self.player_x = player_x
        self.player_o = player_o  # None means "play against the bot"
        self.turn = "X"
        for i in range(9):
            self.add_item(self._make_button(i))

    def _make_button(self, index: int) -> discord.ui.Button:
        button = discord.ui.Button(label="\u200b", style=discord.ButtonStyle.secondary, row=index // 3)
        button.callback = self._make_callback(index, button)
        return button

    def _make_callback(self, index: int, button: discord.ui.Button):
        async def callback(interaction: discord.Interaction):
            current_player = self.player_x if self.turn == "X" else self.player_o
            is_bot_turn = current_player is None
            if not is_bot_turn and interaction.user.id != current_player.id:
                await interaction.response.send_message("It's not your turn.", ephemeral=True)
                return
            if self.board[index]:
                await interaction.response.send_message("That square's taken.", ephemeral=True)
                return

            self._play(index, button)
            await interaction.response.edit_message(content=self._status_text(), view=self)

            if self.player_o is None and self.turn == "O" and not self._game_over():
                bot_index = self._pick_bot_move()
                if bot_index is not None:
                    self._play(bot_index, self.children[bot_index])
                await interaction.edit_original_response(content=self._status_text(), view=self)

        return callback

    def _play(self, index: int, button: discord.ui.Button):
        self.board[index] = self.turn
        button.label = self.turn
        button.style = discord.ButtonStyle.danger if self.turn == "X" else discord.ButtonStyle.primary
        button.disabled = True
        self.turn = "O" if self.turn == "X" else "X"
        if self._game_over():
            for child in self.children:
                child.disabled = True

    def _game_over(self) -> bool:
        return tictactoe_winner(self.board) is not None or all(self.board)

    def _pick_bot_move(self) -> Optional[int]:
        empties = [i for i, v in enumerate(self.board) if not v]
        if not empties:
            return None
        # Try to win, then block, else random.
        for mark in ("O", "X"):
            for i in empties:
                trial = self.board.copy()
                trial[i] = mark
                if tictactoe_winner(trial) == mark:
                    return i
        return random.choice(empties)

    def _status_text(self) -> str:
        winner = tictactoe_winner(self.board)
        if winner:
            who = self.player_x if winner == "X" else self.player_o
            name = "the bot" if who is None else who.mention
            return f"🎉 **{winner}** wins — {name}!"
        if all(self.board):
            return "It's a draw!"
        next_player = self.player_x if self.turn == "X" else self.player_o
        name = "the bot" if next_player is None else next_player.mention
        return f"Tic-tac-toe — {name}'s turn ({self.turn})"


@client.tree.command(name="tictactoe", description="Play tic-tac-toe against another member, or the bot")
@app_commands.describe(opponent="Who to play against (leave empty to play the bot)")
async def tictactoe_cmd(interaction: discord.Interaction, opponent: Optional[discord.Member] = None):
    if opponent and opponent.bot:
        await interaction.response.send_message("You can't challenge a bot account directly — just leave `opponent` empty to play me.", ephemeral=True)
        return
    if opponent and opponent.id == interaction.user.id:
        await interaction.response.send_message("You can't play yourself.", ephemeral=True)
        return
    view = TicTacToeView(interaction.user, opponent)
    await interaction.response.send_message(view._status_text(), view=view)
