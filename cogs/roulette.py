import discord
from discord.ext import commands
from discord import app_commands
import random
import logging
from datetime import datetime, timezone

from config import Colors, Emojis, OWNER_ID
from utils.embeds import create_error_embed, create_success_embed

logger = logging.getLogger(__name__)

class Roulette(commands.Cog):
    """Mini-jeu de roulette européenne avec mises et gains"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Configuration de la roulette
        self.MIN_BET = 10
        self.TAX_RATE = 0.01  # 1% de taxe sur les gains (non affichée)
        
        # Numéros et couleurs de la roulette européenne (37 numéros: 0-36)
        self.RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        self.BLACK_NUMBERS = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}
        self.GREEN_NUMBERS = {0}  # Le zéro est vert
        
        # Dictionnaire pour gérer les cooldowns
        self.roulette_cooldowns = {}
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info(f"✅ Cog Roulette initialisé (mise min: {self.MIN_BET}, taxe cachée: {self.TAX_RATE*100}%)")

    def _check_roulette_cooldown(self, user_id: int) -> float:
        """Vérifie et retourne le cooldown restant pour la roulette"""
        import time
        now = time.time()
        cooldown_duration = 5  # 5 secondes entre chaque spin
        if user_id in self.roulette_cooldowns:
            elapsed = now - self.roulette_cooldowns[user_id]
            if elapsed < cooldown_duration:
                return cooldown_duration - elapsed
        self.roulette_cooldowns[user_id] = now
        return 0

    def get_number_color(self, number: int) -> tuple:
        """Retourne la couleur et l'emoji d'un numéro"""
        if number in self.RED_NUMBERS:
            return "Rouge", "🔴"
        elif number in self.BLACK_NUMBERS:
            return "Noir", "⚫"
        else:  # 0
            return "Vert", "🟢"

    def calculate_winnings(self, bet_type: str, bet_amount: int, winning_number: int) -> int:
        """Calcule les gains selon le type de pari et le numéro gagnant"""
        
        # Pari sur un numéro spécifique (35:1)
        if bet_type.startswith("number_"):
            bet_number = int(bet_type.split("_")[1])
            if winning_number == bet_number:
                gross_winnings = bet_amount * 35  # 35:1 payout
                tax = int(gross_winnings * self.TAX_RATE)
                return gross_winnings - tax
            return 0
        
        # Pari sur Rouge (1:1)
        elif bet_type == "red":
            if winning_number in self.RED_NUMBERS:
                gross_winnings = bet_amount * 2  # 1:1 payout (mise + gain)
                tax = int(gross_winnings * self.TAX_RATE)
                return gross_winnings - tax
            return 0
        
        # Pari sur Noir (1:1) 
        elif bet_type == "black":
            if winning_number in self.BLACK_NUMBERS:
                gross_winnings = bet_amount * 2  # 1:1 payout (mise + gain)
                tax = int(gross_winnings * self.TAX_RATE)
                return gross_winnings - tax
            return 0
            
        # Pari sur Pair (1:1)
        elif bet_type == "even":
            if winning_number != 0 and winning_number % 2 == 0:
                gross_winnings = bet_amount * 2
                tax = int(gross_winnings * self.TAX_RATE)
                return gross_winnings - tax
            return 0
            
        # Pari sur Impair (1:1)
        elif bet_type == "odd":
            if winning_number != 0 and winning_number % 2 == 1:
                gross_winnings = bet_amount * 2
                tax = int(gross_winnings * self.TAX_RATE)
                return gross_winnings - tax
            return 0
            
        # Pari sur 1-18 (Manque) (1:1)
        elif bet_type == "low":
            if 1 <= winning_number <= 18:
                gross_winnings = bet_amount * 2
                tax = int(gross_winnings * self.TAX_RATE)
                return gross_winnings - tax
            return 0
            
        # Pari sur 19-36 (Passe) (1:1)
        elif bet_type == "high":
            if 19 <= winning_number <= 36:
                gross_winnings = bet_amount * 2
                tax = int(gross_winnings * self.TAX_RATE)
                return gross_winnings - tax
            return 0
        
        return 0

    async def transfer_tax_to_owner(self, amount: int):
        """Transfère la taxe à l'owner (silencieusement)"""
        if amount > 0 and OWNER_ID:
            try:
                await self.db.update_balance(OWNER_ID, amount)
            except Exception as e:
                logger.error(f"Erreur transfert taxe roulette: {e}")

    # ==================== COMMANDES ROULETTE ====================

    @app_commands.command(name="roulette", description="🎰 Joue à la roulette ! Mise sur rouge, noir, pair, impair, etc.")
    @app_commands.describe(
        pari="Type de pari: red, black, even, odd, low (1-18), high (19-36), ou un numéro (0-36)",
        mise="Montant à miser en PrissBucks (minimum 10)"
    )
    async def roulette_slash(self, interaction: discord.Interaction, pari: str, mise: int):
        """Slash command pour jouer à la roulette"""
        await interaction.response.defer()
        await self._execute_roulette(interaction, pari, mise, is_slash=True)

    @commands.command(name='roulette', aliases=['roul', 'casino', 'spin'])
    async def roulette_cmd(self, ctx, bet_type: str, bet_amount: int):
        """Joue à la roulette européenne avec différents types de paris"""
        await self._execute_roulette(ctx, bet_type, bet_amount)

    async def _execute_roulette(self, ctx_or_interaction, bet_type: str, bet_amount: int, is_slash=False):
        """Logique commune pour la roulette"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        user_id = user.id

        # Vérifier le cooldown
        cooldown_remaining = self._check_roulette_cooldown(user_id)
        if cooldown_remaining > 0:
            embed = discord.Embed(
                title=f"⏰ Cooldown actif !",
                description=f"Tu pourras rejouer à la roulette dans **{cooldown_remaining:.1f}** secondes.",
                color=Colors.WARNING
            )
            await send_func(embed=embed)
            return

        # Validations
        if bet_amount < self.MIN_BET:
            embed = create_error_embed(
                "Mise trop faible",
                f"La mise minimum est de **{self.MIN_BET:,}** PrissBucks !"
            )
            await send_func(embed=embed)
            return

        # Vérifier le solde
        try:
            current_balance = await self.db.get_balance(user_id)
            if current_balance < bet_amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Tu as **{current_balance:,}** PrissBucks mais tu veux miser **{bet_amount:,}** PrissBucks."
                )
                await send_func(embed=embed)
                return

            # Normaliser le type de pari
            bet_type = bet_type.lower()
            
            # Valider le type de pari
            valid_bets = ["red", "black", "even", "odd", "low", "high"]
            
            # Vérifier si c'est un numéro
            if bet_type.isdigit():
                number = int(bet_type)
                if 0 <= number <= 36:
                    bet_type = f"number_{number}"
                else:
                    embed = create_error_embed(
                        "Numéro invalide",
                        "Les numéros doivent être entre **0** et **36** !"
                    )
                    await send_func(embed=embed)
                    return
            elif bet_type not in valid_bets:
                embed = create_error_embed(
                    "Pari invalide",
                    "Types de paris valides :\n"
                    "• **red** / **black** - Rouge ou Noir (2:1)\n"
                    "• **even** / **odd** - Pair ou Impair (2:1)\n"  
                    "• **low** / **high** - 1-18 ou 19-36 (2:1)\n"
                    "• **0-36** - Numéro spécifique (36:1)\n\n"
                    f"Exemple: `/roulette red {self.MIN_BET}`"
                )
                await send_func(embed=embed)
                return

            # Débiter la mise
            await self.db.update_balance(user_id, -bet_amount)

            # Faire tourner la roulette
            winning_number = random.randint(0, 36)
            color_name, color_emoji = self.get_number_color(winning_number)

            # Calculer les gains
            winnings = self.calculate_winnings(bet_type, bet_amount, winning_number)

            # Si il y a des gains
            if winnings > 0:
                # Calculer la taxe cachée pour les logs
                gross_winnings = 0
                if bet_type.startswith("number_"):
                    gross_winnings = bet_amount * 35
                else:
                    gross_winnings = bet_amount * 2
                
                hidden_tax = int(gross_winnings * self.TAX_RATE)
                
                # Créditer les gains (déjà réduits de la taxe)
                await self.db.update_balance(user_id, winnings)
                
                # Transférer la taxe à l'owner (silencieusement)
                await self.transfer_tax_to_owner(hidden_tax)
                
                # Récupérer le nouveau solde
                new_balance = await self.db.get_balance(user_id)
                
                # Embed de victoire
                embed = discord.Embed(
                    title="🎉 VICTOIRE À LA ROULETTE !",
                    description=f"La bille s'arrête sur **{winning_number}** {color_emoji} **{color_name}** !",
                    color=Colors.SUCCESS
                )
                
                # Détails du pari
                if bet_type.startswith("number_"):
                    bet_description = f"Numéro **{bet_type.split('_')[1]}**"
                    multiplier = "36:1"
                else:
                    bet_descriptions = {
                        "red": "Rouge 🔴", "black": "Noir ⚫",
                        "even": "Pair", "odd": "Impair", 
                        "low": "1-18 (Manque)", "high": "19-36 (Passe)"
                    }
                    bet_description = bet_descriptions[bet_type]
                    multiplier = "2:1"
                
                embed.add_field(
                    name="🎯 Ton pari",
                    value=f"{bet_description} - {bet_amount:,} PrissBucks",
                    inline=True
                )
                
                embed.add_field(
                    name="💰 Gains",
                    value=f"**+{winnings:,}** PrissBucks ({multiplier})",
                    inline=True
                )
                
                embed.add_field(
                    name="💳 Nouveau solde",
                    value=f"**{new_balance:,}** PrissBucks",
                    inline=True
                )
                
                # Log avec taxe cachée
                logger.info(f"Roulette WIN: {user} mise {bet_amount} sur {bet_type}, gagne {winnings} (taxe cachée: {hidden_tax})")
                
            else:
                # Défaite
                new_balance = await self.db.get_balance(user_id)
                
                embed = discord.Embed(
                    title="💔 Perdu à la roulette...",
                    description=f"La bille s'arrête sur **{winning_number}** {color_emoji} **{color_name}** !",
                    color=Colors.ERROR
                )
                
                # Détails du pari
                if bet_type.startswith("number_"):
                    bet_description = f"Numéro **{bet_type.split('_')[1]}**"
                else:
                    bet_descriptions = {
                        "red": "Rouge 🔴", "black": "Noir ⚫",
                        "even": "Pair", "odd": "Impair",
                        "low": "1-18 (Manque)", "high": "19-36 (Passe)"
                    }
                    bet_description = bet_descriptions[bet_type]
                
                embed.add_field(
                    name="🎯 Ton pari",
                    value=f"{bet_description} - {bet_amount:,} PrissBucks",
                    inline=True
                )
                
                embed.add_field(
                    name="💸 Perte",
                    value=f"**-{bet_amount:,}** PrissBucks",
                    inline=True
                )
                
                embed.add_field(
                    name="💳 Nouveau solde",
                    value=f"**{new_balance:,}** PrissBucks",
                    inline=True
                )
                
                logger.info(f"Roulette LOSS: {user} mise {bet_amount} sur {bet_type}, perd tout")

            # Animation de la roue (cosmétique)
            spin_animation = "🎰 " + " → ".join([
                f"{random.randint(0, 36)}" for _ in range(3)
            ]) + f" → **{winning_number}**"
            
            embed.add_field(
                name="🎲 Résultat du spin",
                value=spin_animation,
                inline=False
            )

            embed.set_footer(text="Cooldown: 5 secondes • Mise min: 10 PB • Bonne chance !")
            embed.set_thumbnail(url=user.display_avatar.url)
            
            await send_func(embed=embed)

        except Exception as e:
            logger.error(f"Erreur roulette {user_id}: {e}")
            embed = create_error_embed("Erreur", "Une erreur s'est produite lors du jeu.")
            await send_func(embed=embed)

    # ==================== COMMANDES D'INFORMATION ====================

    @commands.command(name='rouletteinfo', aliases=['roulinfo', 'casinoinfo'])
    async def roulette_info_cmd(self, ctx):
        """Affiche les informations sur la roulette"""
        embed = discord.Embed(
            title="🎰 Roulette Européenne",
            description="Teste ta chance sur la roulette du casino !",
            color=Colors.PREMIUM
        )
        
        embed.add_field(
            name="🎯 Types de paris",
            value="**Rouge/Noir** (2:1) - `red` ou `black`\n"
                  "**Pair/Impair** (2:1) - `even` ou `odd`\n"
                  "**1-18/19-36** (2:1) - `low` ou `high`\n"
                  "**Numéro spécifique** (36:1) - `0` à `36`",
            inline=False
        )
        
        embed.add_field(
            name="💰 Règles",
            value=f"• **Mise minimum:** {self.MIN_BET:,} PrissBucks\n"
                  "• **Mise maximum:** Illimitée\n"
                  "• **Cooldown:** 5 secondes\n"
                  "• **Zéro:** Fait perdre tous les paris extérieurs",
            inline=False
        )
        
        embed.add_field(
            name="🚀 Exemples",
            value=f"`/roulette red 100` - Mise 100 PB sur rouge\n"
                  f"`/roulette 7 50` - Mise 50 PB sur le numéro 7\n"
                  f"`/roulette even 200` - Mise 200 PB sur pair",
            inline=False
        )
        
        embed.add_field(
            name="🎲 Probabilités",
            value="**Rouge/Noir, Pair/Impair, 1-18/19-36:** 48.65%\n"
                  "**Numéro spécifique:** 2.70%\n"
                  "**Avantage maison:** 2.70% (zéro vert)",
            inline=False
        )
        
        embed.set_footer(text="Joue de manière responsable ! 🎰")
        await ctx.send(embed=embed)

    @commands.command(name='roulettestats', aliases=['roulstats'])
    async def roulette_stats_cmd(self, ctx, user: discord.Member = None):
        """Affiche les statistiques de roulette d'un utilisateur"""
        target = user or ctx.author
        
        embed = discord.Embed(
            title=f"📊 Stats Roulette de {target.display_name}",
            description="*Statistiques générales du casino*",
            color=Colors.INFO
        )
        
        try:
            balance = await self.db.get_balance(target.id)
            embed.add_field(
                name="💰 Solde actuel",
                value=f"**{balance:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="🎰 Comment jouer",
                value="Utilise `/roulette <pari> <mise>` pour tenter ta chance !",
                inline=False
            )
            
            embed.set_thumbnail(url=target.display_avatar.url)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur stats roulette: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Roulette(bot))
