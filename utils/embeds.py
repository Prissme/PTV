import discord
from datetime import datetime
from config import Colors, Emojis, PREFIX
import math

# ==================== FONCTIONS MANQUANTES CRITIQUES ====================

def create_cooldown_embed(command: str, retry_after: float) -> discord.Embed:
    """Crée un embed pour les cooldowns - FONCTION MANQUANTE CRITIQUE"""
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
    
    embed.add_field(
        name="💡 Pourquoi ce cooldown ?",
        value="Pour éviter le spam et maintenir l'équilibre du système économique.",
        inline=False
    )
    
    return embed

def create_error_embed(title: str, message: str) -> discord.Embed:
    """Crée un embed d'erreur"""
    embed = discord.Embed(
        title=f"{Emojis.ERROR} {title}",
        description=message,
        color=Colors.ERROR
    )
    return embed

def create_warning_embed(title: str, message: str) -> discord.Embed:
    """Crée un embed d'avertissement"""
    embed = discord.Embed(
        title=f"{Emojis.WARNING} {title}",
        description=message,
        color=Colors.WARNING
    )
    return embed

def create_success_embed(title: str, message: str) -> discord.Embed:
    """Crée un embed de succès"""
    embed = discord.Embed(
        title=f"{Emojis.SUCCESS} {title}",
        description=message,
        color=Colors.SUCCESS
    )
    return embed

def create_info_embed(title: str, message: str) -> discord.Embed:
    """Crée un embed d'information"""
    embed = discord.Embed(
        title=f"ℹ️ {title}",
        description=message,
        color=Colors.INFO
    )
    return embed

# ==================== EMBEDS ÉCONOMIQUES ====================

def create_balance_embed(user: discord.Member, balance: int) -> discord.Embed:
    """Créer un embed pour l'affichage du solde"""
    embed = discord.Embed(
        title=f"{Emojis.MONEY} Solde",
        description=f"**{user.display_name}** possède **{balance:,}** PrissBucks",
        color=Colors.SUCCESS if balance > 0 else Colors.WARNING
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    
    # Ajouter des conseils selon le solde
    if balance == 0:
        embed.add_field(
            name="💡 Comment commencer ?",
            value=f"• Utilise `{PREFIX}daily` pour tes pièces quotidiennes\n"
                  f"• Écris des messages pour gagner des PrissBucks\n"
                  f"• Utilise `/publicbank` pour récupérer des fonds !",
            inline=False
        )
    elif balance < 100:
        embed.add_field(
            name="🚀 Conseils pour progresser",
            value=f"• `{PREFIX}daily` tous les jours\n"
                  f"• Joue à `/ppc` ou `/roulette`\n"
                  f"• Consulte `{PREFIX}shop` pour voir les récompenses",
            inline=False
        )
    
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
        embed.add_field(
            name="🍀 Chance !",
            value=f"Tu as eu de la chance avec un bonus de {bonus} PB !",
            inline=False
        )
    
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="Reviens demain pour ton prochain daily !")
    return embed

# ==================== EMBEDS TRANSFERTS AVEC TAXES ====================

def create_transfer_embed(giver: discord.Member, receiver: discord.Member, amount: int, new_balance: int) -> discord.Embed:
    """Créer un embed pour les transferts classiques (sans taxe) - DEPRECATED"""
    # Cette fonction est maintenant deprecated, utiliser create_transfer_with_tax_embed
    return create_transfer_with_tax_embed(giver, receiver, {
        'gross_amount': amount,
        'net_amount': amount,
        'tax_amount': 0,
        'tax_rate': 0
    }, new_balance)

