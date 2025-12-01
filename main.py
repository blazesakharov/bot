import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import asyncio

TOKEN = os.getenv("TOKEN")

CONFIG_FILE = "config.json"
POSTS_FILE = "posts_map.json"

SELLER_ROLE_NAME = "Seller"
BUYER_ROLE_NAME = "Buyer"
TICKET_CATEGORY_NAME = "Tickets"

# ================= LOAD/SAVE CONFIG =================


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"announce_channel": {}}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)


config = load_config()

# ================= LOAD/SAVE POSTS =================


def load_posts():
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_posts(d):
    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=4)


posts_map = load_posts()

# ================= BOT =================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot is ready as {bot.user}")


# ================= UTILS =================


async def get_or_create_role(guild, role_name):
    role = discord.utils.get(guild.roles, name=role_name)
    if role:
        return role
    return await guild.create_role(name=role_name, mentionable=False)


def make_ticket_name(member):
    return f"ticket-{member.name.replace(' ', '-')}"


# ================= TICKET BUTTONS =================


class OpenTicketView(discord.ui.View):

    def __init__(self, announce_message_id):
        super().__init__(timeout=None)
        self.announce_message_id = str(announce_message_id)

    @discord.ui.button(label="Открыть тикет",
                       style=discord.ButtonStyle.primary)
    async def open_ticket(self, interaction, button):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        user = interaction.user

        info = posts_map.get(str(interaction.message.id))
        if not info:
            return await interaction.followup.send(
                "Ошибка: продавец не найден!", ephemeral=True)

        seller = guild.get_member(info["seller_id"])

        overwrites = {
            guild.default_role:
            discord.PermissionOverwrite(view_channel=False),
            guild.me:
            discord.PermissionOverwrite(view_channel=True, send_messages=True),
            seller:
            discord.PermissionOverwrite(view_channel=True, send_messages=True),
            user:
            discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        category = discord.utils.get(guild.categories,
                                     name=TICKET_CATEGORY_NAME)
        channel_name = make_ticket_name(user)

        ticket = await guild.create_text_channel(channel_name,
                                                 overwrites=overwrites,
                                                 category=category)

        class CloseView(discord.ui.View):

            @discord.ui.button(label="Закрыть тикет",
                               style=discord.ButtonStyle.danger)
            async def close(self, inner, _button):
                if inner.user.id == seller.id or inner.user.guild_permissions.administrator:
                    await inner.response.send_message("Закрываю...",
                                                      ephemeral=True)
                    await asyncio.sleep(1)
                    await ticket.delete()
                else:
                    await inner.response.send_message("Нет прав.",
                                                      ephemeral=True)

        await ticket.send(f"{seller.mention} {user.mention} Тикет открыт!",
                          view=CloseView())
        await interaction.followup.send(f"Тикет создан: {ticket.mention}",
                                        ephemeral=True)


# ================= SLASH COMMANDS =================

# ---- 1. Назначение канала (только админы) ----
import traceback
import datetime


# --- Надёжная версия /set_announce_channel с логированием ошибок ---
@bot.tree.command(
    name="set_announce_channel",
    description="Назначить канал для объявлений (только админы).")
@app_commands.describe(channel="Канал для публикации объявлений")
async def set_announce_channel(interaction: discord.Interaction,
                               channel: discord.TextChannel):
    # Быстрая проверка прав — ответим сразу, если нет прав
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Только администраторы могут назначать канал.", ephemeral=True)
        return

    # Попробуем обработать и залогировать любые ошибки — чтобы не было "Приложение не отвечает"
    try:
        # Подготовка и запись
        guild_id = str(interaction.guild.id)
        if "announce_channel" not in config:
            config["announce_channel"] = {}
        config["announce_channel"][guild_id] = channel.id
        save_config(config)

        # Успешный ответ
        await interaction.response.send_message(
            f"Канал объявлений установлен: {channel.mention}", ephemeral=True)

    except Exception as e:
        # Логируем в консоль + в файл traceback для диагностики
        tb = traceback.format_exc()
        now = datetime.datetime.utcnow().isoformat()
        log_line = f"\n[{now}] Error in /set_announce_channel for guild {getattr(interaction.guild, 'id', 'unknown')}:\n{tb}\n"
        print(log_line)
        try:
            with open("error.log", "a", encoding="utf-8") as lf:
                lf.write(log_line)
        except:
            pass

        # Ответ пользователю — информативный, но короткий
        try:
            await interaction.response.send_message(
                "Произошла ошибка при назначении канала. Я записал детали в лог (error.log). Обратитесь к администратору или пришлите лог мне.",
                ephemeral=True)
        except:
            # Если даже response.send_message упал (маловероятно) — попробуем followup
            try:
                await interaction.followup.send(
                    "Ошибка при обработке команды (см. logs).", ephemeral=True)
            except:
                # ничего не осталось — напечатаем в консоль
                print(
                    "Failed to send error message to user for /set_announce_channel."
                )


# ---- 2. Создать объявление (любой может) ----
@bot.tree.command(name="post",
                  description="Создать объявление (купить или продать)")
@app_commands.describe(type="Тип объявления: sell — продать, buy — купить",
                       title="Заголовок",
                       description="Описание",
                       price="Цена",
                       image="Картинка (обязательно только для продажи)")
async def post(interaction: discord.Interaction,
               type: str,
               title: str,
               description: str,
               price: str,
               image: discord.Attachment = None):

    type = type.lower()

    # Проверка типа
    if type not in ["sell", "buy"]:
        return await interaction.response.send_message(
            "Тип должен быть **sell** (продать) или **buy** (купить).",
            ephemeral=True)

    guild_id = str(interaction.guild.id)
    announce_id = config["announce_channel"].get(guild_id)

    if not announce_id:
        return await interaction.response.send_message(
            "Канал объявлений не назначен! Используйте /set_announce_channel",
            ephemeral=True)

    announce_channel = interaction.guild.get_channel(announce_id)

    # === Проверка картинки ===
    if type == "sell" and image is None:
        return await interaction.response.send_message(
            "Для объявления **продажи** картинка обязательна!", ephemeral=True)

    # === Создание EMBED ===
    embed = discord.Embed(
        title=("🔴 ПРОДАЖА: " + title) if type == "sell" else
        ("🟢 ПОКУПКА: " + title),
        description=description,
        color=discord.Color.red() if type == "sell" else discord.Color.green())

    embed.add_field(name="Цена", value=price, inline=False)
    embed.set_author(name=str(interaction.user))

    if image:
        embed.set_image(url=image.url)

    view = OpenTicketView(0)
    sent = await announce_channel.send(embed=embed, view=view)

    posts_map[str(sent.id)] = {"seller_id": interaction.user.id, "type": type}
    save_posts(posts_map)

    await sent.edit(view=OpenTicketView(sent.id))

    await interaction.response.send_message("Объявление создано!",
                                            ephemeral=True)


# ---- 3. Создать роль Seller ----
@bot.tree.command(name="seller_role_create",
                  description="Создать роль продавца (админы)")
async def seller_role_create(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Нет прав.",
                                                       ephemeral=True)

    guild = interaction.guild
    role = discord.utils.get(guild.roles, name=SELLER_ROLE_NAME)

    if role:
        return await interaction.response.send_message("Роль уже существует.",
                                                       ephemeral=True)

    await guild.create_role(name=SELLER_ROLE_NAME)
    await interaction.response.send_message("Роль Seller создана!",
                                            ephemeral=True)


# ---- 4. Создать роль Buyer ----
@bot.tree.command(name="buyer_role_create",
                  description="Создать роль покупателя (админы)")
async def buyer_role_create(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Нет прав.",
                                                       ephemeral=True)

    guild = interaction.guild
    role = discord.utils.get(guild.roles, name=BUYER_ROLE_NAME)

    if role:
        return await interaction.response.send_message("Роль уже существует.",
                                                       ephemeral=True)

    await guild.create_role(name=BUYER_ROLE_NAME)
    await interaction.response.send_message("Роль Buyer создана!",
                                            ephemeral=True)


# ================= RUN =================

bot.run(TOKEN)
