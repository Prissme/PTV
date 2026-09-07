from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from utils import embeds

# ---------------------------------------------------------------------------
# Configuration — facilement ajustable
# ---------------------------------------------------------------------------

PINATA_BASE_INCOME_PER_SECOND: float = 50.0  # niveau 1
PINATA_LEVEL_MULTIPLIER: float = 2.0  # revenu x2 par niveau
# IMPORTANT : sans plafond, pinata_level est une boucle de rétroaction
# incontrôlable (chaque niveau double le revenu -> plus de cash -> plus
# d'upgrades chance -> plus de niveaux...). On plafonne pour garder un
# revenu maximum raisonnable par rapport au coût total à atteindre (~154M$
# pour maxer tous les upgrades cash). A ce plafond + cash upgrades maxés,
# le revenu reste élevé mais fini.
MAX_PINATA_LEVEL: int = 15

BASE_COOLDOWN_SECONDS: float = 10.0
COOLDOWN_REDUCTION_PER_UPGRADE: float = 0.4
MAX_COOLDOWN_UPGRADES: int = 20  # -> plancher 10 - 20*0.4 = 2s

BASE_UPGRADE_CHANCE: float = 0.0006  # 1/1667 — sans achat : ~6-7 jours pour les 15 niveaux
CHANCE_BONUS_PER_UPGRADE: float = 0.00012  # +0,012 pt de %/achat
MAX_CHANCE_UPGRADES: int = 20  # -> chance de base max (niveau 1) = 1/333
LEVEL_CHANCE_DECAY: float = 0.90  # -10% de chance (multiplicatif) par niveau déjà atteint
# La piñata devient plus dure à casser à mesure qu'elle monte de niveau :
# chaque niveau multiplie la chance par 0.90 (donc niveau 15 = chance ÷ ~4,4
# par rapport au niveau 1, à upgrades égaux). Un malus multiplicatif (plutôt
# qu'un flat) évite de tomber à 0% ou en négatif en fin de run — la chance
# décroît mais reste toujours positive.
# Calcul (espérance) : temps total pour les 15 niveaux.
# Sans upgrade   : cooldown=10s, chance niveau 1=1/1667  -> ~6,7 jours
# Tout maxé      : cooldown=2s,  chance niveau 1=1/333   -> ~6,4 heures
# (avant ce fix : base 0.1% + upgrades à +4pts/achat -> les 15 niveaux
# tombaient en quelques minutes, même sans rien acheter à la boutique)

CASH_BONUS_PER_UPGRADE: float = 0.10  # +10% de revenu par achat
MAX_CASH_UPGRADES: int = 50  # -> +500% max

# Coût de départ + facteur exponentiel par type d'upgrade. La progression est
# volontairement très raide : chaque palier demande un investissement nettement
# supérieur au précédent.
UPGRADE_BASE_COSTS: dict[str, float] = {
    "cooldown": 1_500.0,
    "chance": 3_000.0,
    "cash": 50_000.0,
}
UPGRADE_COST_RATIOS: dict[str, float] = {
    "cooldown": 1.75,
    "chance": 1.75,
    "cash": 1.25,
}

UPGRADE_LABELS: dict[str, str] = {
    "cooldown": "cooldown (-0.4s/achat)",
    "chance": "chance d'upgrade (+0.012%/achat)",
    "cash": "production (+10%/achat)",
}


def _upgrade_cost(upgrade_type: str, current_count: int) -> int:
    base = UPGRADE_BASE_COSTS[upgrade_type]
    ratio = UPGRADE_COST_RATIOS[upgrade_type]
    return int(round(base * (ratio ** current_count)))


def _max_for(upgrade_type: str) -> int:
    return {
        "cooldown": MAX_COOLDOWN_UPGRADES,
        "chance": MAX_CHANCE_UPGRADES,
        "cash": MAX_CASH_UPGRADES,
    }[upgrade_type]