def create_transfer_with_tax_embed(giver: discord.Member, receiver: discord.Member, tax_info: dict, new_balance: int) -> discord.Embed:
    """Créer un embed pour les transferts avec taxe"""
    embed = discord.Embed(
        title=f"{Emojis.TRANSFER} Transfert {'avec taxe ' if tax_info['tax_amount'] > 0 else ''}réussi !",
        description=f"**{giver.display_name}** a transféré **{tax_info['gross_amount']:,}** PrissBucks à **{receiver.display_name}**",
        color=Colors.SUCCESS
    )
    
    if tax_info['tax_amount'] > 0:
        embed.add_field(
            name="💰 Montant demandé",
            value=f"{tax_info['gross_amount']:,} PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name="💸 Reçu par le destinataire",
            value=f"{tax_info['net_amount']:,} PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name=f"{Emojis.TAX} Taxe prélevée",
            value=f"{tax_info['tax_amount']:,} PrissBucks ({tax_info['tax_rate']:.0f}%)",
            inline=True
        )
    else:
        embed.add_field(
            name="💸 Montant transféré",
            value=f"{tax_info['gross_amount']:,} PrissBucks",
            inline=True
        )
    
    embed.add_field(
        name="📊 Ton nouveau solde",
        value=f"{new_balance:,} PrissBucks",
        inline=False
    )
    
    if tax_info['tax_amount'] > 0:
        embed.set_footer(text="Les taxes contribuent au développement du serveur !")
    
    return embed

# ==================== EMBEDS CLASSEMENT ====================

def create_leaderboard_embed(top_users: list, bot, limit: int) -> discord.Embed:
    """Créer un embed pour le leaderboard"""
    embed = discord.Embed(
        title=f"{Emojis.LEADERBOARD} Classement des plus riches",
        color=Colors.GOLD
    )

    if not top_users:
        embed.description = "Aucun utilisateur avec des PrissBucks pour le moment."
        embed.add_field(
            name="🚀 Sois le premier !",
            value=f"Utilise `{PREFIX}daily` pour commencer ton aventure économique !",
            inline=False
        )
        return embed

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
        elif i <= 10:
            emoji = "🏆"
        else:
            emoji = f"`{i:2d}.`"

        description += f"{emoji} **{username}** - {balance:,} PrissBucks\n"

    embed.description = description
    embed.set_footer(text=f"Top {len(top_users)} utilisateurs")
    return embed

# ==================== EMBEDS BOUTIQUE AVEC TAXES ====================

def create_shop_embed(items: list, page: int, total_pages: int) -> discord.Embed:
    """Créer un embed pour la boutique (sans taxes - deprecated)"""
    # DEPRECATED: Utiliser create_shop_embed_with_tax
    return create_shop_embed_with_tax(items, page, total_pages, 0.0)

def create_shop_embed_with_tax(items: list, page: int, total_pages: int, tax_rate: float) -> discord.Embed:
    """Créer un embed pour la boutique avec affichage des taxes"""
    embed = discord.Embed(
        title=f"{Emojis.SHOP} Boutique PrissBucks",
        description=f"Dépense tes PrissBucks pour des récompenses exclusives !",
        color=Colors.PREMIUM
    )
    
    if tax_rate > 0:
        embed.description += f"\n{Emojis.TAX} **Taxe de {tax_rate*100:.0f}% incluse dans les prix affichés**"
    
    if not items:
        embed.add_field(
            name="🏪 Boutique vide",
            value="Aucun item disponible pour le moment.\nRevenez plus tard !",
            inline=False
        )
        return embed
    
    for item in items:
        # Choisir l'icône selon le type d'item
        if item["type"] == "role":
            icon = Emojis.ROLE
        else:
            icon = Emojis.SHOP
        
        # Prix avec et sans taxe
        base_price = item['price']
        total_price = item.get('total_price', base_price)
        tax_amount = item.get('tax_amount', 0)
        
        # Description de l'item
        description = item.get('description', 'Aucune description disponible')
        
        # Affichage du prix avec détail de la taxe
        price_display = f"**{total_price:,}** {Emojis.MONEY}"
        if tax_amount > 0:
            price_display += f" *(base: {base_price:,} + taxe: {tax_amount:,})*"
        
        embed.add_field(
            name=f"{icon} **{item['name']}** - {price_display}",
            value=f"{description}\n`{PREFIX}buy {item['id']}` ou `/buy {item['id']}` pour acheter",
            inline=False
        )
    
    embed.set_footer(text=f"Page {page}/{total_pages} • {len(items)} item(s)" + 
                     (f" • Taxes: {tax_rate*100:.0f}%" if tax_rate > 0 else ""))
    
    # Ajouter des boutons de navigation si nécessaire
    if total_pages > 1:
        embed.add_field(
            name="📄 Navigation",
            value=f"`{PREFIX}shop {page-1 if page > 1 else total_pages}` ← Page précédente\n"
                  f"`{PREFIX}shop {page+1 if page < total_pages else 1}` → Page suivante",
            inline=False
        )
    
    return embed

