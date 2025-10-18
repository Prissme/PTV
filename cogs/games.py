"""Mini-jeux hors casino (Pierre-Papier-Ciseaux)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from utils import embeds

logger = logging.getLogger(__name__)

CHOICES = {"pierre": 0, "papier": 1, "ciseaux": 2, "rock": 0, "paper": 1, "scissors": 2}
MOVE_NAMES = {0: "Pierre", 1: "Papier", 2: "Ciseaux"}
RESULT_MATRIX = {
    (0, 2): 1,
    (1, 0): 1,
    (2, 1): 1,
    (2, 0): -1,
    (0, 1): -1,
    (1, 2): -1,
}


class Games(commands.Cog):
    """Gestion des mini-jeux entre joueurs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.pending: Dict[Tuple[int, int], Dict[str, object]] = {}
        self.lock = asyncio.Lock()

    def _cleanup(self) -> None:
        now = time.time()
        for key, data in list(self.pending.items()):
            if now - data.get("time", 0) > 300:
                self.pending.pop(key, None)

    async def play_rps(self, challenger: discord.Member, opponent: discord.Member, amount: int, move: str) -> discord.Embed:
        move_key = CHOICES.get(move.lower())
        if move_key is None:
            return embeds.error_embed("Choix invalide. Utilise pierre, papier ou ciseaux.")
        if challenger == opponent:
            return embeds.error_embed("Impossible de te défier toi-même.")
        if opponent.bot:
            return embeds.error_embed("Tu ne peux pas défier un bot.")
        if amount <= 0:
            return embeds.error_embed("Le montant doit être positif.")

        key = (challenger.id, opponent.id)
        reverse_key = (opponent.id, challenger.id)
        async with self.lock:
            self._cleanup()
            if reverse_key in self.pending:
                challenge = self.pending.pop(reverse_key)
                if amount != challenge["amount"]:
                    return embeds.error_embed("Le montant doit correspondre à celui proposé.")
                challenger_move = challenge["move"]
                return await self._resolve_rps(opponent, challenger, amount, challenge_move=challenger_move, opponent_move=move_key)
            else:
                self.pending[key] = {"amount": amount, "move": move_key, "time": time.time()}
                return embeds.info_embed(
                    f"Défi envoyé à {opponent.mention}. Il doit utiliser la commande avec le même montant pour accepter.",
                    title="Défi envoyé",
                )

    async def _resolve_rps(
        self,
        challenger: discord.Member,
        opponent: discord.Member,
        amount: int,
        *,
        challenge_move: int,
        opponent_move: int,
    ) -> discord.Embed:
        async with self.database.transaction() as conn:
            challenger_balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                challenger.id,
            )
            opponent_balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                opponent.id,
            )
            if challenger_balance is None or challenger_balance < amount:
                return embeds.error_embed(f"{challenger.display_name} n'a pas assez de fonds.")
            if opponent_balance is None or opponent_balance < amount:
                return embeds.error_embed(f"{opponent.display_name} n'a pas assez de fonds.")

            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", amount, challenger.id)
            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", amount, opponent.id)
            challenger_after = challenger_balance - amount
            opponent_after = opponent_balance - amount

        result = RESULT_MATRIX.get((challenge_move, opponent_move), 0)
        total_pot = amount * 2
        description = (
            f"{challenger.mention} a joué **{MOVE_NAMES[challenge_move]}**\n"
            f"{opponent.mention} a joué **{MOVE_NAMES[opponent_move]}**\n"
        )
        if result == 1:
            before, after = await self.database.increment_balance(challenger.id, total_pot)
            await self.bot.transaction_logs.log(
                challenger.id,
                "rps_win",
                total_pot,
                before,
                after,
                description=f"Victoire contre {opponent.display_name}",
            )
            await self.bot.transaction_logs.log(
                opponent.id,
                "rps_loss",
                -amount,
                opponent_balance,
                opponent_after,
                description=f"Défaite contre {challenger.display_name}",
            )
            description += f"{challenger.mention} remporte {embeds.format_currency(total_pot)} !"
        elif result == -1:
            before, after = await self.database.increment_balance(opponent.id, total_pot)
            await self.bot.transaction_logs.log(
                opponent.id,
                "rps_win",
                total_pot,
                before,
                after,
                description=f"Victoire contre {challenger.display_name}",
            )
            await self.bot.transaction_logs.log(
                challenger.id,
                "rps_loss",
                -amount,
                challenger_balance,
                challenger_after,
                description=f"Défaite contre {opponent.display_name}",
            )
            description += f"{opponent.mention} remporte {embeds.format_currency(total_pot)} !"
        else:
            await self.database.add_public_bank_funds(total_pot)
            description += "Égalité ! La mise part à la banque publique."
            await self.bot.transaction_logs.log(
                challenger.id,
                "rps_tie",
                -amount,
                challenger_balance,
                challenger_after,
                description="Égalité PPC",
            )
            await self.bot.transaction_logs.log(
                opponent.id,
                "rps_tie",
                -amount,
                opponent_balance,
                opponent_after,
                description="Égalité PPC",
            )
        return embeds.info_embed(description, title="Pierre-Papier-Ciseaux")

    @commands.command(name="rps")
    async def rps_prefix(self, ctx: commands.Context, opponent: discord.Member, amount: int, move: str) -> None:
        embed = await self.play_rps(ctx.author, opponent, amount, move)
        await ctx.send(embed=embed)

    @app_commands.command(name="rps", description="Défier un joueur en pierre-papier-ciseaux")
    async def rps_slash(self, interaction: discord.Interaction, adversaire: discord.Member, montant: int, coup: str) -> None:
        embed = await self.play_rps(interaction.user, adversaire, montant, coup)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Games(bot))
