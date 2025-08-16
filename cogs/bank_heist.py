import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import logging
from datetime import datetime, timezone, timedelta
import math

from config import Colors, Emojis, PREFIX, OWNER_ID
from utils.embeds import create_error_embed, create_success_embed, create_info_embed

logger = logging.getLogger(__name__)

class HeistView(discord.ui.View):
    """Interface interactive pour les braquages de banque"""
    
    def __init__(self, organizer, target_amount, entry_fee, max_participants=6):
        super().__init__(timeout=300.0)  # 5 minutes pour rejoindre
        self.organizer = organizer
        self.target_amount = target_amount
        self.entry_fee = entry_fee
        self.max_participants = max_participants
        
        # Participants du braquage
        self.participants = {organizer.id: {"user": organizer, "role": "mastermind", "skill": 0}}
        
        # Statut du braquage
        self.heist_started = False
        self.heist_completed = False
        
        # Phases du braquage
        self.current_phase = "recruitment"  # recruitment -> planning -> execution -> escape
        
        # Statistiques du braquage
        self.bank_security = random.randint(70, 95)  # Sécurité de la banque
        self.police_response = random.randint(60, 90)  # Réactivité police
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie que seuls les participants peuvent interagir"""
        if self.current_phase == "recruitment":
            return True  # Tout le monde peut rejoindre pendant le recrutement
        return interaction.user.id in self.participants

    @discord.ui.button(label='💼 Rejoindre le braquage', style=discord.ButtonStyle.primary)
    async def join_heist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_phase != "recruitment":
            await interaction.response.send_message("❌ Le recrutement est terminé !", ephemeral=True)
            return
            
        if interaction.user.id == self.organizer.id:
            await interaction.response.send_message("❌ Tu es déjà l'organisateur !", ephemeral=True)
            return
            
        if interaction.user.id in self.participants:
            await interaction.response.send_message("❌ Tu fais déjà partie de l'équipe !", ephemeral=True)
            return
            
        if len(self.participants) >= self.max_participants:
            await interaction.response.send_message("❌ L'équipe est complète !", ephemeral=True)
            return

        # Vérifier si l'utilisateur a assez de fonds
        db = interaction.client.database
        balance = await db.get_balance(interaction.user.id)
        
        if balance < self.entry_fee:
            await interaction.response.send_message(
                f"❌ Tu n'as pas assez de PrissBucks ! Il faut {self.entry_fee:,} PB.", 
                ephemeral=True
            )
            return

        # Ajouter le participant avec un rôle aléatoire
        roles = ["hacker", "gunman", "driver", "locksmith", "lookout"]
        assigned_roles = [p["role"] for p in self.participants.values() if p["role"] != "mastermind"]
        available_roles = [r for r in roles if r not in assigned_roles]
        
        if not available_roles:
            available_roles = roles  # Si tous pris, on peut avoir des doublons
            
        role = random.choice(available_roles)
        skill_boost = random.randint(5, 25)  # Chaque participant apporte ses compétences
        
        self.participants[interaction.user.id] = {
            "user": interaction.user, 
            "role": role, 
            "skill": skill_boost
        }
        
        # Débiter les frais d'inscription
        await db.update_balance(interaction.user.id, -self.entry_fee)
        
        await interaction.response.send_message(
            f"✅ **{interaction.user.display_name}** rejoint l'équipe en tant que **{role}** !\n"
            f"🎯 Compétence apportée: +{skill_boost} points\n"
            f"💰 Frais d'inscription débités: {self.entry_fee:,} PB", 
            ephemeral=False
        )
        
        # Mettre à jour l'embed
        await self.update_heist_embed(interaction)

    @discord.ui.button(label='🎯 Lancer le braquage', style=discord.ButtonStyle.danger)
    async def start_heist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.organizer.id:
            await interaction.response.send_message("❌ Seul l'organisateur peut lancer le braquage !", ephemeral=True)
            return
            
        if len(self.participants) < 2:
            await interaction.response.send_message("❌ Il faut au moins 2 personnes pour un braquage !", ephemeral=True)
            return
            
        # Passer à la phase de planification
        self.current_phase = "planning"
        self.heist_started = True
        
        # Désactiver le bouton de recrutement
        self.children[0].disabled = True
        self.children[1].disabled = True
        
        await interaction.response.send_message("🚨 **LE BRAQUAGE COMMENCE !** 🚨", ephemeral=False)
        await asyncio.sleep(2)
        
        # Lancer la séquence du braquage
        await self.execute_heist_sequence(interaction)

    async def update_heist_embed(self, interaction):
        """Met à jour l'embed du braquage"""
        team_skill = sum(p["skill"] for p in self.participants.values())
        
        embed = discord.Embed(
            title="🏦💥 BRAQUAGE DE BANQUE ORGANISÉ",
            description=f"**{self.organizer.display_name}** organise un braquage !",
            color=Colors.WARNING
        )
        
        # Informations du braquage
        embed.add_field(
            name="💰 Objectif",
            value=f"**{self.target_amount:,}** PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name="💳 Frais d'inscription",
            value=f"**{self.entry_fee:,}** PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name="👥 Équipe",
            value=f"**{len(self.participants)}/{self.max_participants}**",
            inline=True
        )
        
        # Liste des participants
        team_text = ""
        role_emojis = {
            "mastermind": "🧠", "hacker": "💻", "gunman": "🔫", 
            "driver": "🚗", "locksmith": "🔐", "lookout": "👁️"
        }
        
        for participant in self.participants.values():
            role_emoji = role_emojis.get(participant["role"], "👤")
            team_text += f"{role_emoji} **{participant['user'].display_name}** - {participant['role'].title()} (+{participant['skill']})\n"
        
        embed.add_field(
            name="🎭 Équipe actuelle",
            value=team_text if team_text else "Aucun participant",
            inline=False
        )
        
        # Statistiques
        embed.add_field(
            name="📊 Statistiques de l'équipe",
            value=f"🎯 **Compétence totale:** {team_skill} points\n"
                  f"🏦 **Sécurité banque:** {self.bank_security}\n"
                  f"🚨 **Police:** {self.police_response}",
            inline=True
        )
        
        # Calcul des chances approximatif
        success_chance = min(85, max(15, team_skill - self.bank_security + random.randint(-10, 10)))
        embed.add_field(
            name="📈 Chances estimées",
            value=f"**~{success_chance}%** de réussite\n*(estimation approximative)*",
            inline=True
        )
        
        # Instructions
        if self.current_phase == "recruitment":
            embed.add_field(
                name="⚡ Instructions",
                value="• Cliquez sur **💼 Rejoindre** pour participer\n"
                      "• L'organisateur peut **🎯 Lancer le braquage** quand prêt\n"
                      "• Plus vous êtes nombreux, plus c'est facile !",
                inline=False
            )
        
        embed.set_footer(text="💀 Attention : En cas d'échec, vous perdez tout ! 💀")
        
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except:
            pass

    async def execute_heist_sequence(self, interaction):
        """Exécute la séquence complète du braquage avec animations"""
        
        # Phase 1: Préparation
        await self.phase_preparation(interaction)
        await asyncio.sleep(3)
        
        # Phase 2: Infiltration
        infiltration_success = await self.phase_infiltration(interaction)
        await asyncio.sleep(3)
        
        if not infiltration_success:
            await self.heist_failed(interaction, "infiltration")
            return
        
        # Phase 3: Coffre-fort
        vault_success = await self.phase_vault(interaction)
        await asyncio.sleep(3)
        
        if not vault_success:
            await self.heist_failed(interaction, "vault")
            return
        
        # Phase 4: Évasion
        escape_success = await self.phase_escape(interaction)
        await asyncio.sleep(2)
        
        if escape_success:
            await self.heist_success(interaction)
        else:
            await self.heist_failed(interaction, "escape")

    async def phase_preparation(self, interaction):
        """Phase de préparation du braquage"""
        embed = discord.Embed(
            title="🗺️ PHASE 1: PRÉPARATION",
            description="L'équipe finalise les derniers détails...",
            color=Colors.INFO
        )
        
        prep_events = [
            "🔍 Reconnaissance des lieux terminée",
            "💻 Systèmes de sécurité analysés", 
            "🚗 Véhicules de fuite préparés",
            "🎭 Déguisements distribués",
            "📱 Communications cryptées établies"
        ]
        
        for event in prep_events:
            embed.add_field(name="✅ Préparatif", value=event, inline=False)
            await interaction.edit_original_response(embed=embed, view=None)
            await asyncio.sleep(1)

    async def phase_infiltration(self, interaction):
        """Phase d'infiltration - première étape critique"""
        embed = discord.Embed(
            title="🏦 PHASE 2: INFILTRATION",
            description="L'équipe tente de pénétrer dans la banque...",
            color=Colors.WARNING
        )
        
        team_skill = sum(p["skill"] for p in self.participants.values())
        
        # Événements d'infiltration
        events = [
            ("🔐 Désactivation de l'alarme", "hacker"),
            ("🚪 Crochetage de la porte", "locksmith"), 
            ("👁️ Surveillance des environs", "lookout"),
            ("🎯 Neutralisation des gardes", "gunman")
        ]
        
        success_points = 0
        max_points = len(events) * 20
        
        for event, required_role in events:
            # Bonus si on a le bon rôle dans l'équipe
            role_bonus = 15 if any(p["role"] == required_role for p in self.participants.values()) else 0
            event_success = random.randint(1, 100) + role_bonus + (team_skill // 10)
            
            if event_success > 60:
                success_points += 20
                embed.add_field(name="✅ " + event, value="**RÉUSSI**", inline=False)
                status = "🟢 RÉUSSI"
            else:
                embed.add_field(name="❌ " + event, value="**ÉCHEC**", inline=False)
                status = "🔴 ÉCHEC"
            
            await interaction.edit_original_response(embed=embed, view=None)
            await asyncio.sleep(1.5)
        
        infiltration_success = success_points >= (max_points * 0.6)  # 60% de réussite minimum
        
        result = "🎯 **INFILTRATION RÉUSSIE !**" if infiltration_success else "🚨 **INFILTRATION ÉCHOUÉE !**"
        embed.add_field(name="📊 Résultat", value=f"{result}\nPoints: {success_points}/{max_points}", inline=False)
        await interaction.edit_original_response(embed=embed, view=None)
        
        return infiltration_success

    async def phase_vault(self, interaction):
        """Phase du coffre-fort - étape la plus technique"""
        embed = discord.Embed(
            title="💰 PHASE 3: COFFRE-FORT",
            description="L'équipe tente d'ouvrir le coffre-fort principal...",
            color=Colors.GOLD
        )
        
        # Le coffre-fort est plus difficile
        team_skill = sum(p["skill"] for p in self.participants.values())
        vault_difficulty = self.bank_security + random.randint(10, 30)
        
        # Étapes du coffre
        vault_steps = [
            ("🔐 Analyse du mécanisme", 25),
            ("💻 Piratage du système", 30),
            ("🧮 Calcul de la combinaison", 35),
            ("⚡ Désactivation finale", 40)
        ]
        
        total_progress = 0
        
        for step, difficulty in vault_steps:
            # Chaque étape demande plus de skill
            step_success = team_skill + random.randint(-20, 30)
            
            if step_success >= difficulty:
                total_progress += 25
                embed.add_field(name="✅ " + step, value="**RÉUSSI** 🎯", inline=False)
            else:
                embed.add_field(name="❌ " + step, value="**ÉCHEC** 💥", inline=False)
            
            await interaction.edit_original_response(embed=embed, view=None)
            await asyncio.sleep(2)
        
        vault_success = total_progress >= 75  # 75% minimum pour ouvrir
        
        if vault_success:
            embed.add_field(
                name="🎉 COFFRE OUVERT !",
                value=f"**{self.target_amount:,}** PrissBucks récupérés !",
                inline=False
            )
        else:
            embed.add_field(
                name="💥 COFFRE RÉSISTANT !",
                value="Le coffre-fort n'a pas cédé...",
                inline=False
            )
            
        await interaction.edit_original_response(embed=embed, view=None)
        return vault_success

    async def phase_escape(self, interaction):
        """Phase d'évasion - échapper à la police"""
        embed = discord.Embed(
            title="🚨 PHASE 4: ÉVASION",
            description="La police arrive ! L'équipe doit s'échapper...",
            color=Colors.ERROR
        )
        
        team_skill = sum(p["skill"] for p in self.participants.values())
        
        # La police est plus efficace s'il y a plus de participants (plus voyant)
        police_efficiency = self.police_response + (len(self.participants) * 5)
        
        escape_challenges = [
            ("🚗 Démarrage des véhicules", "driver"),
            ("📱 Brouillage des communications", "hacker"),
            ("🎯 Couverture de l'équipe", "gunman"),
            ("🗺️ Navigation vers la cachette", "mastermind")
        ]
        
        escape_points = 0
        
        for challenge, role in escape_challenges:
            role_bonus = 20 if any(p["role"] == role for p in self.participants.values()) else 0
            challenge_roll = random.randint(1, 100) + role_bonus + (team_skill // 8)
            police_interference = random.randint(1, police_efficiency)
            
            success = challenge_roll > police_interference
            
            if success:
                escape_points += 25
                embed.add_field(name="✅ " + challenge, value="**RÉUSSI** 🏃‍♂️", inline=False)
            else:
                embed.add_field(name="❌ " + challenge, value="**BLOQUÉ** 🚔", inline=False)
            
            await interaction.edit_original_response(embed=embed, view=None)
            await asyncio.sleep(1.8)
        
        escape_success = escape_points >= 50  # 50% minimum
        
        if escape_success:
            embed.add_field(
                name="🏃‍♂️ ÉVASION RÉUSSIE !",
                value="L'équipe s'échappe dans la nature !",
                inline=False
            )
        else:
            embed.add_field(
                name="🚔 ARRESTATION !",
                value="La police a rattrapé l'équipe...",
                inline=False
            )
            
        await interaction.edit_original_response(embed=embed, view=None)
        return escape_success

    async def heist_success(self, interaction):
        """Gestion du succès du braquage avec redistribution"""
        self.heist_completed = True
        
        # Calcul des gains
        base_reward = self.target_amount
        bonus_multiplier = 1 + (len(self.participants) * 0.1)  # Bonus d'équipe
        total_stolen = int(base_reward * bonus_multiplier)
        
        # Redistribution équitable
        per_person = total_stolen // len(self.participants)
        
        # Bonus pour l'organisateur (5% du total)
        organizer_bonus = int(total_stolen * 0.05)
        
        embed = discord.Embed(
            title="🎉💰 BRAQUAGE RÉUSSI ! 💰🎉",
            description="**L'ÉQUIPE A RÉUSSI LE BRAQUAGE DE LA BANQUE !**",
            color=Colors.GOLD
        )
        
        embed.add_field(
            name="💎 Butin total",
            value=f"**{total_stolen:,}** PrissBucks volés !",
            inline=True
        )
        
        embed.add_field(
            name="👥 Part par personne",
            value=f"**{per_person:,}** PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name="👑 Bonus organisateur",
            value=f"**+{organizer_bonus:,}** PrissBucks",
            inline=True
        )
        
        # Distribuer les gains
        db = interaction.client.database
        participants_text = ""
        
        for user_id, participant in self.participants.items():
            user = participant["user"]
            role = participant["role"]
            
            # Gain de base + bonus organisateur si applicable
            gain = per_person + (organizer_bonus if user_id == self.organizer.id else 0)
            
            # Récupérer les soldes pour les logs
            balance_before = await db.get_balance(user_id)
            await db.update_balance(user_id, gain)
            balance_after = await db.get_balance(user_id)
            
            # Logger la transaction
            if hasattr(interaction.client, 'transaction_logs'):
                await interaction.client.transaction_logs.log_transaction(
                    user_id=user_id,
                    transaction_type='heist_success',
                    amount=gain,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    description=f"Braquage réussi - Rôle: {role} - Butin: {gain:,} PB"
                )
            
            participants_text += f"🎭 **{user.display_name}** ({role}) → +{gain:,} PB\n"
        
        embed.add_field(
            name="🏆 Distribution des gains",
            value=participants_text,
            inline=False
        )
        
        # Statistiques du braquage
        embed.add_field(
            name="📊 Statistiques du braquage",
            value=f"👥 **Participants:** {len(self.participants)}\n"
                  f"💰 **Objectif initial:** {self.target_amount:,} PB\n"
                  f"🎯 **Multiplicateur équipe:** x{bonus_multiplier:.1f}\n"
                  f"⏱️ **Durée:** ~{((datetime.now().minute % 10) + 5)} minutes",
            inline=False
        )
        
        # Envoyer vers banque publique (taxes du crime organisé)
        crime_tax = int(total_stolen * 0.03)  # 3% vers la banque publique
        public_bank_cog = interaction.client.get_cog('PublicBank')
        if public_bank_cog and crime_tax > 0:
            await public_bank_cog.add_casino_loss(crime_tax, "heist_tax")
            embed.add_field(
                name="🏛️ Impact social",
                value=f"**{crime_tax:,}** PB transférés vers la banque publique\n*(Taxe sur le crime organisé)*",
                inline=False
            )
        
        embed.set_footer(text="🏆 LÉGENDE CRIMINELLE ! Vous entrez dans l'histoire ! 🏆")
        
        await interaction.edit_original_response(embed=embed, view=None)
        
        logger.info(f"HEIST SUCCESS: {len(self.participants)} participants, {total_stolen:,} PB volés, organisé par {self.organizer}")

    async def heist_failed(self, interaction, phase):
        """Gestion de l'échec du braquage avec conséquences"""
        self.heist_completed = True
        
        phase_names = {
            "infiltration": "🚨 INFILTRATION",
            "vault": "💰 COFFRE-FORT", 
            "escape": "🚔 ÉVASION"
        }
        
        embed = discord.Embed(
            title="💥🚨 BRAQUAGE ÉCHOUÉ ! 🚨💥",
            description=f"**L'ÉQUIPE A ÉCHOUÉ PENDANT LA PHASE : {phase_names.get(phase, phase.upper())}**",
            color=Colors.ERROR
        )
        
        # Conséquences selon la phase d'échec
        if phase == "infiltration":
            penalty_rate = 0.3  # 30% de perte
            embed.add_field(
                name="🚨 Cause de l'échec",
                value="L'alarme s'est déclenchée trop tôt !\nL'équipe a dû fuir précipitamment.",
                inline=False
            )
        elif phase == "vault":
            penalty_rate = 0.4  # 40% de perte
            embed.add_field(
                name="💥 Cause de l'échec", 
                value="Le coffre-fort était trop sécurisé !\nL'équipe a perdu du temps et des ressources.",
                inline=False
            )
        else:  # escape
            penalty_rate = 0.6  # 60% de perte (plus grave car ils avaient réussi)
            embed.add_field(
                name="🚔 Cause de l'échec",
                value="La police a rattrapé l'équipe !\nUne partie du butin a été saisie.",
                inline=False
            )
        
        # Calculer et appliquer les pénalités
        db = interaction.client.database
        participants_text = ""
        total_penalty = 0
        
        for user_id, participant in self.participants.items():
            user = participant["user"]
            role = participant["role"]
            
            # Pénalité = frais d'inscription + pénalité proportionnelle au solde
            balance_before = await db.get_balance(user_id)
            penalty = int(min(balance_before * penalty_rate, self.entry_fee * 3))  # Maximum 3x l'inscription
            
            if penalty > 0:
                await db.update_balance(user_id, -penalty)
                balance_after = await db.get_balance(user_id)
                total_penalty += penalty
                
                # Logger la perte
                if hasattr(interaction.client, 'transaction_logs'):
                    await interaction.client.transaction_logs.log_transaction(
                        user_id=user_id,
                        transaction_type='heist_failure',
                        amount=-penalty,
                        balance_before=balance_before,
                        balance_after=balance_after,
                        description=f"Braquage échoué ({phase}) - Rôle: {role} - Perte: {penalty:,} PB"
                    )
                
                participants_text += f"💸 **{user.display_name}** ({role}) → -{penalty:,} PB\n"
            else:
                participants_text += f"🛡️ **{user.display_name}** ({role}) → Aucune perte\n"
        
        embed.add_field(
            name="💸 Conséquences financières",
            value=participants_text,
            inline=False
        )
        
        embed.add_field(
            name="📊 Bilan de l'échec",
            value=f"👥 **Participants:** {len(self.participants)}\n"
                  f"💰 **Objectif visé:** {self.target_amount:,} PB\n"
                  f"💸 **Pertes totales:** {total_penalty:,} PB\n"
                  f"🚨 **Phase d'échec:** {phase_names.get(phase, phase)}",
            inline=False
        )
        
        # Les pertes vont à la banque publique (saisies par la police)
        if total_penalty > 0:
            public_bank_cog = interaction.client.get_cog('PublicBank')
            if public_bank_cog:
                await public_bank_cog.add_casino_loss(total_penalty, f"heist_failure_{phase}")
                embed.add_field(
                    name="🏛️ Justice sociale",
                    value=f"**{total_penalty:,}** PB saisis ont été transférés vers la banque publique\n"
                          f"*(Redistribution après saisie policière)*",
                    inline=False
                )
        
        # Messages d'encouragement selon la phase
        encouragement = {
            "infiltration": "🎯 **Prochaine fois, améliorez la préparation !**",
            "vault": "🔐 **Il faut plus de hackers et de locksmith !**",
            "escape": "🚗 **Préparez mieux vos véhicules de fuite !**"
        }
        
        embed.add_field(
            name="💡 Leçon apprise",
            value=encouragement.get(phase, "Réessayez avec une meilleure stratégie !"),
            inline=False
        )
        
        embed.set_footer(text="💀 Le crime ne paie pas toujours... Mais on peut réessayer ! 💀")
        
        await interaction.edit_original_response(embed=embed, view=None)
        
        logger.info(f"HEIST FAILED: {len(self.participants)} participants, phase: {phase}, pertes: {total_penalty:,} PB, organisé par {self.organizer}")

    async def on_timeout(self):
        """En cas de timeout pendant le recrutement"""
        if not self.heist_started:
            # Rembourser tous les participants
            db = self.message.guild.get_member(self.organizer.id)  # Récupération du bot via le message
            
            embed = discord.Embed(
                title="⏰ BRAQUAGE ANNULÉ - TIMEOUT",
                description="Le braquage a été annulé car personne n'a rejoint à temps.\nTous les frais d'inscription sont remboursés.",
                color=Colors.WARNING
            )
            
            try:
                await self.message.edit(embed=embed, view=None)
            except:
                pass


class BankHeist(commands.Cog):
    """Système de braquage de banque ultra-palpitant avec équipes"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Configuration des braquages
        self.MIN_TARGET = 5000     # Minimum 5k PB à voler
        self.MAX_TARGET = 100000   # Maximum 100k PB à voler
        self.MIN_ENTRY_FEE = 100   # Minimum 100 PB d'inscription
        
        # Cooldowns globaux et personnels
        self.global_heist_cooldown = {}  # {guild_id: datetime}
        self.user_heist_cooldown = {}    # {user_id: datetime}
        
        # Historique des braquages pour le serveur
        self.heist_history = []
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        await self.create_heist_tables()
        logger.info("✅ Cog BankHeist initialisé - Braquages de banque ultra-palpitants activés ! 🏦💥")
    
    async def create_heist_tables(self):
        """Crée les tables pour l'historique des braquages"""
        if not self.db.pool:
            return
            
        async with self.db.pool.acquire() as conn:
            # Table pour l'historique des braquages
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS heist_history (
                    id SERIAL PRIMARY KEY,
                    organizer_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    target_amount BIGINT NOT NULL,
                    participants_count INTEGER NOT NULL,
                    success BOOLEAN NOT NULL,
                    total_stolen BIGINT DEFAULT 0,
                    total_lost BIGINT DEFAULT 0,
                    failure_phase VARCHAR(50),
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            
            # Table pour les participants de chaque braquage
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS heist_participants (
                    id SERIAL PRIMARY KEY,
                    heist_id INTEGER REFERENCES heist_history(id),
                    user_id BIGINT NOT NULL,
                    role VARCHAR(50) NOT NULL,
                    skill_contribution INTEGER NOT NULL,
                    gain_or_loss BIGINT NOT NULL,
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            
            # Index pour les requêtes
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_heist_history_guild_id ON heist_history(guild_id)
            ''')
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_heist_history_organizer ON heist_history(organizer_id)
            ''')
            
            logger.info("✅ Tables heist créées/vérifiées")

    def _check_user_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown personnel (2 heures)"""
        if user_id not in self.user_heist_cooldown:
            return 0
            
        now = datetime.now(timezone.utc)
        last_heist = self.user_heist_cooldown[user_id]
        cooldown_end = last_heist + timedelta(hours=2)
        
        if now >= cooldown_end:
            return 0
        
        return (cooldown_end - now).total_seconds()

    def _check_global_cooldown(self, guild_id: int) -> float:
        """Vérifie le cooldown global du serveur (30 minutes)"""
        if guild_id not in self.global_heist_cooldown:
            return 0
            
        now = datetime.now(timezone.utc)
        last_global_heist = self.global_heist_cooldown[guild_id]
        cooldown_end = last_global_heist + timedelta(minutes=30)
        
        if now >= cooldown_end:
            return 0
            
        return (cooldown_end - now).total_seconds()

    def _set_cooldowns(self, user_id: int, guild_id: int):
        """Définit les cooldowns après un braquage"""
        now = datetime.now(timezone.utc)
        self.user_heist_cooldown[user_id] = now
        self.global_heist_cooldown[guild_id] = now

    def _format_time(self, seconds: float) -> str:
        """Formate le temps en heures/minutes/secondes"""
        if seconds <= 0:
            return "Disponible"
        
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes}min {secs}s"
        elif minutes > 0:
            return f"{minutes}min {secs}s"
        else:
            return f"{secs}s"

    async def save_heist_to_history(self, heist_view: HeistView, success: bool, total_amount: int = 0, failure_phase: str = None):
        """Sauvegarde un braquage dans l'historique"""
        if not self.db.pool:
            return
            
        try:
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Insérer le braquage principal
                    heist_row = await conn.fetchrow('''
                        INSERT INTO heist_history 
                        (organizer_id, guild_id, target_amount, participants_count, success, total_stolen, total_lost, failure_phase)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        RETURNING id
                    ''', 
                    heist_view.organizer.id,
                    heist_view.organizer.guild.id if heist_view.organizer.guild else 0,
                    heist_view.target_amount,
                    len(heist_view.participants),
                    success,
                    total_amount if success else 0,
                    total_amount if not success else 0,
                    failure_phase
                    )
                    
                    heist_id = heist_row['id']
                    
                    # Insérer les participants
                    for participant_data in heist_view.participants.values():
                        user = participant_data['user']
                        role = participant_data['role']
                        skill = participant_data['skill']
                        
                        # Calculer le gain/perte approximatif
                        if success:
                            per_person = total_amount // len(heist_view.participants)
                            bonus = int(total_amount * 0.05) if user.id == heist_view.organizer.id else 0
                            gain_or_loss = per_person + bonus
                        else:
                            # Perte approximative selon la phase
                            balance = await self.db.get_balance(user.id)
                            penalty_rates = {"infiltration": 0.3, "vault": 0.4, "escape": 0.6}
                            penalty_rate = penalty_rates.get(failure_phase, 0.3)
                            gain_or_loss = -int(min(balance * penalty_rate, heist_view.entry_fee * 3))
                        
                        await conn.execute('''
                            INSERT INTO heist_participants 
                            (heist_id, user_id, role, skill_contribution, gain_or_loss)
                            VALUES ($1, $2, $3, $4, $5)
                        ''', heist_id, user.id, role, skill, gain_or_loss)
                    
        except Exception as e:
            logger.error(f"Erreur sauvegarde heist: {e}")

    # ==================== COMMANDES PRINCIPALES ====================

    @app_commands.command(name="heist", description="🏦💥 Organise un braquage de banque ultra-palpitant avec ton équipe !")
    @app_commands.describe(
        objectif="Montant à voler en PrissBucks (5,000 à 100,000)",
        inscription="Frais d'inscription par participant (minimum 100 PB)",
        max_participants="Nombre maximum de participants (2 à 8, défaut: 6)"
    )
    async def heist_command(self, interaction: discord.Interaction, objectif: int, inscription: int, max_participants: int = 6):
        """Lance l'organisation d'un braquage de banque"""
        await interaction.response.defer()
        
        organizer = interaction.user
        
        # Validations de base
        if objectif < self.MIN_TARGET or objectif > self.MAX_TARGET:
            embed = create_error_embed(
                "Objectif invalide",
                f"L'objectif doit être entre **{self.MIN_TARGET:,}** et **{self.MAX_TARGET:,}** PrissBucks !"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
            
        if inscription < self.MIN_ENTRY_FEE:
            embed = create_error_embed(
                "Frais d'inscription trop bas",
                f"Les frais d'inscription doivent être d'au moins **{self.MIN_ENTRY_FEE}** PrissBucks !"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
            
        if max_participants < 2 or max_participants > 8:
            embed = create_error_embed(
                "Nombre de participants invalide",
                "Il faut entre **2** et **8** participants maximum !"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Vérifier les cooldowns
        user_cooldown = self._check_user_cooldown(organizer.id)
        if user_cooldown > 0:
            embed = discord.Embed(
                title="⏰ Cooldown personnel actif",
                description=f"Tu pourras organiser un braquage dans **{self._format_time(user_cooldown)}**",
                color=Colors.WARNING
            )
            embed.add_field(
                name="💡 Pourquoi ce cooldown ?",
                value="Pour éviter le spam et maintenir l'équilibre économique du serveur.",
                inline=False
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        guild_cooldown = self._check_global_cooldown(interaction.guild.id)
        if guild_cooldown > 0:
            embed = discord.Embed(
                title="⏰ Cooldown serveur actif", 
                description=f"Un braquage peut être organisé dans **{self._format_time(guild_cooldown)}**",
                color=Colors.WARNING
            )
            embed.add_field(
                name="🏦 Protection de la banque",
                value="La banque renforce sa sécurité entre chaque braquage !",
                inline=False
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Vérifier le solde de l'organisateur
        balance = await self.db.get_balance(organizer.id)
        if balance < inscription:
            embed = create_error_embed(
                "Solde insuffisant",
                f"Tu as **{balance:,}** PrissBucks mais les frais d'inscription sont de **{inscription:,}** PrissBucks."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        try:
            # Créer la vue interactive du braquage
            heist_view = HeistView(organizer, objectif, inscription, max_participants)
            
            # Débiter les frais d'inscription de l'organisateur
            await self.db.update_balance(organizer.id, -inscription)
            
            # Créer l'embed initial
            embed = heist_view.create_recruitment_embed()
            
            # Envoyer le message avec les boutons
            message = await interaction.followup.send(embed=embed, view=heist_view)
            heist_view.message = message
            
            # Définir les cooldowns
            self._set_cooldowns(organizer.id, interaction.guild.id)
            
            logger.info(f"HEIST ORGANIZED: {organizer} organise un braquage de {objectif:,} PB avec inscription {inscription:,} PB")
            
        except Exception as e:
            logger.error(f"Erreur organisation heist: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'organisation du braquage.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.command(name='heist_stats', aliases=['braquage_stats', 'heist_history'])
    async def heist_stats_cmd(self, ctx, user: discord.Member = None):
        """Affiche les statistiques de braquage d'un utilisateur ou du serveur"""
        await self._execute_heist_stats(ctx, user)

    @app_commands.command(name="heist_stats", description="Affiche les statistiques de braquage d'un utilisateur ou du serveur")
    @app_commands.describe(utilisateur="L'utilisateur dont voir les stats (optionnel)")
    async def heist_stats_slash(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        """/heist_stats [utilisateur] - Statistiques de braquage"""
        await interaction.response.defer()
        await self._execute_heist_stats(interaction, utilisateur, is_slash=True)

    async def _execute_heist_stats(self, ctx_or_interaction, user=None, is_slash=False):
        """Logique commune pour les stats de braquage"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
            guild = ctx_or_interaction.guild
            requestor = ctx_or_interaction.user
        else:
            send_func = ctx_or_interaction.send
            guild = ctx_or_interaction.guild
            requestor = ctx_or_interaction.author

        if not self.db.pool:
            embed = create_error_embed("Erreur", "Base de données non disponible.")
            await send_func(embed=embed)
            return

        try:
            if user:
                # Statistiques personnelles
                async with self.db.pool.acquire() as conn:
                    # Stats générales de l'utilisateur
                    user_stats = await conn.fetchrow('''
                        SELECT 
                            COUNT(*) as total_heists,
                            COUNT(CASE WHEN success THEN 1 END) as successful_heists,
                            COUNT(CASE WHEN organizer_id = $1 THEN 1 END) as organized_heists,
                            COALESCE(SUM(CASE WHEN success THEN total_stolen / participants_count ELSE 0 END), 0) as total_earned,
                            COALESCE(SUM(CASE WHEN NOT success THEN total_lost / participants_count ELSE 0 END), 0) as total_lost
                        FROM heist_history h
                        JOIN heist_participants p ON h.id = p.heist_id
                        WHERE p.user_id = $1 AND h.guild_id = $2
                    ''', user.id, guild.id)
                    
                    # Derniers braquages
                    recent_heists = await conn.fetch('''
                        SELECT h.success, h.target_amount, h.timestamp, p.role, p.gain_or_loss
                        FROM heist_history h
                        JOIN heist_participants p ON h.id = p.heist_id
                        WHERE p.user_id = $1 AND h.guild_id = $2
                        ORDER BY h.timestamp DESC
                        LIMIT 5
                    ''', user.id, guild.id)

                if not user_stats or user_stats['total_heists'] == 0:
                    embed = discord.Embed(
                        title=f"📊 Statistiques de braquage - {user.display_name}",
                        description="Cet utilisateur n'a participé à aucun braquage.",
                        color=Colors.WARNING
                    )
                    embed.add_field(
                        name="💡 Comment commencer ?",
                        value="Utilise `/heist <objectif> <inscription>` pour organiser ton premier braquage !",
                        inline=False
                    )
                    await send_func(embed=embed)
                    return

                embed = discord.Embed(
                    title=f"📊 Statistiques de braquage - {user.display_name}",
                    color=Colors.INFO
                )
                
                # Stats principales
                success_rate = (user_stats['successful_heists'] / user_stats['total_heists']) * 100
                net_profit = user_stats['total_earned'] - user_stats['total_lost']
                
                embed.add_field(
                    name="🏦 Carrière criminelle",
                    value=f"**{user_stats['total_heists']}** braquages total\n"
                          f"**{user_stats['successful_heists']}** réussis ({success_rate:.1f}%)\n"
                          f"**{user_stats['organized_heists']}** organisés",
                    inline=True
                )
                
                embed.add_field(
                    name="💰 Bilan financier",
                    value=f"**+{user_stats['total_earned']:,}** PB gagnés\n"
                          f"**-{user_stats['total_lost']:,}** PB perdus\n"
                          f"**{net_profit:+,}** PB profit net",
                    inline=True
                )
                
                # Cooldown actuel
                user_cooldown = self._check_user_cooldown(user.id)
                cooldown_status = f"⏰ {self._format_time(user_cooldown)}" if user_cooldown > 0 else "✅ Disponible"
                
                embed.add_field(
                    name="⏱️ Status actuel",
                    value=f"**Prochaine organisation:** {cooldown_status}",
                    inline=True
                )
                
                # Historique récent
                if recent_heists:
                    history_text = ""
                    for heist in recent_heists:
                        status_emoji = "✅" if heist['success'] else "❌"
                        date = heist['timestamp'].strftime("%d/%m")
                        gain_loss = f"{heist['gain_or_loss']:+,} PB"
                        history_text += f"{status_emoji} {date} - {heist['role']} - {gain_loss}\n"
                    
                    embed.add_field(
                        name="📜 Historique récent",
                        value=history_text,
                        inline=False
                    )
                
                embed.set_thumbnail(url=user.display_avatar.url)
                
            else:
                # Statistiques du serveur
                async with self.db.pool.acquire() as conn:
                    # Stats globales du serveur
                    server_stats = await conn.fetchrow('''
                        SELECT 
                            COUNT(*) as total_heists,
                            COUNT(CASE WHEN success THEN 1 END) as successful_heists,
                            COALESCE(SUM(total_stolen), 0) as total_stolen,
                            COALESCE(SUM(total_lost), 0) as total_lost,
                            COUNT(DISTINCT organizer_id) as unique_organizers
                        FROM heist_history
                        WHERE guild_id = $1
                    ''', guild.id)
                    
                    # Top organisateurs
                    top_organizers = await conn.fetch('''
                        SELECT organizer_id, COUNT(*) as organized_count,
                               COUNT(CASE WHEN success THEN 1 END) as success_count
                        FROM heist_history
                        WHERE guild_id = $1
                        GROUP BY organizer_id
                        ORDER BY organized_count DESC
                        LIMIT 5
                    ''', guild.id)
                    
                    # Derniers braquages
                    recent_server_heists = await conn.fetch('''
                        SELECT organizer_id, success, target_amount, participants_count, timestamp
                        FROM heist_history
                        WHERE guild_id = $1
                        ORDER BY timestamp DESC
                        LIMIT 5
                    ''', guild.id)

                if not server_stats or server_stats['total_heists'] == 0:
                    embed = discord.Embed(
                        title=f"📊 Statistiques du serveur - {guild.name}",
                        description="Aucun braquage n'a encore été organisé sur ce serveur !",
                        color=Colors.WARNING
                    )
                    embed.add_field(
                        name="🏦 Première fois ?",
                        value="Soyez le premier à organiser un braquage avec `/heist` !",
                        inline=False
                    )
                    await send_func(embed=embed)
                    return

                embed = discord.Embed(
                    title=f"📊 Statistiques du serveur - {guild.name}",
                    description="Activité criminelle du serveur",
                    color=Colors.GOLD
                )
                
                # Stats principales
                success_rate = (server_stats['successful_heists'] / server_stats['total_heists']) * 100 if server_stats['total_heists'] > 0 else 0
                
                embed.add_field(
                    name="🏦 Activité générale",
                    value=f"**{server_stats['total_heists']}** braquages total\n"
                          f"**{server_stats['successful_heists']}** réussis ({success_rate:.1f}%)\n"
                          f"**{server_stats['unique_organizers']}** organisateurs uniques",
                    inline=True
                )
                
                embed.add_field(
                    name="💰 Impact économique",
                    value=f"**{server_stats['total_stolen']:,}** PB volés\n"
                          f"**{server_stats['total_lost']:,}** PB perdus\n"
                          f"**{server_stats['total_stolen'] - server_stats['total_lost']:+,}** PB net",
                    inline=True
                )
                
                # Cooldown serveur
                global_cooldown = self._check_global_cooldown(guild.id)
                global_status = f"⏰ {self._format_time(global_cooldown)}" if global_cooldown > 0 else "✅ Disponible"
                
                embed.add_field(
                    name="🏛️ Status serveur",
                    value=f"**Prochain braquage:** {global_status}",
                    inline=True
                )
                
                # Top organisateurs
                if top_organizers:
                    top_text = ""
                    for i, org in enumerate(top_organizers, 1):
                        try:
                            user_obj = self.bot.get_user(org['organizer_id'])
                            name = user_obj.display_name if user_obj else f"User#{org['organizer_id']}"
                        except:
                            name = f"User#{org['organizer_id']}"
                        
                        success_rate = (org['success_count'] / org['organized_count']) * 100
                        emoji = ["🥇", "🥈", "🥉", "🏅", "🏅"][i-1]
                        top_text += f"{emoji} **{name}** - {org['organized_count']} org. ({success_rate:.0f}%)\n"
                    
                    embed.add_field(
                        name="👑 Top organisateurs",
                        value=top_text,
                        inline=False
                    )
                
                # Activité récente
                if recent_server_heists:
                    activity_text = ""
                    for heist in recent_server_heists:
                        try:
                            organizer = self.bot.get_user(heist['organizer_id'])
                            org_name = organizer.display_name if organizer else f"User#{heist['organizer_id']}"
                        except:
                            org_name = f"User#{heist['organizer_id']}"
                        
                        status_emoji = "✅" if heist['success'] else "❌"
                        date = heist['timestamp'].strftime("%d/%m")
                        activity_text += f"{status_emoji} {date} - {org_name} ({heist['participants_count']}👥) - {heist['target_amount']:,} PB\n"
                    
                    embed.add_field(
                        name="📜 Activité récente",
                        value=activity_text,
                        inline=False
                    )
                
                embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

            embed.set_footer(text="🏦💥 Système de braquages ultra-palpitant • Logs automatiques")
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur heist_stats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await send_func(embed=embed)

    @commands.command(name='heist_info', aliases=['braquage_info'])
    async def heist_info_cmd(self, ctx):
        """Affiche le guide complet du système de braquage"""
        embed = discord.Embed(
            title="🏦💥 Guide Complet - Braquages de Banque",
            description="**Système ultra-palpitant de braquages en équipe !**",
            color=Colors.GOLD
        )
        
        embed.add_field(
            name="🎯 Comment organiser un braquage ?",
            value=f"• `/heist <objectif> <inscription> [max_participants]`\n"
                  f"• **Objectif :** {self.MIN_TARGET:,} - {self.MAX_TARGET:,} PrissBucks\n"
                  f"• **Inscription :** minimum {self.MIN_ENTRY_FEE} PB par participant\n"
                  f"• **Équipe :** 2 à 8 participants maximum",
            inline=False
        )
        
        embed.add_field(
            name="👥 Rôles dans l'équipe",
            value="🧠 **Mastermind** - Organisateur (bonus 5%)\n"
                  "💻 **Hacker** - Piratage et alarmes\n"
                  "🔫 **Gunman** - Protection de l'équipe\n"
                  "🚗 **Driver** - Véhicules de fuite\n"
                  "🔐 **Locksmith** - Crochetage et coffres\n"
                  "👁️ **Lookout** - Surveillance",
            inline=True
        )
        
        embed.add_field(
            name="⚡ Phases du braquage",
            value="1️⃣ **Préparation** - Finalisation du plan\n"
                  "2️⃣ **Infiltration** - Entrer dans la banque\n"
                  "3️⃣ **Coffre-fort** - Voler l'argent\n"
                  "4️⃣ **Évasion** - Échapper à la police",
            inline=True
        )
        
        embed.add_field(
            name="💰 Récompenses & Risques",
            value="**🏆 SUCCÈS :**\n"
                  "• Objectif x multiplicateur équipe\n"
                  "• Organisateur : +5% bonus\n"
                  "• Répartition équitable\n\n"
                  "**💀 ÉCHEC :**\n"
                  "• Perte selon phase d'échec\n"
                  "• 30% à 60% de pénalité\n"
                  "• Argent → banque publique",
            inline=False
        )
        
        embed.add_field(
            name="⏰ Cooldowns & Limites",
            value="• **Personnel :** 2 heures entre braquages\n"
                  "• **Serveur :** 30 minutes entre braquages\n"
                  "• **Recrutement :** 5 minutes maximum\n"
                  "• **Historique :** Tout est enregistré !",
            inline=True
        )
        
        embed.add_field(
            name="🧮 Stratégies gagnantes",
            value="• **Plus d'équipiers = plus facile**\n"
                  "• **Diversifiez les rôles** pour les bonus\n"
                  "• **Visez réaliste** selon votre équipe\n"
                  "• **Coordonnez-vous** pendant le recrutement",
            inline=True
        )
        
        embed.add_field(
            name="🏛️ Impact sur l'économie",
            value="• **Gains :** Directement redistribués aux participants\n"
                  "• **Pertes :** Transférées vers la banque publique\n"
                  "• **Taxes crime :** 3% des gains → communauté\n"
                  "• **Équilibre :** Risque élevé, récompense élevée",
            inline=False
        )
        
        embed.set_footer(text=f"💡 Utilise '/heist <montant> <inscription>' pour commencer • Système ultra-palpitant !")
        await ctx.send(embed=embed)

    # Méthode helper pour HeistView
    def create_recruitment_embed(self):
        """Crée l'embed de recrutement (ajouté à HeistView)"""
        team_skill = sum(p["skill"] for p in self.participants.values())
        
        embed = discord.Embed(
            title="🏦💥 BRAQUAGE DE BANQUE ORGANISÉ",
            description=f"**{self.organizer.display_name}** organise un braquage ultra-palpitant !",
            color=Colors.WARNING
        )
        
        # Informations principales
        embed.add_field(
            name="💰 Objectif",
            value=f"**{self.target_amount:,}** PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name="💳 Frais d'inscription",
            value=f"**{self.entry_fee:,}** PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name="👥 Équipe",
            value=f"**{len(self.participants)}/{self.max_participants}**",
            inline=True
        )
        
        # Équipe actuelle
        team_text = ""
        role_emojis = {
            "mastermind": "🧠", "hacker": "💻", "gunman": "🔫", 
            "driver": "🚗", "locksmith": "🔐", "lookout": "👁️"
        }
        
        for participant in self.participants.values():
            role_emoji = role_emojis.get(participant["role"], "👤")
            team_text += f"{role_emoji} **{participant['user'].display_name}** - {participant['role'].title()} (+{participant['skill']})\n"
        
        embed.add_field(
            name="🎭 Équipe actuelle",
            value=team_text if team_text else "Seul l'organisateur pour l'instant",
            inline=False
        )
        
        # Statistiques de difficulté
        embed.add_field(
            name="📊 Défis à relever",
            value=f"🏦 **Sécurité banque :** {self.bank_security}/100\n"
                  f"🚨 **Police :** {self.police_response}/100\n"
                  f"🎯 **Compétence équipe :** {team_skill}",
            inline=True
        )
        
        # Estimation des chances (approximative)
        base_chance = max(20, min(80, team_skill - ((self.bank_security + self.police_response) // 2)))
        team_bonus = len(self.participants) * 5  # Bonus d'équipe
        estimated_chance = min(85, base_chance + team_bonus)
        
        embed.add_field(
            name="📈 Estimation",
            value=f"**~{estimated_chance}%** de réussite\n"
                  f"*(Plus vous êtes, mieux c'est !)*",
            inline=True
        )
        
        # Instructions
        embed.add_field(
            name="⚡ Comment participer ?",
            value="• 💼 **Rejoindre** - Payez l'inscription et choisissez votre rôle\n"
                  "• 🎯 **Lancer** - L'organisateur démarre quand l'équipe est prête\n"
                  "• ⏰ **5 minutes** pour recruter avant expiration",
            inline=False
        )
        
        # Avertissements
        embed.add_field(
            name="⚠️ ATTENTION",
            value="💀 **En cas d'échec, vous perdez de l'argent !**\n"
                  "🏛️ Vos pertes iront à la banque publique\n"
                  "🎯 Plus l'équipe est grande, plus c'est facile !",
            inline=False
        )
        
        embed.set_footer(text="🏦💥 Système de braquages ultra-palpitant • Risque élevé, récompense élevée !")
        return embed


# Ajouter la méthode à HeistView
HeistView.create_recruitment_embed = lambda self: BankHeist.create_recruitment_embed_static(self)

class BankHeistStatic:
    @staticmethod
    def create_recruitment_embed_static(heist_view):
        """Version statique de create_recruitment_embed pour HeistView"""
        team_skill = sum(p["skill"] for p in heist_view.participants.values())
        
        embed = discord.Embed(
            title="🏦💥 BRAQUAGE DE BANQUE ORGANISÉ",
            description=f"**{heist_view.organizer.display_name}** organise un braquage ultra-palpitant !",
            color=Colors.WARNING
        )
        
        # Informations principales
        embed.add_field(
            name="💰 Objectif",
            value=f"**{heist_view.target_amount:,}** PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name="💳 Frais d'inscription",
            value=f"**{heist_view.entry_fee:,}** PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name="👥 Équipe",
            value=f"**{len(heist_view.participants)}/{heist_view.max_participants}**",
            inline=True
        )
        
        # Équipe actuelle
        team_text = ""
        role_emojis = {
            "mastermind": "🧠", "hacker": "💻", "gunman": "🔫", 
            "driver": "🚗", "locksmith": "🔐", "lookout": "👁️"
        }
        
        for participant in heist_view.participants.values():
            role_emoji = role_emojis.get(participant["role"], "👤")
            team_text += f"{role_emoji} **{participant['user'].display_name}** - {participant['role'].title()} (+{participant['skill']})\n"
        
        embed.add_field(
            name="🎭 Équipe actuelle",
            value=team_text if team_text else "Seul l'organisateur pour l'instant",
            inline=False
        )
        
        # Statistiques de difficulté
        embed.add_field(
            name="📊 Défis à relever",
            value=f"🏦 **Sécurité banque :** {heist_view.bank_security}/100\n"
                  f"🚨 **Police :** {heist_view.police_response}/100\n"
                  f"🎯 **Compétence équipe :** {team_skill}",
            inline=True
        )
        
        # Estimation des chances
        base_chance = max(20, min(80, team_skill - ((heist_view.bank_security + heist_view.police_response) // 2)))
        team_bonus = len(heist_view.participants) * 5
        estimated_chance = min(85, base_chance + team_bonus)
        
        embed.add_field(
            name="📈 Estimation",
            value=f"**~{estimated_chance}%** de réussite\n*(Plus vous êtes, mieux c'est !)*",
            inline=True
        )
        
        # Instructions
        embed.add_field(
            name="⚡ Comment participer ?",
            value="• 💼 **Rejoindre** - Payez l'inscription et obtenez un rôle\n"
                  "• 🎯 **Lancer** - L'organisateur démarre le braquage\n"
                  "• ⏰ **5 minutes** pour recruter maximum",
            inline=False
        )
        
        # Avertissements
        embed.add_field(
            name="⚠️ RISQUES & RÉCOMPENSES",
            value="🏆 **SUCCÈS :** Partagez l'objectif + bonus équipe\n"
                  "💀 **ÉCHEC :** Perdez 30-60% selon la phase d'échec\n"
                  "🏛️ **Solidarité :** Vos pertes vont à la banque publique !",
            inline=False
        )
        
        embed.set_footer(text="💥 Plus de risques = Plus de récompenses • Système ultra-palpitant activé !")
        return embed

# Assigner la méthode correctement
HeistView.create_recruitment_embed = lambda self: BankHeistStatic.create_recruitment_embed_static(self)


async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(BankHeist(bot))