class PinataShopView(discord.ui.View):
    """Raccourcis d'achat réservés au joueur qui a ouvert la boutique."""

    def __init__(self, cog: "EventPinata", ctx: commands.Context) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Seul le propriétaire de cette boutique peut utiliser ces boutons.",
                ephemeral=True,
            )
            return False
        return True

    async def _buy(self, interaction: discord.Interaction, upgrade_type: str) -> None:
        await interaction.response.defer()
        await self.cog.pinatashop.callback(self.cog, self.ctx, args=upgrade_type)

    @discord.ui.button(label="Cooldown", emoji="⏱️", style=discord.ButtonStyle.primary)
    async def cooldown(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._buy(interaction, "cooldown")

    @discord.ui.button(label="Chance", emoji="🎲", style=discord.ButtonStyle.success)
    async def chance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._buy(interaction, "chance")

    @discord.ui.button(label="Production", emoji="💵", style=discord.ButtonStyle.secondary)
    async def cash(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._buy(interaction, "cash")

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class EventPinata(commands.Cog):
    """Event Anniversaire : la Piñata (dollars, upgrades de niveau et de stats)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._tables_ready = False

    async def cog_load(self) -> None:
        await self._ensure_tables()

    async def _ensure_tables(self) -> None:
        if self._tables_ready:
            return
        pool = self.database.pool
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pinata_event (
                    user_id BIGINT PRIMARY KEY,
                    dollars DOUBLE PRECISION NOT NULL DEFAULT 0,
                    last_income_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    pinata_level INT NOT NULL DEFAULT 1,
                    last_attempt_at TIMESTAMPTZ,
                    cooldown_upgrades INT NOT NULL DEFAULT 0,
                    chance_upgrades INT NOT NULL DEFAULT 0,
                    cash_upgrades INT NOT NULL DEFAULT 0
                )
                """
            )
        self._tables_ready = True

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------

    @staticmethod
    def _income_per_second(level: int, cash_upgrades: int) -> float:
        level = min(level, MAX_PINATA_LEVEL)  # sécurité anti-explosion exponentielle
        base = PINATA_BASE_INCOME_PER_SECOND * (PINATA_LEVEL_MULTIPLIER ** (level - 1))
        return base * (1 + CASH_BONUS_PER_UPGRADE * cash_upgrades)

    @staticmethod
    def _cooldown_seconds(cooldown_upgrades: int) -> float:
        return max(
            0.0,
            BASE_COOLDOWN_SECONDS - COOLDOWN_REDUCTION_PER_UPGRADE * cooldown_upgrades,
        )

    @staticmethod
    def _upgrade_chance(chance_upgrades: int, level: int) -> float:
        base = BASE_UPGRADE_CHANCE + CHANCE_BONUS_PER_UPGRADE * chance_upgrades
        return min(1.0, base * (LEVEL_CHANCE_DECAY ** (level - 1)))

    @staticmethod
    def _chance_as_fraction(chance: float) -> str:
        """Formate une probabilité en fraction lisible du type '1/5 000'."""
        if chance <= 0:
            return "1/∞"
        return f"1/{round(1 / chance):,}".replace(",", " ")

    async def _ensure_row(self, connection, user_id: int):
        row = await connection.fetchrow(
            "SELECT * FROM pinata_event WHERE user_id = $1",
            user_id,
        )
        if row is None:
            await connection.execute(
                """
                INSERT INTO pinata_event (user_id, dollars, last_income_at)
                VALUES ($1, 0, now())
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
            )
            row = await connection.fetchrow(
                "SELECT * FROM pinata_event WHERE user_id = $1",
                user_id,
            )
        return row

    async def _settle_income(self, connection, user_id: int):
        """Crédite les dollars accumulés depuis la dernière visite. Renvoie la row à jour."""
        row = await self._ensure_row(connection, user_id)
        elapsed = (datetime.now(timezone.utc) - row["last_income_at"]).total_seconds()
        elapsed = max(0.0, elapsed)
        income_per_second = self._income_per_second(row["pinata_level"], row["cash_upgrades"])
        new_dollars = float(row["dollars"]) + income_per_second * elapsed

        await connection.execute(
            """
            UPDATE pinata_event
            SET dollars = $2, last_income_at = now()
            WHERE user_id = $1
            """,
            user_id,
            new_dollars,
        )
        return await connection.fetchrow(
            "SELECT * FROM pinata_event WHERE user_id = $1", user_id
        )

    async def get_dollars(self, user_id: int) -> float:
        pool = self.database.pool
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await self._settle_income(connection, user_id)
                return float(row["dollars"])

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------

    @commands.command(name="pinata")
    async def pinata(self, ctx: commands.Context) -> None:
        """Affiche l'état de ta piñata, ou tente un upgrade si le cooldown est écoulé."""
        user_id = ctx.author.id
        pool = self.database.pool

        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await self._settle_income(connection, user_id)

                cooldown = self._cooldown_seconds(row["cooldown_upgrades"])
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=cooldown)

                # UPDATE atomique : on ne "réserve" la tentative que si le
                # cooldown est bien écoulé, en une seule requête SQL. Ça
                # verrouille la ligne le temps de l'opération, donc si deux
                # commandes arrivent en même temps (spam / double-clic),
                # une seule passe le WHERE ; la seconde voit last_attempt_at
                # déjà mis à jour et échoue. Avant ce fix, le check (SELECT)
                # et l'écriture (UPDATE) étaient deux étapes séparées : du
                # spam pouvait faire lire l'ancienne valeur par plusieurs
                # requêtes en parallèle et contourner totalement le cooldown.
                claimed = await connection.fetchrow(
                    """
                    UPDATE pinata_event
                    SET last_attempt_at = now()
                    WHERE user_id = $1
                      AND (last_attempt_at IS NULL OR last_attempt_at <= $2)
                    RETURNING pinata_level, cash_upgrades, cooldown_upgrades, chance_upgrades, dollars
                    """,
                    user_id,
                    cutoff,
                )

                if claimed is None:
                    # Cooldown pas encore écoulé : on relit l'état actuel pour l'affichage.
                    current = await connection.fetchrow(
                        "SELECT * FROM pinata_event WHERE user_id = $1", user_id
                    )
                    last_attempt = current["last_attempt_at"]
                    remaining = (
                        cooldown - (datetime.now(timezone.utc) - last_attempt).total_seconds()
                        if last_attempt is not None
                        else 0.0
                    )
                    income = self._income_per_second(
                        current["pinata_level"], current["cash_upgrades"]
                    )
                    chance_display = self._chance_as_fraction(
                        self._upgrade_chance(current["chance_upgrades"], current["pinata_level"])
                    )
                    await ctx.send(
                        embed=embeds.info_embed(
                            f"🪅 Piñata niveau **{current['pinata_level']}** — "
                            f"**{income:.1f}$/s**\n"
                            f"🎲 Chance d'upgrade : **{chance_display}**\n"
                            f"💵 Solde : **{current['dollars']:.1f}$**\n"
                            f"⏳ Prochain essai dans **{max(remaining, 0.0):.1f}s**.",
                            title="Piñata de l'event",
                        )
                    )
                    return

                chance = self._upgrade_chance(claimed["chance_upgrades"], claimed["pinata_level"])
                success = random.random() < chance
                level = int(claimed["pinata_level"])
                cash_upgrades = int(claimed["cash_upgrades"])
                cooldown_upgrades = int(claimed["cooldown_upgrades"])

                if success and level < MAX_PINATA_LEVEL:
                    await connection.execute(
                        "UPDATE pinata_event SET pinata_level = pinata_level + 1 WHERE user_id = $1",
                        user_id,
                    )
                    level += 1
                elif success and level >= MAX_PINATA_LEVEL:
                    success = False  # déjà au niveau max, pas de gain supplémentaire

        income = self._income_per_second(level, cash_upgrades)
        if success:
            await ctx.send(
                embed=embeds.success_embed(
                    f"🎉 Ta piñata passe au **niveau {level}** ! "
                    f"Elle rapporte maintenant **{income:.1f}$/s**.",
                    title="🪅 Upgrade réussi !",
                )
            )
        elif level >= MAX_PINATA_LEVEL:
            await ctx.send(
                embed=embeds.info_embed(
                    f"🪅 Ta piñata a atteint le niveau **max ({MAX_PINATA_LEVEL})** ! "
                    f"Elle rapporte **{income:.1f}$/s**.\n"
                    f"Fonce sur `e!pinatashop` pour dépenser tes dollars.",
                    title="Piñata au maximum",
                )
            )
        else:
            chance_display = self._chance_as_fraction(chance)
            await ctx.send(
                embed=embeds.info_embed(
                    f"🪅 Pas d'upgrade cette fois... Ta piñata niveau **{level}** "
                    f"continue de rapporter **{income:.1f}$/s**.\n"
                    f"🎲 Chance d'upgrade : **{chance_display}**\n"
                    f"Réessaie dans {self._cooldown_seconds(cooldown_upgrades):.1f}s.",
                    title="Piñata secouée",
                )
            )

    @commands.command(name="pinatashop", aliases=("pinataupgrade", "pinataboutique"))
    async def pinatashop(self, ctx: commands.Context, *, args: str | None = None) -> None:
        """Achète une amélioration de piñata : `e!pinatashop cooldown|chance|cash`."""
        user_id = ctx.author.id
        choice = (args or "").strip().lower()

        if choice not in UPGRADE_BASE_COSTS:
            pool = self.database.pool
            async with pool.acquire() as connection:
                async with connection.transaction():
                    row = await self._settle_income(connection, user_id)

            chance_display = self._chance_as_fraction(
                self._upgrade_chance(row["chance_upgrades"], row["pinata_level"])
            )
            lines = [
                f"💵 Solde : **{row['dollars']:.1f}$**",
                f"🎲 Chance d'upgrade actuelle : **{chance_display}**",
                "",
            ]
            for key in ("cooldown", "chance", "cash"):
                count = row[f"{key}_upgrades"]
                cap = _max_for(key)
                cost = _upgrade_cost(key, count) if count < cap else None
                cost_text = f"{cost}$" if cost is not None else "MAX"
                lines.append(
                    f"• `{key}` — {UPGRADE_LABELS[key]} : {count}/{cap} "
                    f"(prochain : {cost_text})"
                )
            lines.append("")
            lines.append("Utilise les boutons ci-dessous ou la commande `e!pinatashop <upgrade>`.")
            await ctx.send(
                embed=embeds.info_embed("\n".join(lines), title="🪅 Boutique de la piñata"),
                view=PinataShopView(self, ctx),
            )
            return

        pool = self.database.pool
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await self._settle_income(connection, user_id)
                count = int(row[f"{choice}_upgrades"])
                cap = _max_for(choice)

                if count >= cap:
                    await ctx.send(
                        embed=embeds.error_embed(
                            f"Tu as déjà atteint le max pour `{choice}` ({cap}/{cap})."
                        )
                    )
                    return

                cost = _upgrade_cost(choice, count)
                if float(row["dollars"]) < cost:
                    await ctx.send(
                        embed=embeds.error_embed(
                            f"Il te faut **{cost}$** pour cet upgrade (tu as {row['dollars']:.1f}$)."
                        )
                    )
                    return

                await connection.execute(
                    f"""
                    UPDATE pinata_event
                    SET dollars = dollars - $2, {choice}_upgrades = {choice}_upgrades + 1
                    WHERE user_id = $1
                    """,
                    user_id,
                    cost,
                )

        await ctx.send(
            embed=embeds.success_embed(
                f"Upgrade **{UPGRADE_LABELS[choice]}** acheté pour **{cost}$** "
                f"({count + 1}/{cap}).",
                title="🪅 Piñata améliorée !",
            )
        )

    @commands.command(name="pinatareset", aliases=("pinataresetall",))
    @commands.is_owner()
    async def pinatareset(self, ctx: commands.Context, confirm: str | None = None) -> None:
        """Réinitialise la piñata de TOUS les joueurs (admin uniquement).

        Usage : `e!pinatareset confirm`
        """
        if confirm != "confirm":
            await ctx.send(
                embed=embeds.error_embed(
                    "⚠️ Ça va **réinitialiser la piñata de tout le monde** "
                    "(niveau, dollars, upgrades, cadeau débloqué).\n"
                    "Tape `e!pinatareset confirm` pour valider.",
                )
            )
            return

        pool = self.database.pool
        async with pool.acquire() as connection:
            result = await connection.execute("DELETE FROM pinata_event")

        # asyncpg renvoie une string du type "DELETE 42"
        deleted_count = result.split(" ")[-1] if result else "0"

        await ctx.send(
            embed=embeds.success_embed(
                f"🪅 Piñata réinitialisée pour **{deleted_count}** joueur(s).",
                title="Reset effectué",
            )
        )

    @commands.command(name="pinatareset_user")
    @commands.is_owner()
    async def pinatareset_user(self, ctx: commands.Context, member: discord.Member) -> None:
        """Réinitialise la piñata d'un seul joueur (admin uniquement)."""
        pool = self.database.pool
        async with pool.acquire() as connection:
            result = await connection.execute(
                "DELETE FROM pinata_event WHERE user_id = $1", member.id
            )

        if result.endswith(" 0"):
            await ctx.send(
                embed=embeds.info_embed(f"{member.mention} n'avait pas de piñata en cours.")
            )
            return

        await ctx.send(
            embed=embeds.success_embed(
                f"🪅 Piñata de {member.mention} réinitialisée.",
                title="Reset effectué",
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventPinata(bot))
