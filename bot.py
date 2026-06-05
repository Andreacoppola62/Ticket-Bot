from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


BOT_NAME: Final[str] = "Ticket Bot"
TOKEN_ENV_NAME: Final[str] = "DISCORD_TOKEN"
GUILD_ID_ENV_NAME: Final[str] = "DISCORD_GUILD_ID"

TICKET_CATEGORY_NAME: Final[str] = "Ticket"
STAFF_ROLE_NAME: Final[str] = "staffer"
PANEL_CHANNEL_NAME: Final[str] = "ticket-panel"

OPEN_TICKET_BUTTON_ID: Final[str] = "ticket_bot:open_ticket"
CLAIM_TICKET_BUTTON_ID: Final[str] = "ticket_bot:claim_ticket"
CLOSE_TICKET_BUTTON_ID: Final[str] = "ticket_bot:close_ticket"

COLOR_PANEL: Final[int] = 0x2563EB
COLOR_SUCCESS: Final[int] = 0x16A34A
COLOR_WARNING: Final[int] = 0xF59E0B
COLOR_ERROR: Final[int] = 0xDC2626
COLOR_NEUTRAL: Final[int] = 0x334155

LOGGER = logging.getLogger(BOT_NAME)


@dataclass(frozen=True)
class TicketField:
    label: str
    placeholder: str
    style: discord.TextStyle = discord.TextStyle.short
    max_length: int = 400


@dataclass(frozen=True)
class TicketConfig:
    key: str
    label: str
    description: str
    color: int
    fields: tuple[TicketField, ...]


TICKET_TYPES: Final[dict[str, TicketConfig]] = {
    "assistenza_generica": TicketConfig(
        key="assistenza_generica",
        label="Assistenza Generica",
        description="Problemi generali, dubbi o richieste di supporto.",
        color=0x2563EB,
        fields=(
            TicketField("Nome Discord", "Esempio: Mario#0001"),
            TicketField(
                "Descrizione Problema",
                "Descrivi il problema in modo chiaro.",
                style=discord.TextStyle.paragraph,
                max_length=1500,
            ),
        ),
    ),
    "partnership": TicketConfig(
        key="partnership",
        label="Partnership",
        description="Richiedi o proponi una partnership con il server.",
        color=0x0F766E,
        fields=(
            TicketField("Nome Server", "Inserisci il nome del server."),
            TicketField("Quanti membri ha?", "Esempio: 1500 membri"),
            TicketField(
                "Chi sono i creatori?",
                "Elenca i creatori o responsabili del progetto.",
                style=discord.TextStyle.paragraph,
                max_length=900,
            ),
        ),
    ),
    "assistenza_acquisti": TicketConfig(
        key="assistenza_acquisti",
        label="Assistenza Acquisti",
        description="Supporto per ordini, acquisti o pagamenti.",
        color=0x7C3AED,
        fields=(
            TicketField("Nome Discord", "Esempio: Mario#0001"),
            TicketField("Cosa hai acquistato?", "Descrivi il prodotto o servizio acquistato."),
            TicketField("Quando hai effettuato l'ordine?", "Esempio: 26/05/2026 alle 18:30"),
        ),
    ),
    "candidatura_staffer": TicketConfig(
        key="candidatura_staffer",
        label="Candidatura Staffer",
        description="Invia la tua candidatura per entrare nello staff.",
        color=0xEA580C,
        fields=(
            TicketField("Nome Discord", "Esempio: Mario#0001"),
            TicketField("Quanto tempo dedicherai al server?", "Esempio: 2 ore al giorno"),
            TicketField(
                "Perché vuoi diventare Staffer?",
                "Spiega motivazione, esperienza e disponibilità.",
                style=discord.TextStyle.paragraph,
                max_length=1500,
            ),
        ),
    ),
}


def make_embed(title: str, description: str, color: int) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_image(url="[https://ibb.co/gxsSsTm][img]https://i.ibb.co/Tsz4z8Y/Logo-Coppola.png[/img][/url]")
    embed.set_footer(text=BOT_NAME)
    return embed


