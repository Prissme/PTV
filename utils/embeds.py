import discord
from datetime import datetime
from config import Colors, Emojis, PREFIX
import math

def create_balance_embed(user: discord.Member, balance: int) -> discord.Embed:
    """Créer un embed pour l'affichage du solde"""
    embed = discord.Embed(
        title=f"{Emojis.MONEY} Solde",
        description=f"**{user.display_name}** possède **{balance:,}** PrissBucks",
        color=Colors.SUCCESS if balance > 0 else Colors.WARNING
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed

def create_daily_embed(user: discord.Member, total_reward: int, bonus: int = 0) -> discord.Embed:
    """Créer un embed pour le daily spin"""
    embed = discord.Embed(
        title=f"{Emojis.DAILY} Daily Spin !",
        description=f"**{user.display_name}** a gagné **{total_reward:,}** PrissBucks !",
        color=Colors.SUCCESS
    )
    
    if bonus > 0:
        embed.description += f"\n🎉 **BONUS:** +{bonus} pièces !"
    
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="Reviens demain pour ton prochain daily !")
    return embed

def create_transfer_embed(giver: discord.Member, receiver: discord.Member, amount: int, new_balance: int) -> discord.Embed:
    """Créer un embed pour les transferts"""
    embed = discord.Embed(
        title=f"{Emojis.TRANSFER} Transfert réussi !",
        description=f"**{giver.display_name}** a donné **{amount:,}** PrissBucks à **{receiver.display_name}**",
        color=Colors.SUCCESS
    )
    embed.set_footer(text=f"Nouveau solde de {giver.display_name}: {new_balance:,} PrissBucks")
    return embed

def create_leaderboard_embed(top_users: list, bot, limit: int) -> discord.Embed:
    """Créer un embed pour le leaderboard"""
    embed = discord.Embed(
        title=f"{Emojis.LEADERBOARD} Classement des plus riches",
        color=Colors.GOLD
    )

    description = ""
    for i, (user_id, balance) in enumerate(top_users, 1):
        try:
            user = bot.get_user(user_id)
            username = user.display_name if user else f"Utilisateur {user_id}"
        except:
            username = f"Utilisateur {user_id}"

        if i == 1:
            emoji = "🥇"
        elif i == 2:
            emoji = "🥈"
        elif i == 3:
            emoji = "🥉"
        else:
            emoji = f"`{i:2d}.`"

        description += f"{emoji} **{username}** - {balance:,} PrissBucks\n"

    embed.description = description
    embed.set_footer(text=f"Top {len(top_users)} utilisateurs")
    return embed

def create_shop_embed(items: list, page: int, total_pages: int) -> discord.Embed:
    """Créer un embed pour la boutique"""
    embed = discord.Embed(
        title=f"{Emojis.SHOP} Boutique PrissBucks",
        description="Dépense tes PrissBucks pour des récompenses exclusives !",
        color=Colors.PREMIUM
    )
    
    for item in items:
        # Choisir l'icône selon le type d'item
        if item["type"] == "role":
            icon = Emojis.ROLE
        elif item["type"] == "cooldown_reset":
            icon = "⏰"
        else:
            icon = Emojis.SHOP
        
        # Description enrichie pour les items spéciaux
        description = item['description']
        if item["type"] == "cooldown_reset":
            description += "\n💫 **Usage immédiat** - Se déclenche automatiquement à l'achat !"
        
        embed.add_field(
            name=f"{icon} **{item['name']}** - {item['price']:,} {Emojis.MONEY}",
            value=f"{description}\n`{PREFIX}buy {item['id']}` ou `/buy {item['id']}` pour acheter",
            inline=False
        )
    
    embed.set_footer(text=f"Page {page}/{total_pages} • {len(items)} item(s) sur cette page")
    
    # Ajouter des boutons de navigation si nécessaire
    if total_pages > 1:
        embed.add_field(
            name="📄 Navigation",
            value=f"`{PREFIX}shop {page-1 if page > 1 else total_pages}` ← Page précédente\n"
                  f"`{PREFIX}shop {page+1 if page < total_pages else 1}` → Page suivante",
            inline=False
        )
    
    return embed

def create_purchase_embed(user: discord.Member, item: dict, new_balance: int, role_granted: bool = False, role_name: str = None, special_effect: str = None) -> discord.Embed:
    """Créer un embed pour les achats"""
    embed = discord.Embed(
        title=f"{Emojis.SUCCESS} Achat réussi !",
        description=f"**{user.display_name}** a acheté **{item['name']}** pour **{item['price']:,}** PrissBucks !",
        color=Colors.SUCCESS
    )
    
    # Ajouter les effets selon le type d'item
    if role_granted and role_name:
        embed.description += f"\n{Emojis.ROLE} **Rôle {role_name} attribué !**"
    
    if special_effect:
        embed.description += f"\n{special_effect}"
    
    # Footer avec le nouveau solde
    embed.set_footer(text=f"Nouveau solde: {new_balance:,} PrissBucks")
    embed.set_thumbnail(url=user.display_avatar.url)
    
    # Ajouter un field spécial pour les cooldown resets
    if item["type"] == "cooldown_reset":
        embed.add_field(
            name="✨ Effet immédiat",
            value="Tous tes cooldowns ont été supprimés !\nTu peux maintenant utiliser toutes tes commandes.",
            inline=False
        )
    
    return embed

