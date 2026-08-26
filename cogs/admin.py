"""Commandes administrateur pour la gestion d'EcoBot."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import asyncpg
import discord
from discord.ext import commands

from config import Emojis, PET_DEFINITIONS, PET_EMOJIS, QUERY_TIMEOUT_SECONDS, scale_pet_value
from database.db import DatabaseError
from utils import embeds

logger = logging.getLogger(__name__)


def _build_pet_lookup(definitions: Iterable) -> dict[str, object]:
    return {getattr(pet, "name", "").lower(): pet for pet in definitions}


class AdminGrantPetModal(discord.ui.Modal):
    def __init__(self, cog: "Admin") -> None:
        super().__init__(title="🎛️ Panneau admin — Give un pet")
        self.cog = cog

        self.target_user = discord.ui.TextInput(
            label="Utilisateur",
            placeholder="ID ou mention du joueur",
            min_length=1,
            max_length=50,
        )
        self.pet_name = discord.ui.TextInput(
            label="Nom du pet",
            placeholder="Exemple : Titanic Colt",
            min_length=1,
            max_length=50,
        )
        self.flags = discord.ui.TextInput(
            label="Options (facultatif)",
            placeholder="gold, rainbow, galaxy, shiny",
            required=False,
            max_length=100,
        )

        self.add_item(self.target_user)
        self.add_item(self.pet_name)
        self.add_item(self.flags)

    @staticmethod
    def _parse_user_id(value: str) -> int | None:
        digits = re.sub(r"\D", "", value)
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True, thinking=True)

        user_id = self._parse_user_id(self.target_user.value)
        if user_id is None:
            await interaction.followup.send(
                embed=embeds.error_embed("Impossible de lire l'utilisateur visé."),
                ephemeral=True,
            )
            return

        pet_identifier = self.pet_name.value.strip().lower()
        definition = self.cog.pet_lookup.get(pet_identifier)
        if definition is None:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Pet introuvable. Vérifie l'orthographe exacte.", title="Pet introuvable"
                ),
                ephemeral=True,
            )
            return

        normalized_flags = {flag.strip().lower() for flag in self.flags.value.split(",") if flag.strip()}
        is_galaxy = "galaxy" in normalized_flags
        is_rainbow = "rainbow" in normalized_flags and not is_galaxy
        is_gold = "gold" in normalized_flags and not is_rainbow and not is_galaxy
        is_shiny = "shiny" in normalized_flags
        is_huge = bool(getattr(definition, "is_huge", False) or ("huge" in normalized_flags))

        pet_id = getattr(definition, "pet_id", None)
        if not pet_id:
            pet_id = await self.cog.database.get_pet_id_by_name(definition.name)
        if pet_id is None:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Impossible de retrouver l'identifiant interne de ce pet."
                ),
                ephemeral=True,
            )
            return

        try:
            pet_row = await self.cog.database.add_user_pet(
                user_id,
                int(pet_id),
                is_huge=is_huge,
                is_gold=is_gold,
                is_rainbow=is_rainbow,
                is_galaxy=is_galaxy,
                is_shiny=is_shiny,
            )
        except DatabaseError as exc:
            await interaction.followup.send(embed=embeds.error_embed(str(exc)), ephemeral=True)
            return

        emoji = PET_EMOJIS.get(definition.name, PET_EMOJIS.get("default", "🐾"))
        lines = [
            f"👤 Cible : <@{user_id}> (`{user_id}`)",
            f"🐾 Pet : {emoji} **{definition.name}** ({getattr(definition, 'rarity', 'Inconnu')})",
            f"✨ Variantes : "
            + ", ".join(
                [label for label, active in (
                    ("Huge/Titanic", is_huge),
                    ("Or", is_gold),
                    ("Rainbow", is_rainbow),
                    ("Galaxy", is_galaxy),
                    ("Shiny", is_shiny),
                )
                if active
                ]
                or ["Aucune"]
            ),
            f"🆔 ID inventaire : #{pet_row['id']}",
        ]

        await interaction.followup.send(
            embed=embeds.success_embed("\n".join(lines), title="Pet attribué avec succès"),
            ephemeral=True,
        )
        logger.info(
            "Admin grant_pet",
            extra={
                "admin_id": interaction.user.id,
                "target_id": user_id,
                "pet_name": definition.name,
            },
        )


class AdminPanelView(discord.ui.View):
    def __init__(self, cog: "Admin") -> None:
        super().__init__(timeout=600)
        self.cog = cog

    @discord.ui.button(label="Donner un pet", style=discord.ButtonStyle.success)
    async def open_grant_modal(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(AdminGrantPetModal(self.cog))


class Admin(commands.Cog):
    """Commandes réservées au propriétaire du bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.pet_lookup = _build_pet_lookup(PET_DEFINITIONS)

    @commands.command(name="adminpanel")
    @commands.is_owner()
    async def admin_panel(self, ctx: commands.Context) -> None:
        """Affiche un panneau interactif pour attribuer rapidement un pet."""

        view = AdminPanelView(self)
        description = (
            "Clique sur le bouton ci-dessous pour ouvrir un formulaire simple :\n"
            "• Sélectionne le joueur (ID ou mention)\n"
            "• Indique le nom exact du pet\n"
            "• Ajoute des options (gold, rainbow, galaxy, shiny) si nécessaire"
        )
        await ctx.send(
            embed=embeds.info_embed(description, title="🎛️ Panneau admin"),
            view=view,
        )

    @commands.command(name="addbalance")
    @commands.is_owner()
    async def add_balance(self, ctx: commands.Context, user: discord.Member, amount: int) -> None:
        """Admin: Ajouter du solde à un utilisateur."""

        if amount == 0:
            await ctx.send(embed=embeds.warning_embed("Le montant doit être différent de zéro."))
            return

        try:
            before, after = await self.database.increment_balance(
                user.id,
                amount,
                transaction_type="admin_adjustment",
                description=f"Ajustement manuel par {ctx.author.id}",
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        await ctx.send(
            embed=embeds.success_embed(
                f"Solde de {user.mention} mis à jour : {embeds.format_currency(before)} → {embeds.format_currency(after)}",
                title="Solde modifié",
            )
        )
        logger.info(
            "Admin add_balance",
            extra={
                "admin_id": ctx.author.id,
                "target_id": user.id,
                "amount": amount,
                "before": before,
                "after": after,
            },
        )

    @commands.command(name="addpb")
    @commands.is_owner()
    async def add_pb(
        self,
        ctx: commands.Context,
        user_id: int,
        amount: int,
        *,
        reason: str | None = None,
    ) -> None:
        """Admin: Ajouter des PB via l'identifiant utilisateur."""

        if amount <= 0:
            await ctx.send(embed=embeds.warning_embed("Le montant doit être supérieur à 0."))
            return

        description = reason or f"Ajout manuel par {ctx.author.id}"

        try:
            before, after = await self.database.increment_balance(
                user_id,
                amount,
                transaction_type="admin_grant",
                description=description,
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        member = None
        if ctx.guild is not None:
            member = ctx.guild.get_member(user_id)
        user_obj = member or self.bot.get_user(user_id)
        if user_obj is None:
            target_display = f"Utilisateur {user_id}"
        else:
            target_display = user_obj.mention

        lines = [
            f"Cible : {target_display}",
            f"Ajout : {embeds.format_currency(amount)}",
            f"Solde avant : {embeds.format_currency(before)}",
            f"Solde après : {embeds.format_currency(after)}",
        ]
        if reason:
            lines.append(f"Raison : {reason}")

        await ctx.send(embed=embeds.success_embed("\n".join(lines), title="PB ajoutés"))

        logger.info(
            "Admin add_pb",
            extra={
                "admin_id": ctx.author.id,
                "target_id": user_id,
                "amount": amount,
                "before": before,
                "after": after,
                "reason": reason,
            },
        )

    @commands.command(name="givepet", aliases=("transferpet",))
    @commands.is_owner()
    async def give_pet(
        self,
        ctx: commands.Context,
        source: discord.User,
        target: discord.User,
        *,
        pet: str,
    ) -> None:
        """Admin: Transférer un pet d'un utilisateur à un autre."""

        if source.id == target.id:
            await ctx.send(
                embed=embeds.warning_embed("L'utilisateur source et la cible doivent être différents."),
            )
            return

        pet_identifier = pet.strip()
        if not pet_identifier:
            await ctx.send(
                embed=embeds.error_embed(
                    "Précise l'identifiant ou le nom du pet à transférer.",
                    title="Pet introuvable",
                )
            )
            return

        await self.database.ensure_user(source.id)
        await self.database.ensure_user(target.id)

        transfer_result = None
        last_error: str | None = None

        if pet_identifier.isdigit():
            try:
                transfer_result = await self.database.admin_transfer_user_pet(
                    source.id,
                    target.id,
                    int(pet_identifier),
                )
            except DatabaseError as exc:
                last_error = str(exc)
        else:
            candidates = await self.database.get_user_pet_by_name(source.id, pet_identifier)
            if not candidates:
                await ctx.send(
                    embed=embeds.error_embed(
                        f"{source.mention} n'a aucun pet nommé '{pet_identifier}'.",
                        title="Pet introuvable",
                    )
                )
                return

            ordered_candidates = sorted(
                candidates,
                key=lambda record: (bool(record["is_active"]), record["acquired_at"]),
            )

            for candidate in ordered_candidates:
                try:
                    transfer_result = await self.database.admin_transfer_user_pet(
                        source.id,
                        target.id,
                        int(candidate["id"]),
                    )
                except DatabaseError as exc:
                    last_error = str(exc)
                    continue
                else:
                    last_error = None
                    break

        if transfer_result is None:
            message = last_error or "Impossible de transférer ce pet pour le moment."
            await ctx.send(embed=embeds.error_embed(message))
            return

        flags: list[str] = []
        if transfer_result["is_huge"]:
            flags.append("Huge")
        if transfer_result["is_gold"]:
            flags.append("Or")
        if transfer_result["is_rainbow"]:
            flags.append("Rainbow")

        pet_description = f"**{transfer_result['name']}** ({transfer_result['rarity']})"
        if flags:
            pet_description += " – " + ", ".join(flags)

        lines = [
            f"Source : {source.mention} (`{source.id}`)",
            f"Cible : {target.mention} (`{target.id}`)",
            f"Pet transféré : {pet_description}",
            f"ID inventaire : #{transfer_result['id']}",
            "Revenu de base : "
            f"{embeds.format_currency(scale_pet_value(int(transfer_result['base_income_per_hour'])))} PB/h",
        ]

        await ctx.send(embed=embeds.success_embed("\n".join(lines), title="Pet transféré"))

        logger.info(
            "Admin give_pet",
            extra={
                "admin_id": ctx.author.id,
                "source_id": source.id,
                "target_id": target.id,
                "user_pet_id": int(transfer_result["id"]),
                "pet_name": str(transfer_result["name"]),
            },
        )

    @commands.command(name="resetuser")
    @commands.is_owner()
    async def reset_user(self, ctx: commands.Context, user: discord.Member) -> None:
        """Admin: Réinitialiser un utilisateur."""

        await self.database.ensure_user(user.id)
        balance = await self.database.fetch_balance(user.id)
        if balance > 0:
            await self.database.increment_balance(
                user.id,
                -balance,
                transaction_type="admin_reset",
                description=f"Réinitialisation par {ctx.author.id}",
            )

        async with self.database.transaction() as connection:
            await connection.execute("DELETE FROM user_pets WHERE user_id = $1", user.id)
            await connection.execute("DELETE FROM pet_openings WHERE user_id = $1", user.id)
            await connection.execute("UPDATE users SET last_daily = NULL, pet_last_claim = NULL WHERE user_id = $1", user.id)

        await self.database.reset_user_grade(user.id)

        grade_cog = self.bot.get_cog("GradeSystem")
        if grade_cog and hasattr(grade_cog, "_assign_grade_role"):
            try:
                await grade_cog._assign_grade_role(user, 0)  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Impossible de retirer les rôles de grade pour %s", user.id)

        await ctx.send(
            embed=embeds.success_embed(
                f"Données d'{user.mention} réinitialisées.",
                title="Utilisateur réinitialisé",
            )
        )
        logger.info("Admin reset_user", extra={"admin_id": ctx.author.id, "target_id": user.id})

    @commands.command(name="nerfmoney", aliases=("adminresetriches",))
    @commands.is_owner()
    async def admin_reset_riches(self, ctx: commands.Context) -> None:
        """Reset les :Gem: des utilisateurs très riches (commande one-shot)."""

        already_run = await self.database.get_config_flag("rich_reset_executed")
        if already_run:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Cette commande a déjà été exécutée et ne peut l'être qu'une fois."
                )
            )
            return

        confirm_text = (
            "⚠️ **CONFIRMATION REQUISE**\n\n"
            "Cette action va :\n"
            f"• Récupérer tous les utilisateurs avec ≥1M {Emojis.GEM}\n"
            f"• Reset leurs {Emojis.GEM} à 100K\n"
            "• Logger l'opération\n"
            "• Marquer comme exécuté (irréversible)\n\n"
            "Réagis avec ✅ pour confirmer (30s)"
        )
        confirm_msg = await ctx.send(confirm_text)
        await confirm_msg.add_reaction("✅")

        def check(reaction: discord.Reaction, user: discord.User) -> bool:
            return (
                user.id == ctx.author.id
                and reaction.message.id == confirm_msg.id
                and str(reaction.emoji) == "✅"
            )

        try:
            await self.bot.wait_for("reaction_add", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send(embed=embeds.warning_embed("⏱️ Opération annulée (timeout)."))
            return

        status_msg = await ctx.send(embed=embeds.info_embed("⏳ Traitement en cours..."))

        try:
            result = await self.database.reset_rich_users_gems(
                threshold=1_000_000,
                new_amount=100_000,
            )

            affected_count = int(result["affected_count"])
            total_removed = int(result["total_gems_removed"])
            users_details = list(result["users"])

            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "executor": str(ctx.author),
                "executor_id": ctx.author.id,
                "affected_users": affected_count,
                "total_gems_removed": total_removed,
                "details": users_details,
            }

            await self._log_reset_operation(log_entry)
            await self.database.set_config_flag("rich_reset_executed", True)

            embed = discord.Embed(
                title=f"✅ Reset {Emojis.GEM} effectué",
                color=0x57F287,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(
                name="Statistiques",
                value=(
                    f"**Utilisateurs affectés** : {affected_count}\n"
                    f"**{Emojis.GEM} retirées** : {total_removed:,}\n"
                    f"**Nouvelle limite** : 100,000 {Emojis.GEM}"
                ),
                inline=False,
            )

            if users_details:
                top_5 = users_details[:5]
                details_text = "\n".join(
                    f"<@{user['user_id']}> : {user['old_gems']:,} → 100K (-{user['removed']:,})"
                    for user in top_5
                )
                embed.add_field(
                    name="Top 5 Affectés",
                    value=details_text,
                    inline=False,
                )

            embed.set_footer(text="Opération irréversible - Logged dans admin_logs/")

            await status_msg.edit(content=None, embed=embed)
        except Exception as exc:
            logger.exception("Erreur lors du reset des :Gem: riches")
            await status_msg.edit(
                embed=embeds.error_embed(
                    f"❌ Erreur : {exc}\n\nVoir les logs pour détails."
                )
            )

    @commands.command(name="resetserver", aliases=("resetprogression", "resetall"))
    @commands.is_owner()
    async def reset_server(self, ctx: commands.Context) -> None:
        """Admin: Réinitialise TOUTE la progression de TOUS les utilisateurs.

        Supprime soldes, gemmes, pets, grades, clans, potions, enchantements,
        tombola, historique de transactions, etc. Action IRRÉVERSIBLE.
        """

        warning_text = (
            "🚨 **DANGER — RESET COMPLET DU SERVEUR** 🚨\n\n"
            "Cette action va supprimer **DÉFINITIVEMENT** pour **TOUS** les "
            "utilisateurs :\n"
            f"• Soldes et {Emojis.GEM}\n"
            "• Tous les pets possédés\n"
            "• Grades, zones débloquées et masteries\n"
            "• Clans et adhésions\n"
            "• Potions, enchantements équipés\n"
            "• Listings marché/plaza, enchères en cours\n"
            "• Tickets et entrées de tombola\n"
            "• Historique complet des transactions\n"
            "• L'état du King of the Hill\n\n"
            "**Il n'existe aucune sauvegarde automatique. Cette opération est "
            "irréversible.**\n\n"
            f"Pour confirmer, tape exactement `CONFIRMER {ctx.guild.id if ctx.guild else 0}` "
            "dans les 30 prochaines secondes."
        )
        await ctx.send(warning_text)

        expected = f"CONFIRMER {ctx.guild.id if ctx.guild else 0}"

        def check(message: discord.Message) -> bool:
            return (
                message.author.id == ctx.author.id
                and message.channel.id == ctx.channel.id
                and message.content.strip() == expected
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send(embed=embeds.warning_embed("⏱️ Reset annulé (timeout ou texte incorrect)."))
            return

        confirm_msg = await ctx.send(
            "Dernière confirmation : réagis avec ✅ pour lancer le reset (15s)."
        )
        await confirm_msg.add_reaction("✅")

        def reaction_check(reaction: discord.Reaction, user: discord.User) -> bool:
            return (
                user.id == ctx.author.id
                and reaction.message.id == confirm_msg.id
                and str(reaction.emoji) == "✅"
            )

        try:
            await self.bot.wait_for("reaction_add", check=reaction_check, timeout=15.0)
        except asyncio.TimeoutError:
            await ctx.send(embed=embeds.warning_embed("⏱️ Reset annulé (timeout)."))
            return

        status_msg = await ctx.send(embed=embeds.info_embed("⏳ Reset complet en cours..."))

        try:
            result = await self.database.reset_all_progress()

            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "executor": str(ctx.author),
                "executor_id": ctx.author.id,
                "guild_id": ctx.guild.id if ctx.guild else None,
                "users_removed": result["users_removed"],
            }
            await self._log_reset_operation(log_entry, prefix="server_reset")

            await status_msg.edit(
                content=None,
                embed=embeds.success_embed(
                    f"**{result['users_removed']}** utilisateur(s) réinitialisé(s). "
                    "Toute la progression du serveur a été effacée.",
                    title="✅ Reset serveur effectué",
                ),
            )
            logger.warning(
                "Admin reset_server exécuté",
                extra={"admin_id": ctx.author.id, "users_removed": result["users_removed"]},
            )
        except Exception as exc:
            logger.exception("Erreur lors du reset complet du serveur")
            await status_msg.edit(
                embed=embeds.error_embed(f"❌ Erreur : {exc}\n\nVoir les logs pour détails.")
            )

    async def _log_reset_operation(
        self, log_entry: dict[str, Any], *, prefix: str = "rich_reset"
    ) -> None:
        """Sauvegarde l'opération dans un fichier JSON."""

        log_dir = Path("admin_logs")
        log_dir.mkdir(exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"{prefix}_{timestamp}.json"
        log_file.write_text(
            json.dumps(log_entry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Reset operation logged to %s", log_file)

    @commands.command(name="dbstats")
    @commands.is_owner()
    async def db_stats(self, ctx: commands.Context) -> None:
        """Admin: Statistiques de la base de données."""

        try:
            stats = await self.database.get_database_stats()
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        description = (
            f"Utilisateurs : **{int(stats['users_count'])}**\n"
            f"Transactions : **{int(stats['transactions_count'])}**\n"
            f"Pets détenus : **{int(stats['pets_count'])}**\n"
            f"Annonces actives : **{int(stats['listings_active'])}/{int(stats['listings_count'])}**\n"
            f"Richesse totale : **{embeds.format_currency(int(stats['total_balance']))}**"
        )
        embed = embeds.info_embed(description, title="Statistiques base de données")
        await ctx.send(embed=embed)

    @commands.command(name="analytics")
    @commands.is_owner()
    async def analytics(self, ctx: commands.Context) -> None:
        """Admin: Résumé système et analytics avancées."""

        status_msg = await ctx.send("⏳ Génération des analytics…")
        try:
            totals, pet_values = await asyncio.wait_for(
                self.database.get_analytics_snapshot(),
                timeout=QUERY_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, asyncpg.exceptions.QueryCanceledError):
            await status_msg.edit(
                content=None,
                embed=embeds.warning_embed(
                    "La requête est trop lourde pour être traitée rapidement."
                ),
            )
            return
        except DatabaseError as exc:
            await status_msg.edit(embed=embeds.error_embed(str(exc)), content=None)
            return

        summary_lines = [
            "EcoBot est un bot d'économie complet centré sur les pets, l'épargne et le commerce.",
            "Les joueurs gagnent des PB via le daily, l'activité et les revenus des pets équipés.",
            f"Les {Emojis.GEM} servent aux achats premium (boutique, enchères, échanges spéciaux).",
            "Le RAP mesure la valeur cumulée des pets possédés (base + variations).",
            "Un marché et des annonces permettent de vendre/échanger des pets entre joueurs.",
            "Les systèmes de grades, clans, potions et enchantements enrichissent la progression.",
        ]
        embed = embeds.info_embed("\n".join(summary_lines), title="📊 Analytics admin")

        totals_lines = [
            f"💰 PB totaux : **{embeds.format_currency(totals['total_pb'])}**",
            f"{Emojis.GEM} totales : **{embeds.format_gems(totals['total_gems'])}**",
            f"📈 RAP total : **{embeds.format_gems(totals['total_rap'])}**",
        ]
        embed.add_field(name="Totaux serveur", value="\n".join(totals_lines), inline=False)
        await status_msg.edit(content=None, embed=embed)

        if not pet_values:
            await ctx.send(embed=embeds.warning_embed("Aucun pet trouvé pour calculer les valeurs."))
            return

        sorted_values = sorted(pet_values, key=lambda item: int(item["value"]), reverse=True)
        pet_lines: list[str] = []
        for pet in sorted_values:
            name = str(pet["name"])
            emoji = PET_EMOJIS.get(name, PET_EMOJIS.get("default", "🐾"))
            value = embeds.format_gems(int(pet["value"]))
            pet_lines.append(f"{emoji} **{name}** — {value}")

        chunk: list[str] = []
        chunk_length = 0
        chunks: list[str] = []
        for line in pet_lines:
            line_length = len(line) + 1
            if chunk and chunk_length + line_length > 900:
                chunks.append("\n".join(chunk))
                chunk = []
                chunk_length = 0
            chunk.append(line)
            chunk_length += line_length
        if chunk:
            chunks.append("\n".join(chunk))

        for index, block in enumerate(chunks, start=1):
            title = "🐾 Valeur des pets" if index == 1 else f"🐾 Valeur des pets (suite {index})"
            await ctx.send(embed=embeds.info_embed(block, title=title))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