def success_embed(title: str, description: str) -> discord.Embed:
    return make_embed(title, description, COLOR_SUCCESS)


def warning_embed(title: str, description: str) -> discord.Embed:
    return make_embed(title, description, COLOR_WARNING)


def error_embed(title: str, description: str) -> discord.Embed:
    return make_embed(title, description, COLOR_ERROR)


def truncate_value(value: str, limit: int = 1024) -> str:
    value = value.strip()
    if not value:
        return "Non specificato"
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def get_staff_role(guild: discord.Guild) -> discord.Role | None:
    return discord.utils.find(
        lambda role: role.name.lower() == STAFF_ROLE_NAME.lower(),
        guild.roles,
    )


def get_ticket_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    return discord.utils.find(
        lambda category: category.name.lower() == TICKET_CATEGORY_NAME.lower(),
        guild.categories,
    )


def member_has_staff_role(member: discord.Member, staff_role: discord.Role) -> bool:
    return staff_role in member.roles


def format_channel_name(member: discord.Member) -> str:
    raw_name = member.name or member.display_name or str(member.id)
    slug = re.sub(r"[^a-z0-9-]+", "-", raw_name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        slug = f"user-{member.id}"
    return f"ticket-{slug}"[:90]


def find_existing_ticket_channel(
    guild: discord.Guild,
    member_id: int,
) -> discord.TextChannel | None:
    category = get_ticket_category(guild)
    if category is None:
        return None

    marker = f"ticket_owner_id={member_id};"
    for channel in category.text_channels:
        if channel.topic and marker in channel.topic:
            return channel
    return None


def is_ticket_channel(channel: discord.abc.GuildChannel, category: discord.CategoryChannel) -> bool:
    return (
        isinstance(channel, discord.TextChannel)
        and channel.category_id == category.id
        and channel.topic is not None
        and "ticket_owner_id=" in channel.topic
    )


async def send_interaction_message(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> None:
    """Risponde una sola volta all'interazione, evitando errori di doppia risposta."""
    payload: dict[str, object] = {
        "content": content,
        "embed": embed,
        "view": view,
        "ephemeral": ephemeral,
        "allowed_mentions": allowed_mentions or discord.AllowedMentions.none(),
    }

    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def get_required_objects(
    interaction: discord.Interaction,
    *,
    require_staff: bool = False,
) -> tuple[discord.Guild, discord.CategoryChannel, discord.Role] | None:
    guild = interaction.guild
    if guild is None:
        await send_interaction_message(
            interaction,
            embed=error_embed(
                "Comando non disponibile",
                "Questo bot può essere usato solo dentro un server Discord.",
            ),
        )
        return None

    staff_role = get_staff_role(guild)
    if staff_role is None:
        await send_interaction_message(
            interaction,
            embed=error_embed(
                "Ruolo staffer mancante",
                f"Non trovo il ruolo `@{STAFF_ROLE_NAME}`. Crea il ruolo e riprova.",
            ),
        )
        return None

    if require_staff:
        if not isinstance(interaction.user, discord.Member) or not member_has_staff_role(
            interaction.user,
            staff_role,
        ):
            await send_interaction_message(
                interaction,
                embed=error_embed(
                    "Permesso negato",
                    f"Solo gli utenti con il ruolo {staff_role.mention} possono usare questa azione.",
                ),
            )
            return None

    ticket_category = get_ticket_category(guild)
    if ticket_category is None:
        await send_interaction_message(
            interaction,
            embed=error_embed(
                "Categoria Ticket mancante",
                f"Non trovo la categoria `{TICKET_CATEGORY_NAME}`. Creala manualmente e riprova.",
            ),
        )
        return None

    return guild, ticket_category, staff_role


def build_panel_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="Ticket Bot - Centro Assistenza",
        description=(
            "Apri un ticket selezionando la categoria più adatta alla tua richiesta. "
            "Un membro dello staff ti risponderà appena possibile."
        ),
        color=COLOR_PANEL,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Categorie disponibili",
        value=(
            "Assistenza Generica\n"
            "Partnership\n"
            "Assistenza Acquisti\n"
            "Candidatura Staffer"
        ),
        inline=False,
    )
    embed.add_field(
        name="Regole",
        value="Apri un solo ticket alla volta e descrivi la richiesta con informazioni chiare.",
        inline=False,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=BOT_NAME)
    return embed


def build_select_embed() -> discord.Embed:
    return make_embed(
        "Seleziona una categoria",
        "Scegli dal menu il tipo di ticket che vuoi aprire. Dopo la scelta comparirà un modulo dedicato.",
        COLOR_NEUTRAL,
    )


def build_ticket_embed(
    *,
    member: discord.Member,
    config: TicketConfig,
    answers: dict[str, str],
    opened_at: datetime,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Ticket - {config.label}",
        description="Nuova richiesta aperta. Lo staff può reclamare o chiudere il ticket dai pulsanti qui sotto.",
        color=config.color,
        timestamp=opened_at,
    )
    embed.add_field(
        name="Utente",
        value=f"{member.mention}\n`{member}` (`{member.id}`)",
        inline=False,
    )
    embed.add_field(name="Tipo ticket", value=config.label, inline=True)
    embed.add_field(
        name="Data apertura",
        value=f"<t:{int(opened_at.timestamp())}:F>",
        inline=True,
    )
    for question, answer in answers.items():
        embed.add_field(name=question, value=truncate_value(answer), inline=False)

    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=BOT_NAME)
    return embed