# ==================== EMBEDS ACHATS AVEC TAXES ====================

def create_purchase_embed(user: discord.Member, item: dict, new_balance: int, role_granted: bool = False, role_name: str = None, special_effect: str = None) -> discord.Embed:
    """Créer un embed pour les achats (sans taxe - deprecated)"""
    tax_info = {
        'base_price': item['price'],
        'total_price': item['price'],
        'tax_amount': 0,
        'tax_rate': 0
    }
    return create_purchase_embed_with_tax(user, item, tax_info, new_balance, role_granted, role_name, special_effect)

def create_purchase_embed_with_tax(user: discord.Member, item: dict, tax_info: dict, new_balance: int, role_granted: bool = False, role_name: str = None, special_effect: str = None) -> discord.Embed:
    """Créer un embed pour les achats avec taxes"""
    embed = discord.Embed(
        title=f"{Emojis.SUCCESS} Achat réussi !",
        description=f"**{user.display_name}** a acheté **{item['name']}** !",
        color=Colors.SUCCESS
    )
    
    # Détails financiers avec taxe
    embed.add_field(
        name="💰 Prix de base",
        value=f"{tax_info['base_price']:,} PrissBucks",
        inline=True
    )
    
    if tax_info['tax_amount'] > 0:
        embed.add_field(
            name=f"{Emojis.TAX} Taxe",
            value=f"{tax_info['tax_amount']:,} PrissBucks ({tax_info['tax_rate']:.0f}%)",
            inline=True
        )
        
        embed.add_field(
            name="💸 Total payé",
            value=f"**{tax_info['total_price']:,}** PrissBucks",
            inline=True
        )
    else:
        embed.add_field(
            name="💸 Total payé",
            value=f"**{tax_info['total_price']:,}** PrissBucks",
            inline=True
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Espace vide
    
    # Ajouter les effets selon le type d'item
    if role_granted and role_name:
        embed.add_field(
            name="🎭 Rôle attribué",
            value=f"**{role_name}** ✅",
            inline=False
        )
    
    if special_effect:
        embed.add_field(
            name="✨ Effet spécial",
            value=special_effect,
            inline=False
        )
    
    # Footer avec le nouveau solde
    embed.set_footer(text=f"Nouveau solde: {new_balance:,} PrissBucks")
    embed.set_thumbnail(url=user.display_avatar.url)
    
    return embed

# ==================== EMBEDS INVENTAIRE ====================

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
    total_taxes = 0
    for purchase in purchases[:10]:  # Limiter à 10 items pour éviter les embeds trop longs
        # Icône selon le type
        if purchase["type"] == "role":
            icon = Emojis.ROLE
        else:
            icon = Emojis.INVENTORY
            
        date = purchase["purchase_date"].strftime("%d/%m/%Y")
        
        # Description avec détails de prix et taxe
        item_desc = f"{Emojis.MONEY} **{purchase['price_paid']:,}** PrissBucks"
        if purchase.get('tax_paid', 0) > 0:
            base_price = purchase['price_paid'] - purchase['tax_paid']
            item_desc += f" *(base: {base_price:,} + taxe: {purchase['tax_paid']:,})*"
        item_desc += f"\n📅 Acheté le {date}"
        
        embed.add_field(
            name=f"{icon} {purchase['name']}",
            value=item_desc,
            inline=True
        )
        total_spent += purchase["price_paid"]
        total_taxes += purchase.get("tax_paid", 0)
    
    if len(purchases) > 10:
        embed.add_field(
            name="📄 ...",
            value=f"Et {len(purchases) - 10} autre(s) item(s)",
            inline=True
        )
    
    # Footer avec statistiques détaillées
    footer_text = f"Total dépensé: {total_spent:,} PrissBucks"
    if total_taxes > 0:
        footer_text += f" (dont {total_taxes:,} de taxes)"
    embed.set_footer(text=footer_text)
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed

# ==================== EMBEDS STATISTIQUES ====================

def create_shop_stats_embed(stats: dict) -> discord.Embed:
    """Créer un embed pour les statistiques du shop avec taxes"""
    embed = discord.Embed(
        title="📊 Statistiques de la boutique",
        color=Colors.INFO
    )
    
    if not stats or stats.get('total_purchases', 0) == 0:
        embed.description = "Aucune donnée de vente pour le moment."
        return embed
    
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
    
    # Statistiques sur les taxes
    embed.add_field(
        name=f"{Emojis.TAX} Taxes collectées", 
        value=f"**{stats.get('total_taxes', 0):,}** PrissBucks", 
        inline=True
    )
    
    if stats['total_revenue'] > 0:
        tax_percentage = (stats.get('total_taxes', 0) / stats['total_revenue'] * 100)
        embed.add_field(
            name="📈 % Taxes", 
            value=f"**{tax_percentage:.1f}%** du CA", 
            inline=True
        )
    
    # Top des items
    if stats.get('top_items'):
        top_text = ""
        for i, item in enumerate(stats['top_items'][:5], 1):
            emoji = ["🥇", "🥈", "🥉", "🏅", "🏅"][i-1]
            revenue_text = f"{item.get('revenue', 0):,} PB"
            if item.get('taxes_collected', 0) > 0:
                revenue_text += f" (dont {item['taxes_collected']:,} taxes)"
            top_text += f"{emoji} **{item['name']}** - {item['purchases']} vente(s) ({revenue_text})\n"
        
        embed.add_field(
            name="🏆 Top des ventes",
            value=top_text,
            inline=False
        )
    
    return embed

# ==================== EMBEDS AIDE ====================

def create_help_embed(user_permissions: dict) -> discord.Embed:
    """Créer un embed d'aide avec informations sur les taxes"""
    from config import TRANSFER_TAX_RATE, SHOP_TAX_RATE
    
    embed = discord.Embed(
        title="🤖 Bot Économie - Aide",
        description="Voici toutes les commandes disponibles :",
        color=Colors.INFO
    )

    # Commandes principales avec taxes
    embed.add_field(
        name=f"{Emojis.MONEY} Commandes Économie",
        value=f"`{PREFIX}balance [@user]` - Affiche le solde\n"
              f"`{PREFIX}give <@user> <montant>` - Donne des pièces ({TRANSFER_TAX_RATE*100:.0f}% de taxe)\n"
              f"`{PREFIX}daily` - Daily spin (récupère tes pièces quotidiennes)\n"
              f"`{PREFIX}leaderboard [limite]` - Top des plus riches",
        inline=False
    )
    
    # Commandes shop avec taxes
    embed.add_field(
        name=f"{Emojis.SHOP} Commandes Boutique",
        value=f"`{PREFIX}shop [page]` - Affiche la boutique (prix avec taxe {SHOP_TAX_RATE*100:.0f}%)\n"
              f"`{PREFIX}buy <id>` - Achète un item\n"
              f"`{PREFIX}inventory [@user]` - Affiche l'inventaire",
        inline=False
    )
    
    # Banque publique
    embed.add_field(
        name=f"{Emojis.PUBLIC_BANK} Banque Publique",
        value=f"`{PREFIX}publicbank` - Voir les fonds disponibles\n"
              f"`{PREFIX}withdraw_public <montant>` - Retirer des fonds\n"
              f"🔥 Alimentée par les pertes casino !",
        inline=False
    )
    
    # Mini-jeux et fonctions spéciales
    embed.add_field(
        name="🎮 Mini-jeux & Spécial",
        value=f"`/ppc <@adversaire> <mise>` - Pierre-Papier-Ciseaux\n"
              f"`/roulette <pari> <mise>` - Roulette casino\n"
              f"`{PREFIX}voler <@user>` - Tente de voler des PrissBucks",
        inline=False
    )
    
    # Système de taxes
    embed.add_field(
        name=f"{Emojis.TAX} Système de Taxes",
        value=f"• **Transferts:** {TRANSFER_TAX_RATE*100:.0f}% de taxe sur `give`\n"
              f"• **Boutique:** {SHOP_TAX_RATE*100:.0f}% de taxe sur tous les achats\n"
              f"• **Exemptions:** Daily, messages, mini-jeux, ajouts admin\n"
              f"• **Utilité:** Financement du développement du serveur",
        inline=False
    )
    
    # Commandes admin si permissions
    if user_permissions.get('administrator'):
        embed.add_field(
            name="👑 Commandes Admin",
            value=f"`{PREFIX}addpb <@user> <montant>` - Ajoute des PrissBucks\n"
                  f"`{PREFIX}additem <prix> <@role> <nom>` - Ajoute un item au shop\n"
                  f"`{PREFIX}shopstats` - Statistiques de la boutique",
            inline=False
        )

    embed.set_footer(text=f"Préfixe: {PREFIX} | Taxes: {TRANSFER_TAX_RATE*100:.0f}%/{SHOP_TAX_RATE*100:.0f}%")
    return embed

# ==================== EMBEDS STATUS COOLDOWNS ====================

def create_cooldowns_status_embed(user: discord.Member, active_cooldowns: list) -> discord.Embed:
    """Créer un embed pour l'état des cooldowns d'un utilisateur"""
    if active_cooldowns:
        embed = discord.Embed(
            title=f"⏰ Cooldowns de {user.display_name}",
            description="\n".join(active_cooldowns),
            color=Colors.WARNING
        )
        
        embed.add_field(
            name="💡 Conseils d'attente",
            value="• Consulte `{PREFIX}help` pour découvrir d'autres commandes\n"
                  "• Vérifie `{PREFIX}publicbank` pour des fonds gratuits\n"
                  "• Écris des messages pour gagner des PrissBucks automatiquement !",
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
                  f"• `{PREFIX}give <@user> <montant>` - Partage tes PrissBucks\n"
                  f"• `/ppc @user 100` - Joue à Pierre-Papier-Ciseaux\n"
                  f"• `/roulette red 50` - Tente ta chance à la roulette",
            inline=False
        )
    
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed

# ==================== UTILITAIRES ====================

def format_time_duration(seconds: float) -> str:
    """Formate une durée en secondes en format lisible"""
    if seconds <= 0:
        return "Disponible maintenant"
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}min")
    if secs > 0 or not parts:  # Toujours afficher les secondes si c'est tout ce qu'on a
        parts.append(f"{secs}s")
    
    return " ".join(parts)

def get_balance_color(balance: int) -> int:
    """Retourne une couleur selon le montant du solde"""
    if balance >= 100000:
        return Colors.GOLD
    elif balance >= 10000:
        return Colors.SUCCESS
    elif balance >= 1000:
        return Colors.INFO
    elif balance > 0:
        return Colors.WARNING
    else:
        return Colors.ERROR

def get_balance_emoji(balance: int) -> str:
    """Retourne un emoji selon le montant du solde"""
    if balance >= 100000:
        return "💎"
    elif balance >= 10000:
        return "💰"
    elif balance >= 1000:
        return "🪙"
    elif balance > 0:
        return "💵"
    else:
        return "😢"

def truncate_text(text: str, max_length: int = 100) -> str:
    """Tronque un texte s'il est trop long"""
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."