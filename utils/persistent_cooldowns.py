"""
Système de cooldowns persistants pour éviter les bypass au redémarrage
Remplace tous les cooldowns en mémoire par un stockage en base de données
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import json

logger = logging.getLogger(__name__)

class PersistentCooldowns:
    """Gestionnaire de cooldowns persistants stockés en base de données"""
    
    def __init__(self, database):
        self.db = database
        
        # Types de cooldowns supportés avec leurs durées par défaut
        self.COOLDOWN_TYPES = {
            'daily': 86400,      # 24 heures
            'give': 5,           # 5 secondes
            'message': 20,       # 20 secondes
            'steal': 1800,       # 30 minutes
            'heist': 7200,       # 2 heures
            'casino': 4,         # 4 secondes
            'buy': 3,            # 3 secondes
            'publicbank': 1800,  # 30 minutes
            'bank': 3,           # 3 secondes
        }
    
    async def check_cooldown(self, user_id: int, cooldown_type: str, 
                           duration: Optional[int] = None) -> float:
        """
        Vérifie le cooldown d'un utilisateur pour un type donné
        
        Args:
            user_id: ID Discord de l'utilisateur
            cooldown_type: Type de cooldown (daily, give, message, etc.)
            duration: Durée custom en secondes (optionnel)
        
        Returns:
            float: Secondes restantes (0 si cooldown expiré)
        """
        if not self.db.pool:
            logger.warning("Database pool not available, cooldown check failed")
            return 0
        
        # Utiliser la durée par défaut si non spécifiée
        if duration is None:
            duration = self.COOLDOWN_TYPES.get(cooldown_type, 60)
        
        try:
            async with self.db.pool.acquire() as conn:
                # Récupérer le dernier usage
                row = await conn.fetchrow("""
                    SELECT last_used, expires_at 
                    FROM cooldowns 
                    WHERE user_id = $1 AND cooldown_type = $2
                """, user_id, cooldown_type)
                
                if not row:
                    # Aucun cooldown enregistré = disponible
                    return 0
                
                now = datetime.now(timezone.utc)
                
                # Vérifier expiration explicite si définie
                if row['expires_at'] and now >= row['expires_at']:
                    return 0
                
                # Calculer le temps écoulé depuis le dernier usage
                elapsed = (now - row['last_used']).total_seconds()
                
                if elapsed >= duration:
                    return 0  # Cooldown expiré
                
                return duration - elapsed  # Temps restant
                
        except Exception as e:
            logger.error(f"Erreur check_cooldown {user_id}/{cooldown_type}: {e}")
            return 0  # En cas d'erreur, autoriser l'action
    
    async def set_cooldown(self, user_id: int, cooldown_type: str, 
                          duration: Optional[int] = None, 
                          metadata: Optional[Dict[str, Any]] = None):
        """
        Active un cooldown pour un utilisateur
        
        Args:
            user_id: ID Discord de l'utilisateur
            cooldown_type: Type de cooldown
            duration: Durée en secondes (optionnel)
            metadata: Données additionnelles (optionnel)
        """
        if not self.db.pool:
            logger.warning("Database pool not available, cooldown set failed")
            return
        
        # Utiliser la durée par défaut si non spécifiée
        if duration is None:
            duration = self.COOLDOWN_TYPES.get(cooldown_type, 60)
        
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=duration)
        
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO cooldowns (user_id, cooldown_type, last_used, expires_at, metadata)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, cooldown_type) 
                    DO UPDATE SET 
                        last_used = EXCLUDED.last_used,
                        expires_at = EXCLUDED.expires_at,
                        metadata = EXCLUDED.metadata
                """, user_id, cooldown_type, now, expires_at, 
                json.dumps(metadata or {}))
                
                logger.debug(f"Cooldown set: {user_id}/{cooldown_type} for {duration}s")
                
        except Exception as e:
            logger.error(f"Erreur set_cooldown {user_id}/{cooldown_type}: {e}")
    
    async def reset_cooldown(self, user_id: int, cooldown_type: str):
        """
        Supprime un cooldown (utile pour les admins)
        """
        if not self.db.pool:
            return
            
        try:
            async with self.db.pool.acquire() as conn:
                result = await conn.execute("""
                    DELETE FROM cooldowns 
                    WHERE user_id = $1 AND cooldown_type = $2
                """, user_id, cooldown_type)
                
                logger.info(f"Cooldown reset: {user_id}/{cooldown_type}")
                return "DELETE 0" not in result
                
        except Exception as e:
            logger.error(f"Erreur reset_cooldown {user_id}/{cooldown_type}: {e}")
            return False
    
    async def get_user_cooldowns(self, user_id: int) -> Dict[str, float]:
        """
        Récupère tous les cooldowns actifs d'un utilisateur
        
        Returns:
            Dict[str, float]: {cooldown_type: seconds_remaining}
        """
        if not self.db.pool:
            return {}
            
        try:
            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT cooldown_type, last_used, expires_at 
                    FROM cooldowns 
                    WHERE user_id = $1
                """, user_id)
                
                now = datetime.now(timezone.utc)
                active_cooldowns = {}
                
                for row in rows:
                    cooldown_type = row['cooldown_type']
                    default_duration = self.COOLDOWN_TYPES.get(cooldown_type, 60)
                    
                    # Calculer temps restant
                    if row['expires_at']:
                        remaining = (row['expires_at'] - now).total_seconds()
                    else:
                        elapsed = (now - row['last_used']).total_seconds()
                        remaining = default_duration - elapsed
                    
                    if remaining > 0:
                        active_cooldowns[cooldown_type] = remaining
                
                return active_cooldowns
                
        except Exception as e:
            logger.error(f"Erreur get_user_cooldowns {user_id}: {e}")
            return {}
    
    async def cleanup_expired(self) -> int:
        """
        Nettoie les cooldowns expirés (appelé périodiquement)
        
        Returns:
            int: Nombre de cooldowns supprimés
        """
        if not self.db.pool:
            return 0
            
        try:
            async with self.db.pool.acquire() as conn:
                result = await conn.fetchval("SELECT cleanup_expired_cooldowns()")
                logger.debug(f"Cooldowns cleanup: {result} expired entries removed")
                return result or 0
                
        except Exception as e:
            logger.error(f"Erreur cleanup_expired: {e}")
            return 0
    
    async def get_cooldown_stats(self) -> Dict[str, Any]:
        """
        Statistiques sur l'utilisation des cooldowns
        """
        if not self.db.pool:
            return {}
            
        try:
            async with self.db.pool.acquire() as conn:
                stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total_cooldowns,
                        COUNT(DISTINCT user_id) as unique_users,
                        COUNT(CASE WHEN expires_at > NOW() THEN 1 END) as active_cooldowns
                    FROM cooldowns
                """)
                
                # Stats par type
                type_stats = await conn.fetch("""
                    SELECT cooldown_type, COUNT(*) as count
                    FROM cooldowns 
                    WHERE expires_at > NOW()
                    GROUP BY cooldown_type
                    ORDER BY count DESC
                """)
                
                return {
                    'total_cooldowns': stats['total_cooldowns'],
                    'unique_users': stats['unique_users'], 
                    'active_cooldowns': stats['active_cooldowns'],
                    'by_type': {row['cooldown_type']: row['count'] for row in type_stats}
                }
                
        except Exception as e:
            logger.error(f"Erreur get_cooldown_stats: {e}")
            return {}

    # Méthodes de compatibilité pour migration progressive
    async def check_daily_cooldown(self, user_id: int) -> float:
        """Compatibilité avec l'ancien système daily"""
        return await self.check_cooldown(user_id, 'daily', 86400)
    
    async def set_daily_cooldown(self, user_id: int):
        """Compatibilité avec l'ancien système daily"""
        await self.set_cooldown(user_id, 'daily', 86400)
    
    async def check_give_cooldown(self, user_id: int) -> float:
        """Compatibilité avec l'ancien système give"""
        return await self.check_cooldown(user_id, 'give', 5)
    
    async def set_give_cooldown(self, user_id: int):
        """Compatibilité avec l'ancien système give"""
        await self.set_cooldown(user_id, 'give', 5)