def build_countdown_embed(seconds: int) -> discord.Embed:
    return make_embed(
        "Chiusura ticket",
        f"Il ticket verrà chiuso tra {seconds} secondi.",
        COLOR_WARNING,
    )


async def create_or_get_panel_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    staff_role: discord.Role,
) -> discord.TextChannel:
    existing = discord.utils.get(category.text_channels, name=PANEL_CHANNEL_NAME)
    if existing is not None:
        return existing

    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        ),
        staff_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        ),
    }

    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            read_message_history=True,
            embed_links=True,
        )

    return await guild.create_text_channel(
        name=PANEL_CHANNEL_NAME,
        category=category,
        overwrites=overwrites,
        topic=f"Pannello ufficiale di {BOT_NAME}.",
        reason=f"Creazione pannello ticket di {BOT_NAME}",
    )


async def create_ticket_channel(
    *,
    guild: discord.Guild,
    category: discord.CategoryChannel,
    staff_role: discord.Role,
    member: discord.Member,
    config: TicketConfig,
    answers: dict[str, str],
) -> discord.TextChannel:
    opened_at = discord.utils.utcnow()
    topic = (
        f"ticket_owner_id={member.id}; "
        f"ticket_type={config.key}; "
        f"opened_at={opened_at.isoformat()}"
    )

    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            read_message_history=True,
        ),
        staff_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            read_message_history=True,
            manage_messages=True,
        ),
    }

    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            read_message_history=True,
            embed_links=True,
        )

    channel = await guild.create_text_channel(
        name=format_channel_name(member),
        category=category,
        overwrites=overwrites,
        topic=topic[:1024],
        reason=f"Ticket aperto da {member} ({member.id})",
    )

    embed = build_ticket_embed(
        member=member,
        config=config,
        answers=answers,
        opened_at=opened_at,
    )
    await channel.send(
        content=f"{member.mention} {staff_role.mention}",
        embed=embed,
        view=TicketActionsView(),
        allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
    )
    return channel


class TicketBot(commands.Bot):
    async def setup_hook(self) -> None:
        self.add_view(TicketPanelView())
        self.add_view(TicketActionsView())

        guild_id = os.getenv(GUILD_ID_ENV_NAME)
        if guild_id:
            try:
                guild_object = discord.Object(id=int(guild_id))
            except ValueError:
                LOGGER.warning("DISCORD_GUILD_ID non valido: sincronizzo i comandi globalmente.")
                synced = await self.tree.sync()
            else:
                self.tree.copy_global_to(guild=guild_object)
                synced = await self.tree.sync(guild=guild_object)
                LOGGER.info("Slash command sincronizzati nel server %s: %s", guild_id, len(synced))
                return
        else:
            synced = await self.tree.sync()

        LOGGER.info("Slash command globali sincronizzati: %s", len(synced))