def create_inventory_embed(user: discord.Member, purchases: list) -> discord.Embed:
    """Créer un embed pour l'inventaire"""
    if not purchases:
        embed = discord.Embed(
            title=f"{Emojis.INVENTORY} Inventaire vide",
            description=f"**{user.display_name}** n'a encore rien acheté dans la boutique.",
            color=Colors.WARNING
        )
        embed.add_field(
            name="💡 Astuce",
            value=f"Utilise `{PREFIX}shop` pour voir les items disponibles !",
            inline=False
        )
        return embed
    
    embed = discord.Embed(
        title=f"{Emojis.INVENTORY} Inventaire de {user.display_name}",
        description=f"**{len(purchases)}** item(s) possédé(s)",
        color=Colors.PREMIUM
    )
    
    total_spent = 0
    for purchase in purchases[:10]:  # Limiter à 10 items
        # Icône selon le type
        if purchase["type"] == "role":
            icon = Emojis.ROLE
        elif purchase["type"] == "cooldown_reset":
            icon = "⏰"
        else:
            icon = Emojis.INVENTORY
            
        date = purchase["purchase_date"].strftime("%d/%m/%Y")
        
        # Description spéciale pour les items consommables
        item_desc = f"{Emojis.MONEY} **{purchase['price_paid']:,}** PrissBucks\n📅 Acheté le {date}"
        if purchase["type"] == "cooldown_reset":
            item_desc += "\n💫 *Item consommable utilisé*"
        
        embed.add_field(
            name=f"{icon} {purchase['name']}",
            value=item_desc,
            inline=True
        )
        total_spent += purchase["price_paid"]
    
    if len(purchases) > 10:
        embed.add_field(
            name="📄 ...",
            value=f"Et {len(purchases) - 10} autre(s) item(s)",
            inline=True
        )
    
    embed.set_footer(text=f"Total dépensé: {total_spent:,} PrissBucks")
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed

def create_error_embed(title: str, message: str) -> discord.Embed:
    """Créer un embed d'erreur"""
    embed = discord.Embed(
        title=f"{Emojis.ERROR} {title}",
        description=message,
        color=Colors.ERROR
    )
    return embed

def create_warning_embed(title: str, message: str) -> discord.Embed:
    """Créer un embed d'avertissement"""
    embed = discord.Embed(
        title=f"{Emojis.WARNING} {title}",
        description=message,
        color=Colors.WARNING
    )
    return embed

def create_success_embed(title: str, message: str) -> discord.Embed:
    """Créer un embed de succès"""
    embed = discord.Embed(
        title=f"{Emojis.SUCCESS} {title}",
        description=message,
        color=Colors.SUCCESS
    )
    return embed

def create_info_embed(title: str, message: str) -> discord.Embed:
    """Créer un embed d'information"""
    embed = discord.Embed(
        title=f"ℹ️ {title}",
        description=message,
        color=Colors.INFO
    )
    return embed

