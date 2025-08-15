import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import logging
from datetime import datetime, timezone

from config import Colors, Emojis, OWNER_ID
from utils.embeds import create_error_embed, create_success_embed

logger = logging.getLogger(__name__)

class RouletteEnhanced(commands.Cog):
    """Mini-jeu de roulette européenne avec animations et expérience addictive"""
    
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
        
        # Éléments visuels addictifs
        self.WHEEL_EMOJIS = ["🎰", "🎯", "🎲", "💫", "⭐", "✨", "🌟", "💥"]
        self.CONFETTI = ["🎉", "🎊", "🥳", "🍾", "💸", "💰", "🏆", "🎈"]
        self.SUSPENSE_EMOJIS = ["⏳", "🤔", "😬", "🤞", "🙏", "😰", "💀", "🔥"]
        
        # Système de streaks pour addiction
        self.user_streaks = {}
        self.user_sessions = {}
        
        # Dictionnaire pour gérer les cooldowns
        self.roulette_cooldowns = {}
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info(f"✅ Cog Roulette Enhanced initialisé")

    def _check_roulette_cooldown(self, user_id: int) -> float:
        """Vérifie et retourne le cooldown restant pour la roulette"""
        import time
        now = time.time()
        cooldown_duration = 3  # Réduit à 3 secondes pour plus d'addiction
        if user_id in self.roulette_cooldowns:
            elapsed = now - self.roulette_cooldowns[user_id]
            if elapsed < cooldown_duration:
                return cooldown_duration - elapsed
        self.roulette_cooldowns[user_id] = now
        return 0

    def get_number_color_enhanced(self, number: int) -> tuple:
        """Retourne la couleur, l'emoji et les effets visuels d'un numéro"""
        if number in self.RED_NUMBERS:
            return "Rouge", "🔴", "❤️‍🔥", "#ff0000"
        elif number in self.BLACK_NUMBERS:
            return "Noir", "⚫", "🖤", "#000000"
        else:  # 0
            return "Vert", "💚", "🍀", "#00ff00"

    def get_user_session_data(self, user_id: int) -> dict:
        """Récupère ou initialise les données de session d'un utilisateur"""
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {
                'games_played': 0,
                'total_bet': 0,
                'total_won': 0,
                'win_streak': 0,
                'loss_streak': 0,
                'biggest_win': 0,
                'session_start': datetime.now()
            }
        return self.user_sessions[user_id]

    def update_session_stats(self, user_id: int, bet_amount: int, winnings: int):
        """Met à jour les statistiques de session"""
        session = self.get_user_session_data(user_id)
        session['games_played'] += 1
        session['total_bet'] += bet_amount
        
        if winnings > 0:
            session['total_won'] += winnings
            session['win_streak'] += 1
            session['loss_streak'] = 0
            if winnings > session['biggest_win']:
                session['biggest_win'] = winnings
        else:
            session['win_streak'] = 0
            session['loss_streak'] += 1

    async def create_animated_spin_sequence(self, ctx_or_interaction, winning_number: int, is_slash=False):
        """Crée une séquence d'animation pour le spin de la roulette"""
        if is_slash:
            edit_func = ctx_or_interaction.edit_original_response
        else:
            # Pour les messages normaux, on va envoyer un nouveau message et l'éditer
            msg = await ctx_or_interaction.send("🎰 **La roulette tourne...**")
            edit_func = msg.edit

        # Phase 1: Anticipation
        anticipation_embed = discord.Embed(
            title="🎰 LA ROULETTE TOURNE...",
            description="```\n🎯 Préparation du spin...\n```",
            color=Colors.WARNING
        )
        anticipation_embed.add_field(
            name="🌪️ Status", 
            value="**La bille est lancée !** " + random.choice(self.SUSPENSE_EMOJIS), 
            inline=False
        )
        await edit_func(embed=anticipation_embed)
        await asyncio.sleep(1)

        # Phase 2: Animation des numéros qui défilent
        for i in range(4):
            fake_numbers = [random.randint(0, 36) for _ in range(5)]
            animation_text = " → ".join([f"**{n}**" for n in fake_numbers])
            
            spin_embed = discord.Embed(
                title="🎰 ROULETTE EN COURS...",
                description=f"```\n{animation_text} → ...\n```",
                color=Colors.INFO
            )
            
            speed_indicators = ["💨", "⚡", "🔥", "💥"]
            spin_embed.add_field(
                name=f"{speed_indicators[i]} Vitesse", 
                value=f"**{'Très rapide' if i < 2 else 'Ralentissement'}** {random.choice(self.WHEEL_EMOJIS)}", 
                inline=False
            )
            
            await edit_func(embed=spin_embed)
            await asyncio.sleep(0.8)

        # Phase 3: Ralentissement dramatique
        final_sequence = []
        for _ in range(3):
            final_sequence.append(str(random.randint(0, 36)))
        final_sequence.append(f"**{winning_number}**")
        
        dramatic_embed = discord.Embed(
            title="🎲 RÉSULTAT IMMINENT...",
            description=f"```\n{' → '.join(final_sequence)}\n```",
            color=Colors.WARNING
        )
        dramatic_embed.add_field(
            name="⏰ Final", 
            value="**La bille ralentit...** 😬", 
            inline=False
        )
        await edit_func(embed=dramatic_embed)
        await asyncio.sleep(1.5)

        return edit_func

    def create_result_embed(self, user, bet_type: str, bet_amount: int, winning_number: int, winnings: int, new_balance: int, session_data: dict) -> discord.Embed:
        """Crée un embed de résultat ultra-visuel et addictif"""
        color_name, color_emoji, heart_emoji, hex_color = self.get_number_color_enhanced(winning_number)
        
        # Embed de base
        if winnings > 0:
            # VICTOIRE - Design explosif
            embed = discord.Embed(
                title=f"🎉 JACKPOT ! VICTOIRE ÉPIQUE ! 🎉",
                description=f"## {random.choice(self.CONFETTI)} **NUMÉRO GAGNANT: {winning_number}** {color_emoji} {heart_emoji} {random.choice(self.CONFETTI)}",
                color=int(hex_color.replace('#', '0x'), 16) if hex_color != '#000000' else Colors.SUCCESS
            )
            
            # Animation textuelle de victoire
            victory_animation = "✨ " + " ✨ ".join([random.choice(self.CONFETTI) for _ in range(5)]) + " ✨"
            embed.add_field(name="🎊 CÉLÉBRATION", value=victory_animation, inline=False)
            
        else:
            # DÉFAITE - Design dramatique mais encourageant
            embed = discord.Embed(
                title="💔 Pas cette fois... MAIS NE RENONCE PAS !",
                description=f"## 🎲 **Numéro tiré: {winning_number}** {color_emoji} {heart_emoji}",
                color=Colors.ERROR
            )
            
            # Messages d'encouragement
            encouragements = [
                "🔥 **La prochaine est LA BONNE !**",
                "💪 **Tu étais si proche !**",
                "⚡ **Recommence, la chance tourne !**",
                "🎯 **Un vrai joueur n'abandonne jamais !**"
            ]
            embed.add_field(name="💬 Motivation", value=random.choice(encouragements), inline=False)

        # Détails du pari avec style
        if bet_type.startswith("number_"):
            bet_description = f"🎯 **Numéro {bet_type.split('_')[1]}**"
            multiplier = "**36:1** 🚀"
        else:
            bet_descriptions = {
                "red": "🔴 **Rouge**", "black": "⚫ **Noir**",
                "even": "⚪ **Pair**", "odd": "🔘 **Impair**", 
                "low": "📉 **1-18**", "high": "📈 **19-36**"
            }
            bet_description = bet_descriptions.get(bet_type, bet_type)
            multiplier = "**2:1** 💰"

        # Informations financières stylisées
        embed.add_field(
            name="🎯 TON PARI",
            value=f"{bet_description}\n💸 **{bet_amount:,}** PrissBucks",
            inline=True
        )
        
        if winnings > 0:
            profit = winnings - bet_amount
            embed.add_field(
                name="💰 GAINS MASSIFS",
                value=f"🎊 **+{winnings:,}** PrissBucks\n💎 Profit: **+{profit:,}** PB\n🔥 Ratio: {multiplier}",
                inline=True
            )
        else:
            embed.add_field(
                name="💸 PERTE",
                value=f"📉 **-{bet_amount:,}** PrissBucks\n⚡ **Retry pour la victoire !**",
                inline=True
            )

        embed.add_field(
            name="💳 SOLDE ACTUEL",
            value=f"{'💎' if new_balance > 1000 else '💰'} **{new_balance:,}** PrissBucks",
            inline=True
        )

        # Statistiques de session addictives
        win_rate = (session_data['total_won'] / max(session_data['total_bet'], 1)) * 100
        embed.add_field(
            name="📊 STATISTIQUES DE SESSION",
            value=f"🎮 **{session_data['games_played']}** parties jouées\n"
                  f"💰 **{session_data['total_won']:,}** PB gagnés\n"
                  f"📈 Taux de succès: **{win_rate:.1f}%**\n"
                  f"🔥 Streak: **{session_data['win_streak']}** victoires",
            inline=False
        )

        # Système de streaks pour encourager
        if session_data['win_streak'] >= 2:
            streak_bonus = f"🔥 **STREAK DE FEU !** {session_data['win_streak']} victoires consécutives ! 🔥"
            embed.add_field(name="⚡ STREAK BONUS", value=streak_bonus, inline=False)
        elif session_data['loss_streak'] >= 3:
            comeback_msg = f"💪 **{session_data['loss_streak']} défaites** - LA VICTOIRE APPROCHE ! 🎯"
            embed.add_field(name="🎯 COMEBACK TIME", value=comeback_msg, inline=False)

        # Footer incitatif
        motivational_footers = [
            "🎰 Rejoue dans 3 secondes ! • 💰 Mise min: 10 PB",
            "🔥 La chance peut tourner à tout moment ! • ⚡ Action rapide !",
            "💎 Plus tu joues, plus tu gagnes ! • 🎯 Vise la lune !",
            "🚀 Chaque spin peut être LE bon ! • 💰 Fortune t'attend !"
        ]
        embed.set_footer(text=random.choice(motivational_footers))
        embed.set_thumbnail(url=user.display_avatar.url)

        # Couleur dynamique selon les gains
        if winnings > bet_amount * 10:  # Gros gain
            embed.color = Colors.GOLD
        elif winnings > 0:
            embed.color = Colors.SUCCESS
        elif session_data['loss_streak'] >= 5:  # Encouragement après plusieurs pertes
            embed.color = Colors.WARNING
        else:
            embed.color = Colors.ERROR

        return embed

    # ==================== COMMANDES ROULETTE ENHANCED ====================

    @app_commands.command(name="roulette", description="🎰 Roulette ULTRA addictive ! Animations, streaks, victoires épiques !")
    @app_commands.describe(
        pari="Type de pari: red, black, even, odd, low (1-18), high (19-36), ou un numéro (0-36)",
        mise="Montant à miser en PrissBucks (minimum 10)"
    )
    async def roulette_slash(self, interaction: discord.Interaction, pari: str, mise: int):
        """Slash command pour jouer à la roulette enhanced"""
        await interaction.response.defer()
        await self._execute_roulette_enhanced(interaction, pari, mise, is_slash=True)

    @commands.command(name='roulette', aliases=['roul', 'casino', 'spin'])
    async def roulette_cmd(self, ctx, bet_type: str, bet_amount: int):
        """Joue à la roulette européenne avec expérience addictive"""
        await self._execute_roulette_enhanced(ctx, bet_type, bet_amount)

    async def _execute_roulette_enhanced(self, ctx_or_interaction, bet_type: str, bet_amount: int, is_slash=False):
        """Logique commune pour la roulette enhanced avec animations"""
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
                title=f"⏰ Patience... La roulette se prépare !",
                description=f"🔥 **{cooldown_remaining:.1f}s** avant le prochain spin épique !\n\n"
                           f"🎯 **Prépare ta stratégie !** 💰\n"
                           f"⚡ Plus tu attends, plus la tension monte !",
                color=Colors.WARNING
            )
            embed.add_field(
                name="🎰 Conseils pendant l'attente",
                value="• 💭 Réfléchis à ton prochain pari\n• 🎯 La patience fait les grands gagnants\n• 🔥 Prépare-toi pour la victoire !",
                inline=False
            )
            await send_func(embed=embed)
            return

        # Validations avec messages stylisés
        if bet_amount < self.MIN_BET:
            embed = discord.Embed(
                title="💰 Mise trop petite pour un grand gagnant !",
                description=f"🎯 **Mise minimum:** {self.MIN_BET:,} PrissBucks\n"
                           f"💎 **Ton potentiel:** ILLIMITÉ !\n\n"
                           f"💪 Augmente ta mise pour des gains MASSIFS ! 🚀",
                color=Colors.WARNING
            )
            await send_func(embed=embed)
            return

        try:
            current_balance = await self.db.get_balance(user_id)
            if current_balance < bet_amount:
                embed = discord.Embed(
                    title="💸 Solde insuffisant - MAIS NE RENONCE PAS !",
                    description=f"💰 **Ton solde:** {current_balance:,} PrissBucks\n"
                               f"🎯 **Mise souhaitée:** {bet_amount:,} PrissBucks\n\n"
                               f"💡 **Astuce:** Commence plus petit et monte progressivement !\n"
                               f"🔥 **Les petites victoires mènent aux JACKPOTS !**",
                    color=Colors.ERROR
                )
                # Suggestion de mise adaptée
                suggested_bet = min(current_balance, max(self.MIN_BET, current_balance // 2))
                if suggested_bet >= self.MIN_BET:
                    embed.add_field(
                        name="💡 SUGGESTION STRATÉGIQUE",
                        value=f"🎯 Essaie avec **{suggested_bet:,} PrissBucks** !\n⚡ Parfait pour démarrer ta série de victoires !",
                        inline=False
                    )
                await send_func(embed=embed)
                return

            # Normaliser et valider le type de pari
            bet_type = bet_type.lower()
            valid_bets = ["red", "black", "even", "odd", "low", "high"]
            
            if bet_type.isdigit():
                number = int(bet_type)
                if 0 <= number <= 36:
                    bet_type = f"number_{number}"
                else:
                    embed = discord.Embed(
                        title="🎯 Numéro invalide - Vise juste !",
                        description="🎰 **Numéros valides:** 0 à 36\n"
                                   "💎 **Récompense:** Jusqu'à 36x ta mise !\n\n"
                                   "🔥 **Exemples gagnants:**\n"
                                   "• `/roulette 7 100` - Le chiffre de la chance !\n"
                                   "• `/roulette 0 50` - Le jackpot vert ! 💚",
                        color=Colors.ERROR
                    )
                    await send_func(embed=embed)
                    return
            elif bet_type not in valid_bets:
                embed = discord.Embed(
                    title="🎲 Pari invalide - Choisis ta stratégie !",
                    description="🔥 **PARIS HAUTE RÉCOMPENSE (36:1) :**\n"
                               "🎯 **0-36** - Numéro spécifique = JACKPOT MASSIF !\n\n"
                               "💰 **PARIS STRATÉGIQUES (2:1) :**\n"
                               "🔴 **red** / ⚫ **black** - Rouge ou Noir\n"
                               "⚪ **even** / 🔘 **odd** - Pair ou Impair\n"
                               "📉 **low** / 📈 **high** - 1-18 ou 19-36",
                    color=Colors.WARNING
                )
                embed.add_field(
                    name="🚀 EXEMPLES DE VICTOIRES",
                    value=f"• `/roulette red {self.MIN_BET*5}` - Rouge = 2x ta mise !\n"
                          f"• `/roulette 21 {self.MIN_BET}` - Numéro = 36x ta mise ! 🤑",
                    inline=False
                )
                await send_func(embed=embed)
                return

            # Débiter la mise
            await self.db.update_balance(user_id, -bet_amount)

            # Faire tourner la roulette avec animation
            winning_number = random.randint(0, 36)
            
            # Animation du spin
            edit_func = await self.create_animated_spin_sequence(ctx_or_interaction, winning_number, is_slash)

            # Calculer les gains
            winnings = self.calculate_winnings(bet_type, bet_amount, winning_number)

            # Traitement des gains et taxes
            if winnings > 0:
                gross_winnings = 0
                if bet_type.startswith("number_"):
                    gross_winnings = bet_amount * 35
                else:
                    gross_winnings = bet_amount * 2
                
                hidden_tax = int(gross_winnings * self.TAX_RATE)
                await self.db.update_balance(user_id, winnings)
                await self.transfer_tax_to_owner(hidden_tax)

            # Récupérer le nouveau solde et les stats
            new_balance = await self.db.get_balance(user_id)
            session_data = self.get_user_session_data(user_id)
            self.update_session_stats(user_id, bet_amount, winnings)

            # Créer l'embed de résultat ultra-visuel
            result_embed = self.create_result_embed(
                user, bet_type, bet_amount, winning_number, 
                winnings, new_balance, session_data
            )

            # Envoyer le résultat final
            await edit_func(embed=result_embed)

            # Log amélioré
            status = "WIN" if winnings > 0 else "LOSS"
            logger.info(f"Roulette Enhanced {status}: {user} mise {bet_amount} sur {bet_type}, "
                       f"{'gagne ' + str(winnings) if winnings > 0 else 'perd tout'}")

        except Exception as e:
            logger.error(f"Erreur roulette enhanced {user_id}: {e}")
            embed = create_error_embed(
                "🔧 Erreur technique", 
                "⚡ Un problème temporaire ! Réessaie dans quelques secondes !\n🎯 Ta chance t'attend !"
            )
            await send_func(embed=embed)

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

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(RouletteEnhanced(bot))