class BaseSafeView(discord.ui.View):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[discord.ui.View],
    ) -> None:
        LOGGER.error("Errore nella view %s", item, exc_info=(type(error), error, error.__traceback__))
        await send_interaction_message(
            interaction,
            embed=error_embed(
                "Errore imprevisto",
                "Si è verificato un problema durante l'azione. Riprova tra poco.",
            ),
        )


class TicketPanelView(BaseSafeView):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Apri ticket",
        style=discord.ButtonStyle.primary,
        custom_id=OPEN_TICKET_BUTTON_ID,
    )
    async def open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        required = await get_required_objects(interaction)
        if required is None:
            return

        guild, _category, _staff_role = required
        if not isinstance(interaction.user, discord.Member):
            await send_interaction_message(
                interaction,
                embed=error_embed(
                    "Utente non valido",
                    "Non riesco a leggere i dati del tuo profilo in questo server.",
                ),
            )
            return

        existing_ticket = find_existing_ticket_channel(guild, interaction.user.id)
        if existing_ticket is not None:
            await send_interaction_message(
                interaction,
                embed=warning_embed(
                    "Ticket già aperto",
                    f"Hai già un ticket attivo: {existing_ticket.mention}. Chiudilo prima di aprirne un altro.",
                ),
            )
            return

        await send_interaction_message(
            interaction,
            embed=build_select_embed(),
            view=TicketCategorySelectView(),
        )