def create_cooldown_embed(command: str, retry_after: float) -> discord.Embed:
    """Créer un embed pour les cooldowns"""
    hours = int(retry_after // 3600)
    minutes = int((retry_after % 3600) // 60)
    seconds = int(retry_after % 60)
    
    if hours > 0:
        time_str = f"**{hours}h {minutes}min {seconds}s**"
    elif minutes > 0:
        time_str = f"**{minutes}min {seconds}s**"
    else:
        time_str = f"**{seconds}s**"
    
    embed = discord.Embed(
        title=f"{Emojis.COOLDOWN} Cooldown actif !",
        description=f"Tu pourras utiliser `{command}` dans {time_str}",
        color=Colors.WARNING
    )
    
    # Ajouter une mention de l'item Reset Cooldowns
    embed.add_field(
        name="💡 Astuce",
        value="Tu peux acheter l'item **⏰ Reset Cooldowns** dans le shop pour supprimer tous tes cooldowns !",
        inline=False
    )
    
    return embed

def create_shop_stats_embed(stats: dict) -> discord.Embed:
    """Créer un embed pour les statistiques du shop"""
    embed = discord.Embed(
        title="📊 Statistiques de la boutique",
        color=Colors.INFO
    )
    
    # Statistiques générales
    embed.add_field(
        name="👥 Acheteurs uniques", 
        value=f"**{stats['unique_buyers']}** utilisateurs", 
        inline=True
    )
    embed.add_field(
        name="🛒 Total des achats", 
        value=f"**{stats['total_purchases']}** achats", 
        inline=True
    )
    embed.add_field(
        name="💰 Revenus totaux", 
        value=f"**{stats['total_revenue']:,}** PrissBucks", 
        inline=True
    )
    
    # Top des items
    if stats['top_items']:
        top_text = ""
        for i, item in enumerate(stats['top_items'][:5], 1):
            emoji = ["🥇", "🥈", "🥉", "🏅", "🏅"][i-1]
            top_text += f"{emoji} **{item['name']}** - {item['purchases']} vente(s)\n"
        
        embed.add_field(
            name="🏆 Top des ventes",
            value=top_text,
            inline=False
        )
    
    return embed

def create_help_embed(user_permissions: dict) -> discord.Embed:
    """Créer un embed d'aide"""
    embed = discord.Embed(
        title="🤖 Bot Économie - Aide",
        description="Voici toutes les commandes disponibles :",
        color=Colors.INFO
    )

    # Commandes principales
    embed.add_field(
        name=f"{Emojis.MONEY} Commandes Économie",
        value=f"`{PREFIX}balance [@user]` - Affiche le solde\n"
              f"`{PREFIX}give <@user> <montant>` - Donne des pièces\n"
              f"`{PREFIX}daily` - Daily spin (récupère tes pièces quotidiennes)\n"
              f"`{PREFIX}leaderboard [limite]` - Top des plus riches",
        inline=False
    )
    
    # Commandes shop
    embed.add_field(
        name=f"{Emojis.SHOP} Commandes Boutique",
        value=f"`{PREFIX}shop [page]` - Affiche la boutique\n"
              f"`{PREFIX}buy <id>` - Achète un item\n"
              f"`{PREFIX}inventory [@user]` - Affiche l'inventaire",
        inline=False
    )
    
    # Mini-jeux et fonctions spéciales
    embed.add_field(
        name="🎮 Mini-jeux & Spécial",
        value=f"`/ppc <@adversaire> <mise>` - Pierre-Papier-Ciseaux\n"
              f"`{PREFIX}voler <@user>` - Tente de voler des PrissBucks\n"
              f"`{PREFIX}cooldowns` - Vérifier tes cooldowns actifs",
        inline=False
    )
    
    # Commandes admin si permissions
    if user_permissions.get('administrator'):
        embed.add_field(
            name="👑 Commandes Admin",
            value=f"`{PREFIX}addpb <@user> <montant>` - Ajoute des PrissBucks\n"
                  f"`{PREFIX}resetcd [@user]` - Reset les cooldowns d'un utilisateur",
            inline=False
        )

    # Nouveautés
    embed.add_field(
        name="✨ Nouveautés",
        value="🔸 **Item Reset Cooldowns** - Supprime tous tes cooldowns pour 200 PrissBucks !\n"
              "🔸 **Messages récompensés** - +1 PrissBuck par message (CD: 20s)\n"
              "🔸 **Système de vol** - Risque/récompense avec 50% de chances",
        inline=False
    )

    # Aliases
    embed.add_field(
        name="🔄 Aliases populaires",
        value="`balance` → `bal`, `money`\n"
              "`give` → `pay`, `transfer`\n"
              "`daily` → `dailyspin`, `spin`\n"
              "`leaderboard` → `top`, `rich`, `lb`\n"
              "`inventory` → `inv`\n"
              "`cooldowns` → `cd`, `mycooldowns`",
        inline=False
    )

    embed.set_footer(text=f"Préfixe: {PREFIX} | Système Reset Cooldowns disponible !")
    return embed

def create_cooldowns_status_embed(user: discord.Member, active_cooldowns: list) -> discord.Embed:
    """Créer un embed pour l'état des cooldowns d'un utilisateur"""
    if active_cooldowns:
        embed = discord.Embed(
            title=f"⏰ Cooldowns de {user.display_name}",
            description="\n".join(active_cooldowns),
            color=Colors.WARNING
        )
        embed.add_field(
            name="💡 Astuce",
            value="Tu peux acheter l'item **⏰ Reset Cooldowns** (200 PB) dans le shop pour supprimer tous tes cooldowns !",
            inline=False
        )
        embed.add_field(
            name="🛒 Comment faire",
            value=f"1. `{PREFIX}shop` - Voir la boutique\n"
                  f"2. `{PREFIX}buy <id>` - Acheter l'item Reset Cooldowns\n"
                  f"3. Effet immédiat - Tous tes cooldowns sont supprimés !",
            inline=False
        )
    else:
        embed = discord.Embed(
            title=f"✅ Aucun cooldown actif",
            description=f"**{user.display_name}** peut utiliser toutes les commandes !",
            color=Colors.SUCCESS
        )
        embed.add_field(
            name="🚀 Tu es libre !",
            value="Profite-en pour utiliser tes commandes préférées :\n"
                  f"• `{PREFIX}daily` - Récupère tes pièces quotidiennes\n"
                  f"• `{PREFIX}voler <@user>` - Tente ta chance au vol\n" 
                  f"• `{PREFIX}give