# Helper pour les décorateurs de cooldown
def cooldown_check(cooldown_type: str, duration: Optional[int] = None):
    """
    Décorateur pour vérifier les cooldowns persistants
    
    Usage:
        @cooldown_check('daily', 86400)
        async def daily_command(self, ctx):
            # La commande ne s'exécute que si le cooldown est OK
    """
    def decorator(func):
        async def wrapper(self, ctx_or_interaction, *args, **kwargs):
            user_id = getattr(ctx_or_interaction, 'user', ctx_or_interaction.author).id
            
            # Vérifier le cooldown
            if hasattr(self.bot, 'persistent_cooldowns'):
                remaining = await self.bot.persistent_cooldowns.check_cooldown(
                    user_id, cooldown_type, duration
                )
                
                if remaining > 0:
                    # Envoyer message de cooldown
                    embed = discord.Embed(
                        title="⏰ Cooldown actif !",
                        description=f"Tu pourras utiliser cette commande dans **{remaining:.1f}s**",
                        color=0xff9900
                    )
                    
                    if hasattr(ctx_or_interaction, 'response'):
                        await ctx_or_interaction.response.send_message(embed=embed, ephemeral=True)
                    else:
                        await ctx_or_interaction.send(embed=embed)
                    return
                
                # Activer le cooldown après exécution réussie
                await func(self, ctx_or_interaction, *args, **kwargs)
                await self.bot.persistent_cooldowns.set_cooldown(user_id, cooldown_type, duration)
            else:
                # Fallback si le système n'est pas initialisé
                await func(self, ctx_or_interaction, *args, **kwargs)
        
        return wrapper
    return decorator