class TicketCategorySelect(discord.ui.Select[discord.ui.View]):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label=config.label,
                description=config.description,
                value=config.key,
            )
            for config in TICKET_TYPES.values()
        ]
        super().__init__(
            placeholder="Scegli la categoria del ticket",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        required = await get_required_objects(interaction)
        if required is None:
            return

        guild, _category, _staff_role = required
        if not isinstance(interaction.user, discord.Member):
            await send_interaction_message(
                interaction,
                embed=error_embed(
                    "Utente non valido",
                    "Non riesco a leggere i dati del tuo profilo in questo server.",
                ),
            )
            return

        existing_ticket = find_existing_ticket_channel(guild, interaction.user.id)
        if existing_ticket is not None:
            await send_interaction_message(
                interaction,
                embed=warning_embed(
                    "Ticket già aperto",
                    f"Hai già un ticket attivo: {existing_ticket.mention}. Chiudilo prima di aprirne un altro.",
                ),
            )
            return

        config = TICKET_TYPES[self.values[0]]
        await interaction.response.send_modal(TicketModal(config))


class TicketCategorySelectView(BaseSafeView):
    def __init__(self) -> None:
        super().__init__(timeout=180)
        self.add_item(TicketCategorySelect())


class TicketModal(discord.ui.Modal):
    def __init__(self, config: TicketConfig) -> None:
        super().__init__(title=f"Ticket - {config.label}", timeout=300)
        self.config = config
        self.inputs: list[discord.ui.TextInput[discord.ui.Modal]] = []

        for field in config.fields:
            text_input = discord.ui.TextInput(
                label=field.label,
                placeholder=field.placeholder,
                style=field.style,
                required=True,
                max_length=field.max_length,
            )
            self.add_item(text_input)
            self.inputs.append(text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        required = await get_required_objects(interaction)
        if required is None:
            return

        guild, category, staff_role = required
        if not isinstance(interaction.user, discord.Member):
            await send_interaction_message(
                interaction,
                embed=error_embed(
                    "Utente non valido",
                    "Non riesco a leggere i dati del tuo profilo in questo server.",
                ),
            )
            return

        existing_ticket = find_existing_ticket_channel(guild, interaction.user.id)
        if existing_ticket is not None:
            await send_interaction_message(
                interaction,
                embed=warning_embed(
                    "Ticket già aperto",
                    f"Hai già un ticket attivo: {existing_ticket.mention}. Chiudilo prima di aprirne un altro.",
                ),
            )
            return

        answers = {text_input.label: str(text_input.value) for text_input in self.inputs}
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            channel = await create_ticket_channel(
                guild=guild,
                category=category,
                staff_role=staff_role,
                member=interaction.user,
                config=self.config,
                answers=answers,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed(
                    "Permessi insufficienti",
                    "Non posso creare il canale ticket. Verifica che il bot possa gestire canali e inviare embed.",
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            LOGGER.error(
                "Errore HTTP durante la creazione ticket",
                exc_info=(type(error), error, error.__traceback__),
            )
            await interaction.followup.send(
                embed=error_embed(
                    "Creazione fallita",
                    "Discord non ha accettato la creazione del canale. Riprova tra poco.",
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=success_embed(
                "Ticket aperto",
                f"Il tuo ticket è stato creato correttamente: {channel.mention}.",
            ),
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.error("Errore nel modal ticket", exc_info=(type(error), error, error.__traceback__))
        await send_interaction_message(
            interaction,
            embed=error_embed(
                "Errore imprevisto",
                "Non sono riuscito a completare l'apertura del ticket. Riprova tra poco.",
            ),
        )


class TicketActionsView(BaseSafeView):
    def __init__(self, *, claimed: bool = False) -> None:
        super().__init__(timeout=None)
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == CLAIM_TICKET_BUTTON_ID:
                item.disabled = claimed
                item.label = "Ticket reclamato" if claimed else "Reclama Ticket"
                item.style = discord.ButtonStyle.secondary if claimed else discord.ButtonStyle.success

    @discord.ui.button(
        label="Reclama Ticket",
        style=discord.ButtonStyle.success,
        custom_id=CLAIM_TICKET_BUTTON_ID,
    )
    async def claim_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        required = await get_required_objects(interaction, require_staff=True)
        if required is None:
            return

        _guild, category, _staff_role = required
        if interaction.channel is None or not is_ticket_channel(interaction.channel, category):
            await send_interaction_message(
                interaction,
                embed=error_embed(
                    "Canale non valido",
                    "Questa azione può essere usata solo dentro un canale ticket.",
                ),
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await send_interaction_message(
                interaction,
                embed=error_embed(
                    "Utente non valido",
                    "Non riesco a leggere i dati del tuo profilo in questo server.",
                ),
            )
            return

        if interaction.message is not None:
            await interaction.message.edit(view=TicketActionsView(claimed=True))

        await send_interaction_message(
            interaction,
            embed=success_embed(
                "Ticket reclamato",
                f"{interaction.user.mention} ha preso in carico questo ticket.",
            ),
            ephemeral=False,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

    @discord.ui.button(
        label="Chiudi Ticket",
        style=discord.ButtonStyle.danger,
        custom_id=CLOSE_TICKET_BUTTON_ID,
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        required = await get_required_objects(interaction, require_staff=True)
        if required is None:
            return

        _guild, category, _staff_role = required
        if interaction.channel is None or not is_ticket_channel(interaction.channel, category):
            await send_interaction_message(
                interaction,
                embed=error_embed(
                    "Canale non valido",
                    "Questa azione può essere usata solo dentro un canale ticket.",
                ),
            )
            return

        channel = interaction.channel
        assert isinstance(channel, discord.TextChannel)

        await interaction.response.send_message(
            embed=build_countdown_embed(5),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        countdown_message = await interaction.original_response()

        for seconds in range(4, 0, -1):
            await asyncio.sleep(1)
            try:
                await countdown_message.edit(embed=build_countdown_embed(seconds))
            except discord.HTTPException:
                break

        await asyncio.sleep(1)
        try:
            await channel.delete(reason=f"Ticket chiuso da {interaction.user}")
        except discord.Forbidden:
            await send_interaction_message(
                interaction,
                embed=error_embed(
                    "Permessi insufficienti",
                    "Non posso eliminare questo canale. Verifica i permessi del bot.",
                ),
            )
        except discord.HTTPException as error:
            LOGGER.error(
                "Errore HTTP durante la chiusura ticket",
                exc_info=(type(error), error, error.__traceback__),
            )


intents = discord.Intents.default()
intents.guilds = True

bot = TicketBot(
    command_prefix=commands.when_mentioned_or("!"),
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none(),
)


@bot.event
async def on_ready() -> None:
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="i ticket del server",
        )
    )
    LOGGER.info("%s online come %s (ID: %s)", BOT_NAME, bot.user, bot.user.id if bot.user else "N/D")


@bot.tree.command(
    name="ticket-control-panel",
    description="Invia il pannello professionale per aprire ticket.",
)
@app_commands.guild_only()
async def ticket_control_panel(interaction: discord.Interaction) -> None:
    required = await get_required_objects(interaction, require_staff=True)
    if required is None:
        return

    guild, category, staff_role = required

    try:
        panel_channel = await create_or_get_panel_channel(guild, category, staff_role)
        await panel_channel.send(embed=build_panel_embed(guild), view=TicketPanelView())
    except discord.Forbidden:
        await send_interaction_message(
            interaction,
            embed=error_embed(
                "Permessi insufficienti",
                "Non posso creare o scrivere nel canale del pannello. Verifica i permessi del bot nella categoria Ticket.",
            ),
        )
        return
    except discord.HTTPException as error:
        LOGGER.error(
            "Errore HTTP durante l'invio del pannello",
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_interaction_message(
            interaction,
            embed=error_embed(
                "Invio pannello fallito",
                "Discord non ha accettato l'invio del pannello. Riprova tra poco.",
            ),
        )
        return

    await send_interaction_message(
        interaction,
        embed=success_embed(
            "Pannello inviato",
            f"Il pannello ticket è stato inviato in {panel_channel.mention}.",
        ),
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    original_error = getattr(error, "original", error)

    if isinstance(original_error, app_commands.NoPrivateMessage):
        embed = error_embed(
            "Comando non disponibile",
            "Questo comando può essere usato solo dentro un server Discord.",
        )
    elif isinstance(original_error, app_commands.BotMissingPermissions):
        embed = error_embed(
            "Permessi bot mancanti",
            "Mi mancano uno o più permessi necessari per completare questa azione.",
        )
    elif isinstance(original_error, app_commands.CommandOnCooldown):
        embed = warning_embed(
            "Rallenta un attimo",
            f"Potrai riusare questo comando tra {original_error.retry_after:.1f} secondi.",
        )
    else:
        LOGGER.error(
            "Errore slash command",
            exc_info=(type(original_error), original_error, original_error.__traceback__),
        )
        embed = error_embed(
            "Errore imprevisto",
            "Si è verificato un problema durante l'esecuzione del comando.",
        )

    await send_interaction_message(interaction, embed=embed)


@bot.event
async def on_command_error(
    ctx: commands.Context[commands.Bot],
    error: commands.CommandError,
) -> None:
    LOGGER.warning("Errore comando prefix ignorato: %s", error)


def load_bot_token() -> str:
    load_dotenv()
    token = os.getenv(TOKEN_ENV_NAME)
    if not token or token == "INSERISCI_IL_TOKEN_DEL_BOT":
        raise RuntimeError(
            f"Configura {TOKEN_ENV_NAME} nel file .env prima di avviare il bot."
        )
    return token


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    )
    bot.run(load_bot_token(), log_handler=None)


if __name__ == "__main__":
    main()


# ISTRUZIONI RAPIDE
# 1. Crea una categoria Discord chiamata Ticket.
# 2. Crea un ruolo Discord chiamato staffer e assegnalo allo staff.
# 3. Inserisci il token del bot nel file .env.
# 4. Installa le dipendenze con: pip install -r requirements.txt
# 5. Avvia il bot con: python bot.py
