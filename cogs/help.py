import discord
from discord.ext import commands
from discord import app_commands
import logging

from config import PREFIX, Colors, TRANSFER_TAX_RATE, SHOP_TAX_RATE

logger = logging.getLogger(__name__)

class SimpleHelp(commands.Cog):
    """Aide simplifiée envoyée en privé"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.remove_command('help')  # Supprimer l'aide par défaut
        logger.info("Cog Help simplifié initialisé")

    @commands.command(name='help', aliases=['h'])
    async def help_cmd(self, ctx):
        """Aide essentielle envoyée en privé"""
        await self._send_help(ctx, ctx.author, is_slash=False)

    @app_commands.command(name="help", description="Affiche l'aide essentielle en privé")
    async def help_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._send_help(interaction, interaction.user, is_slash=True)

    async def _send_help(self, ctx_or_interaction, user, is_slash=False):
        """Logique commune pour l'aide simplifiée"""
        try:
            embed = self._create_simple_help_embed()
            
            if is_slash:
                await ctx_or_interaction.followup.send(embed=embed, ephemeral=True)
            else:
                try:
                    # Essayer d'envoyer en privé
                    await user.send(embed=embed)
                    await ctx_or_interaction.send("Aide envoyée en message privé !")
                except discord.Forbidden:
                    # Si MP fermés, envoyer dans le canal
                    await ctx_or_interaction.send(embed=embed, delete_after=60)
                    
        except Exception as e:
            logger.error(f"Erreur help: {e}")
            # Aide minimale en cas d'erreur
            basic_help = (
                f"**Commandes essentielles:**\n"
                f"`{PREFIX}balance` - Ton solde\n"
                f"`{PREFIX}daily` - Récompense quotidienne\n"
                f"`{PREFIX}shop` - Boutique\n"
                f"`{PREFIX}give <@user> <montant>` - Transférer\n"
                f"`/publicbank` - Banque publique"
            )
            
            if is_slash:
                await ctx_or_interaction.followup.send(basic_help, ephemeral=True)
            else:
                await ctx_or_interaction.send(basic_help, delete_after=30)

    def _create_simple_help_embed(self) -> discord.Embed:
        """Crée un embed d'aide simple et essentiel"""
        embed = discord.Embed(
            title="Aide Bot Économie",
            description="Commandes essentielles • Format: `/cmd` ou `e!cmd`",
            color=Colors.INFO
        )
        
        # Économie de base
        embed.add_field(
            name="Économie",
            value=f"`balance` - Ton solde\n"
                  f"`daily` - Récompense 24h\n"
                  f"`give <@user> <montant>` - Transférer (taxe {TRANSFER_TAX_RATE*100:.0f}%)\n"
                  f"`leaderboard` - Classement",
            inline=False
        )
        
        # Boutique
        embed.add_field(
            name="Boutique",
            value=f"`shop` - Items disponibles\n"
                  f"`buy <id>` - Acheter (taxe {SHOP_TAX_RATE*100:.0f}%)\n"
                  f"`inventory` - Tes achats",
            inline=False
        )
        
        # Nouveautés importantes
        embed.add_field(
            name="Banque Publique",
            value="`publicbank` - Fonds publics\n"
                  f"`withdraw_public <montant>` - Retirer\n"
                  "Alimentée par les pertes casino !",
            inline=False
        )
        
        # Jeux
        embed.add_field(
            name="Mini-jeux",
            value="`ppc <@user> <mise>` - Pierre-Papier-Ciseaux\n"
                  f"`roulette <pari> <mise>` - Casino\n"
                  f"`voler <@user>` - Vol risqué",
            inline=False
        )
        
        # Infos système
        embed.add_field(
            name="Système",
            value=f"Préfixe: `{PREFIX}` ou slash `/`\n"
                  f"Taxes: {TRANSFER_TAX_RATE*100:.0f}% transferts, {SHOP_TAX_RATE*100:.0f}% achats\n"
                  "Messages: +1 PB (20s cooldown)",
            inline=False
        )
        
        embed.set_footer(text=f"Utilise {PREFIX}help ou /help pour cette aide")
        return embed

    @commands.command(name='commands', aliases=['cmd'])
    async def commands_list(self, ctx):
        """Liste rapide des commandes sans détails"""
        commands_text = "**Commandes rapides:**\n"
        
        # Grouper par cog et prendre les commandes principales
        essential_commands = {
            'balance', 'daily', 'give', 'shop', 'buy', 'inventory', 
            'leaderboard', 'publicbank', 'withdraw_public', 'ppc', 
            'roulette', 'voler', 'help', 'ping'
        }
        
        available_commands = []
        for command in self.bot.commands:
            if command.name in essential_commands and not command.hidden:
                available_commands.append(f"`{PREFIX}{command.name}`")
        
        # Ajouter quelques slash importantes
        slash_commands = ["/help", "/balance", "/daily", "/shop", "/ppc", "/roulette", "/publicbank"]
        
        embed = discord.Embed(
            title="Liste des commandes",
            description=f"**Prefix:** {', '.join(sorted(available_commands))}\n\n" +
                       f"**Slash:** {', '.join(slash_commands)}",
            color=Colors.INFO
        )
        
        embed.set_footer(text=f"Utilise {PREFIX}help pour les détails")
        await ctx.send(embed=embed, delete_after=45)

    @commands.command(name='quickstart', aliases=['start'])
    async def quickstart_cmd(self, ctx):
        """Guide de démarrage ultra rapide"""
        embed = discord.Embed(
            title="Démarrage rapide",
            description="3 étapes pour commencer :",
            color=Colors.SUCCESS
        )
        
        embed.add_field(
            name="1. Première récompense",
            value=f"`{PREFIX}daily` - Récupère tes premières PrissBucks",
            inline=False
        )
        
        embed.add_field(
            name="2. Voir ton solde",
            value=f"`{PREFIX}balance` - Vérifie tes PrissBucks",
            inline=False
        )
        
        embed.add_field(
            name="3. Explorer",
            value=f"`{PREFIX}shop` - Voir la boutique\n"
                  f"`/publicbank` - Banque publique\n"
                  f"`/ppc @ami 100` - Défier un ami",
            inline=False
        )
        
        embed.add_field(
            name="Conseils",
            value="• Messages = +1 PB automatique\n"
                  "• Daily toutes les 24h\n"
                  "• Pertes casino → banque publique",
            inline=False
        )
        
        try:
            await ctx.author.send(embed=embed)
            await ctx.send("Guide de démarrage envoyé en privé !")
        except discord.Forbidden:
            await ctx.send(embed=embed, delete_after=60)

async def setup(bot):
    await bot.add_cog(SimpleHelp(bot))