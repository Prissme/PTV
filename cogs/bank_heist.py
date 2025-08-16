# Modifications à apporter au fichier bank_heist.py
# Remplacer les méthodes de cooldown par ces versions persistantes

import time
from datetime import datetime, timezone

class BankHeist(commands.Cog):
    # ... (reste du code identique)
    
    async def _check_heist_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown avec persistance en base de données"""
        if not self.db.pool:
            return 0
            
        try:
            async with self.db.pool.acquire() as conn:
                # Récupère le dernier braquage depuis la table cooldowns
                row = await conn.fetchrow("""
                    SELECT last_used 
                    FROM cooldowns 
                    WHERE user_id = $1 AND cooldown_type = 'bank_heist'
                """, user_id)
                
                if row and row['last_used']:
                    now = datetime.now(timezone.utc)
                    elapsed = (now - row['last_used']).total_seconds()
                    
                    if elapsed < self.COOLDOWN_SECONDS:
                        return self.COOLDOWN_SECONDS - elapsed
                
                return 0
                
        except Exception as e:
            logger.error(f"Erreur vérification cooldown braquage {user_id}: {e}")
            # Fallback vers l'ancien système en mémoire si la BDD échoue
            return self._check_heist_cooldown_memory(user_id)

    async def _set_heist_cooldown(self, user_id: int):
        """Met en cooldown avec persistance en base de données"""
        if not self.db.pool:
            # Fallback vers mémoire si pas de BDD
            self._set_heist_cooldown_memory(user_id)
            return
            
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO cooldowns (user_id, cooldown_type, last_used)
                    VALUES ($1, 'bank_heist', NOW())
                    ON CONFLICT (user_id, cooldown_type) 
                    DO UPDATE SET last_used = NOW()
                """, user_id)
                
            # Garder aussi en mémoire pour les vérifications rapides
            self._set_heist_cooldown_memory(user_id)
            
            logger.debug(f"Cooldown braquage persisté pour user {user_id}")
            
        except Exception as e:
            logger.error(f"Erreur sauvegarde cooldown braquage {user_id}: {e}")
            # Fallback vers mémoire en cas d'erreur BDD
            self._set_heist_cooldown_memory(user_id)

    # Méthodes de fallback pour la compatibilité
    def _check_heist_cooldown_memory(self, user_id: int) -> float:
        """Version mémoire du cooldown (fallback)"""
        now = time.time()
        if user_id in self.heist_cooldowns:
            elapsed = now - self.heist_cooldowns[user_id]
            if elapsed < self.COOLDOWN_SECONDS:
                return self.COOLDOWN_SECONDS - elapsed
        return 0

    def _set_heist_cooldown_memory(self, user_id: int):
        """Version mémoire du cooldown (fallback)"""
        self.heist_cooldowns[user_id] = time.time()

    # NOUVELLE : Méthode pour migrer les cooldowns mémoire vers BDD
    async def migrate_memory_cooldowns_to_db(self):
        """Migre les cooldowns en mémoire vers la base de données"""
        if not self.db.pool or not self.heist_cooldowns:
            return
            
        try:
            current_time = time.time()
            migrated = 0
            
            async with self.db.pool.acquire() as conn:
                for user_id, timestamp in list(self.heist_cooldowns.items()):
                    # Ne migrer que les cooldowns encore actifs
                    if current_time - timestamp < self.COOLDOWN_SECONDS:
                        cooldown_datetime = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                        
                        await conn.execute("""
                            INSERT INTO cooldowns (user_id, cooldown_type, last_used)
                            VALUES ($1, 'bank_heist', $2)
                            ON CONFLICT (user_id, cooldown_type) 
                            DO UPDATE SET last_used = GREATEST(cooldowns.last_used, EXCLUDED.last_used)
                        """, user_id, cooldown_datetime)
                        
                        migrated += 1
            
            logger.info(f"🔄 Migration: {migrated} cooldowns braquage migrés vers BDD")
            
        except Exception as e:
            logger.error(f"Erreur migration cooldowns braquage: {e}")

    # Appeler cette méthode au démarrage du cog
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        
        # Migration automatique des cooldowns existants
        await self.migrate_memory_cooldowns_to_db()
        
        logger.info(f"✅ Cog BankHeist initialisé avec cooldowns persistants")

# Si tu n'as pas encore la table cooldowns, voici le SQL pour la créer :
"""
CREATE TABLE IF NOT EXISTS cooldowns (
    user_id BIGINT NOT NULL,
    cooldown_type VARCHAR(50) NOT NULL,
    last_used TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (user_id, cooldown_type)
);

-- Index pour optimiser les requêtes
CREATE INDEX IF NOT EXISTS idx_cooldowns_user_type ON cooldowns(user_id, cooldown_type);
CREATE INDEX IF NOT EXISTS idx_cooldowns_last_used ON cooldowns(last_used);
"""

# BONUS : Commande admin pour voir les cooldowns actifs
class BankHeist(commands.Cog):
    # ... (méthodes précédentes)
    
    @commands.command(name='heist_cooldowns')
    @commands.has_permissions(administrator=True)
    async def heist_cooldowns_admin(self, ctx):
        """[ADMIN] Affiche les cooldowns de braquage actifs"""
        try:
            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT user_id, last_used,
                           EXTRACT(EPOCH FROM (NOW() - last_used)) as elapsed_seconds
                    FROM cooldowns 
                    WHERE cooldown_type = 'bank_heist'
                    AND EXTRACT(EPOCH FROM (NOW() - last_used)) < $1
                    ORDER BY last_used DESC
                    LIMIT 10
                """, self.COOLDOWN_SECONDS)
            
            if not rows:
                embed = discord.Embed(
                    title="🏦 Cooldowns Braquage",
                    description="Aucun cooldown de braquage actif",
                    color=Colors.INFO
                )
                await ctx.send(embed=embed)
                return
            
            embed = discord.Embed(
                title="🏦 Cooldowns Braquage Actifs",
                description=f"**{len(rows)}** utilisateur(s) en cooldown",
                color=Colors.WARNING
            )
            
            cooldown_list = []
            for row in rows:
                try:
                    user = self.bot.get_user(row['user_id'])
                    username = user.display_name if user else f"User#{row['user_id']}"
                except:
                    username = f"User#{row['user_id']}"
                
                remaining = self.COOLDOWN_SECONDS - row['elapsed_seconds']
                time_str = self._format_cooldown_time(remaining)
                
                cooldown_list.append(f"• **{username}** - {time_str}")
            
            embed.add_field(
                name="⏰ Temps restants",
                value="\n".join(cooldown_list[:10]),
                inline=False
            )
            
            embed.set_footer(text=f"Cooldown total: {self.COOLDOWN_HOURS}h | Stockage: Base de données")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur affichage cooldowns admin: {e}")
            embed = create_error_embed("Erreur", "Impossible de récupérer les cooldowns.")
            await ctx.send(embed=embed)