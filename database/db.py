import asyncpg
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any
import json

logger = logging.getLogger(__name__)

class DatabaseError(Exception):
    """Exception personnalisée pour les erreurs de base de données"""
    pass

class Database:
    """
    Classe de base de données simplifiée et robuste.
    Focus sur la clarté, simplicité et fiabilité.
    """
    
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("DSN requis pour la base de données")
        
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        """Connexion à la base de données avec initialisation automatique"""
        try:
            self.pool = await asyncpg.create_pool(dsn=self.dsn)
            await self._init_tables()
            logger.info("✅ Base de données connectée et initialisée")
        except Exception as e:
            logger.error(f"❌ Erreur connexion DB: {e}")
            raise DatabaseError(f"Connexion échouée: {e}")
    
    async def close(self):
        """Fermeture propre de la base de données"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("🔌 Base de données fermée")
    
    def _ensure_connected(self):
        """Vérifie que la base de données est connectée"""
        if not self.pool:
            raise DatabaseError("Base de données non connectée")
    
    async def _init_tables(self):
        """Initialise toutes les tables nécessaires"""
        self._ensure_connected()
        
        async with self.pool.acquire() as conn:
            # Table utilisateurs (table principale)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    balance BIGINT DEFAULT 0 CHECK (balance >= 0),
                    last_daily TIMESTAMP WITH TIME ZONE
                )
            ''')
            
            # Table items boutique
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS shop_items (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    price BIGINT NOT NULL CHECK (price > 0),
                    type VARCHAR(50) NOT NULL,
                    data JSONB DEFAULT '{}',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            
            # Table achats utilisateurs
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_purchases (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    item_id INTEGER REFERENCES shop_items(id),
                    purchase_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    price_paid BIGINT NOT NULL CHECK (price_paid > 0),
                    tax_paid BIGINT DEFAULT 0 CHECK (tax_paid >= 0)
                )
            ''')
            
            # Index pour performances
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_balance ON users(balance DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_purchases_user ON user_purchases(user_id)')
            
            logger.info("✅ Tables initialisées")
    
    # ==================== MÉTHODES ÉCONOMIE SIMPLIFIÉES ====================
    
    async def get_balance(self, user_id: int) -> int:
        """Récupère le solde d'un utilisateur (méthode la plus utilisée)"""
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE user_id = $1", 
                    user_id
                )
                return row['balance'] if row else 0
        except Exception as e:
            logger.error(f"Erreur get_balance {user_id}: {e}")
            return 0
    
    async def update_balance(self, user_id: int, amount: int):
        """
        Met à jour le solde d'un utilisateur.
        Crée l'utilisateur si nécessaire.
        """
        if amount == 0:
            return  # Optimisation : ne rien faire si montant = 0
        
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO users (user_id, balance) VALUES ($1, $2)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET balance = GREATEST(0, users.balance + $2)
                ''', user_id, amount)
        except Exception as e:
            logger.error(f"Erreur update_balance {user_id}: {e}")
            raise DatabaseError(f"Échec mise à jour solde: {e}")
    
    async def set_balance(self, user_id: int, amount: int):
        """Définit le solde exact d'un utilisateur"""
        if amount < 0:
            raise ValueError("Le solde ne peut pas être négatif")
        
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO users (user_id, balance) VALUES ($1, $2)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET balance = $2
                ''', user_id, amount)
        except Exception as e:
            logger.error(f"Erreur set_balance {user_id}: {e}")
            raise DatabaseError(f"Échec définition solde: {e}")
    
    async def transfer(self, from_user: int, to_user: int, amount: int) -> bool:
        """
        Transfert simple entre deux utilisateurs.
        Transaction atomique garantie.
        """
        if amount <= 0:
            return False
        
        if from_user == to_user:
            return False
        
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Vérifier et débiter
                    sender_balance = await conn.fetchval(
                        "SELECT balance FROM users WHERE user_id = $1", 
                        from_user
                    )
                    
                    if not sender_balance or sender_balance < amount:
                        return False
                    
                    # Effectuer le transfert
                    await conn.execute(
                        "UPDATE users SET balance = balance - $1 WHERE user_id = $2", 
                        amount, from_user
                    )
                    
                    await conn.execute('''
                        INSERT INTO users (user_id, balance) VALUES ($1, $2)
                        ON CONFLICT (user_id) 
                        DO UPDATE SET balance = users.balance + $2
                    ''', to_user, amount)
                    
                    return True
        except Exception as e:
            logger.error(f"Erreur transfer {from_user} -> {to_user}: {e}")
            return False
    
    async def transfer_with_tax(self, from_user: int, to_user: int, amount: int, 
                              tax_rate: float, tax_recipient: int) -> Tuple[bool, Dict]:
        """
        Transfert avec taxe - version simplifiée.
        Retourne (succès, info_détaillée).
        """
        if amount <= 0 or tax_rate < 0:
            return False, {"error": "Paramètres invalides"}
        
        # Calculs simples
        tax_amount = int(amount * tax_rate)
        net_amount = amount - tax_amount
        
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Vérifier le solde
                    sender_balance = await conn.fetchval(
                        "SELECT balance FROM users WHERE user_id = $1", 
                        from_user
                    )
                    
                    if not sender_balance or sender_balance < amount:
                        return False, {"error": "Solde insuffisant"}
                    
                    # Débiter l'expéditeur
                    await conn.execute(
                        "UPDATE users SET balance = balance - $1 WHERE user_id = $2", 
                        amount, from_user
                    )
                    
                    # Créditer le destinataire (montant net)
                    await conn.execute('''
                        INSERT INTO users (user_id, balance) VALUES ($1, $2)
                        ON CONFLICT (user_id) 
                        DO UPDATE SET balance = users.balance + $2
                    ''', to_user, net_amount)
                    
                    # Créditer la taxe si elle existe
                    if tax_amount > 0 and tax_recipient:
                        await conn.execute('''
                            INSERT INTO users (user_id, balance) VALUES ($1, $2)
                            ON CONFLICT (user_id) 
                            DO UPDATE SET balance = users.balance + $2
                        ''', tax_recipient, tax_amount)
                    
                    return True, {
                        "gross_amount": amount,
                        "net_amount": net_amount,
                        "tax_amount": tax_amount,
                        "tax_rate": tax_rate * 100
                    }
        except Exception as e:
            logger.error(f"Erreur transfer_with_tax {from_user} -> {to_user}: {e}")
            return False, {"error": str(e)}
    
    # ==================== DAILY SYSTEM SIMPLIFIÉ ====================
    
    async def get_last_daily(self, user_id: int) -> Optional[datetime]:
        """Récupère la dernière fois que l'utilisateur a fait son daily"""
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT last_daily FROM users WHERE user_id = $1", 
                    user_id
                )
                return row['last_daily'] if row else None
        except Exception as e:
            logger.error(f"Erreur get_last_daily {user_id}: {e}")
            return None
    
    async def set_last_daily(self, user_id: int, timestamp: datetime):
        """Met à jour la dernière fois que l'utilisateur a fait son daily"""
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO users (user_id, last_daily) VALUES ($1, $2)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET last_daily = $2
                ''', user_id, timestamp)
        except Exception as e:
            logger.error(f"Erreur set_last_daily {user_id}: {e}")
            raise DatabaseError(f"Échec update daily: {e}")
    
    # ==================== CLASSEMENT SIMPLIFIÉ ====================
    
    async def get_top_users(self, limit: int = 10) -> List[Tuple[int, int]]:
        """Récupère le top des utilisateurs les plus riches"""
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT user_id, balance 
                    FROM users 
                    WHERE balance > 0 
                    ORDER BY balance DESC 
                    LIMIT $1
                ''', min(limit, 100))  # Limite de sécurité
                
                return [(row['user_id'], row['balance']) for row in rows]
        except Exception as e:
            logger.error(f"Erreur get_top_users: {e}")
            return []
    
    # ==================== BOUTIQUE SIMPLIFIÉE ====================
    
    async def get_shop_items(self, active_only: bool = True) -> List[Dict]:
        """Récupère la liste des items du shop"""
        self._ensure_connected()
        
        try:
            query = "SELECT * FROM shop_items"
            if active_only:
                query += " WHERE is_active = TRUE"
            query += " ORDER BY price ASC"
            
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query)
                
                items = []
                for row in rows:
                    item = dict(row)
                    # Convertir JSONB en dict Python
                    if isinstance(item['data'], str):
                        try:
                            item['data'] = json.loads(item['data'])
                        except (json.JSONDecodeError, TypeError):
                            item['data'] = {}
                    items.append(item)
                
                return items
        except Exception as e:
            logger.error(f"Erreur get_shop_items: {e}")
            return []
    
    async def get_shop_item(self, item_id: int) -> Optional[Dict]:
        """Récupère un item spécifique du shop"""
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM shop_items WHERE id = $1", 
                    item_id
                )
                
                if not row:
                    return None
                
                item = dict(row)
                # Convertir JSONB
                if isinstance(item['data'], str):
                    try:
                        item['data'] = json.loads(item['data'])
                    except (json.JSONDecodeError, TypeError):
                        item['data'] = {}
                
                return item
        except Exception as e:
            logger.error(f"Erreur get_shop_item {item_id}: {e}")
            return None
    
    async def add_shop_item(self, name: str, description: str, price: int, 
                          item_type: str, data: Dict) -> int:
        """Ajoute un item au shop"""
        if price <= 0:
            raise ValueError("Le prix doit être positif")
        
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    INSERT INTO shop_items (name, description, price, type, data)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                ''', name, description, price, item_type, json.dumps(data))
                
                return row['id']
        except Exception as e:
            logger.error(f"Erreur add_shop_item: {e}")
            raise DatabaseError(f"Échec ajout item: {e}")
    
    async def purchase_item_with_tax(self, user_id: int, item_id: int, 
                                   tax_rate: float, tax_recipient: int) -> Tuple[bool, str, Dict]:
        """
        Effectue un achat avec taxe - version simplifiée.
        Transaction atomique garantie.
        """
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Récupérer l'item
                    item = await conn.fetchrow('''
                        SELECT id, name, price, type, data 
                        FROM shop_items 
                        WHERE id = $1 AND is_active = TRUE
                    ''', item_id)
                    
                    if not item:
                        return False, "Item inexistant ou inactif", {}
                    
                    # Vérifier si l'utilisateur a déjà acheté (pour les rôles)
                    if item['type'] == "role":
                        existing = await conn.fetchval('''
                            SELECT 1 FROM user_purchases 
                            WHERE user_id = $1 AND item_id = $2
                        ''', user_id, item_id)
                        
                        if existing:
                            return False, "Tu possèdes déjà cet item", {}
                    
                    # Calculer les prix
                    base_price = item['price']
                    tax_amount = int(base_price * tax_rate)
                    total_price = base_price + tax_amount
                    
                    # Vérifier le solde
                    user_balance = await conn.fetchval(
                        "SELECT balance FROM users WHERE user_id = $1", 
                        user_id
                    ) or 0
                    
                    if user_balance < total_price:
                        return False, f"Solde insuffisant (besoin: {total_price:,})", {}
                    
                    # Effectuer l'achat
                    await conn.execute(
                        "UPDATE users SET balance = balance - $1 WHERE user_id = $2", 
                        total_price, user_id
                    )
                    
                    # Créditer la taxe
                    if tax_amount > 0 and tax_recipient:
                        await conn.execute('''
                            INSERT INTO users (user_id, balance) VALUES ($1, $2)
                            ON CONFLICT (user_id) 
                            DO UPDATE SET balance = users.balance + $2
                        ''', tax_recipient, tax_amount)
                    
                    # Enregistrer l'achat
                    await conn.execute('''
                        INSERT INTO user_purchases (user_id, item_id, price_paid, tax_paid)
                        VALUES ($1, $2, $3, $4)
                    ''', user_id, item_id, total_price, tax_amount)
                    
                    return True, f"Achat de '{item['name']}' réussi", {
                        "base_price": base_price,
                        "tax_amount": tax_amount,
                        "total_price": total_price,
                        "tax_rate": tax_rate * 100
                    }
        except Exception as e:
            logger.error(f"Erreur purchase_item_with_tax {user_id}: {e}")
            return False, f"Erreur technique: {e}", {}
    
    async def get_user_purchases(self, user_id: int) -> List[Dict]:
        """Récupère l'historique des achats d'un utilisateur"""
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT up.purchase_date, up.price_paid, up.tax_paid,
                           si.name, si.description, si.type, si.data
                    FROM user_purchases up
                    JOIN shop_items si ON up.item_id = si.id
                    WHERE up.user_id = $1
                    ORDER BY up.purchase_date DESC
                    LIMIT 50
                ''', user_id)
                
                purchases = []
                for row in rows:
                    purchase = dict(row)
                    # Convertir JSONB
                    if isinstance(purchase['data'], str):
                        try:
                            purchase['data'] = json.loads(purchase['data'])
                        except (json.JSONDecodeError, TypeError):
                            purchase['data'] = {}
                    purchases.append(purchase)
                
                return purchases
        except Exception as e:
            logger.error(f"Erreur get_user_purchases {user_id}: {e}")
            return []
    
    async def get_shop_stats(self) -> Dict:
        """Récupère les statistiques simples du shop"""
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                # Statistiques de base
                stats = await conn.fetchrow('''
                    SELECT 
                        COUNT(DISTINCT up.user_id) as unique_buyers,
                        COUNT(up.id) as total_purchases,
                        COALESCE(SUM(up.price_paid), 0) as total_revenue,
                        COALESCE(SUM(up.tax_paid), 0) as total_taxes
                    FROM user_purchases up
                ''')
                
                # Top items
                top_items = await conn.fetch('''
                    SELECT si.name, COUNT(up.id) as purchases, 
                           SUM(up.price_paid) as revenue
                    FROM user_purchases up
                    JOIN shop_items si ON up.item_id = si.id
                    GROUP BY si.id, si.name
                    ORDER BY purchases DESC
                    LIMIT 5
                ''')
                
                return {
                    "unique_buyers": stats['unique_buyers'] or 0,
                    "total_purchases": stats['total_purchases'] or 0,
                    "total_revenue": stats['total_revenue'] or 0,
                    "total_taxes": stats['total_taxes'] or 0,
                    "top_items": [dict(row) for row in top_items]
                }
        except Exception as e:
            logger.error(f"Erreur get_shop_stats: {e}")
            return {"unique_buyers": 0, "total_purchases": 0, "total_revenue": 0, "total_taxes": 0, "top_items": []}
    
    # ==================== MÉTHODES UTILITAIRES ====================
    
    async def deactivate_shop_item(self, item_id: int) -> bool:
        """Désactive un item du shop"""
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE shop_items SET is_active = FALSE WHERE id = $1", 
                    item_id
                )
                return result != "UPDATE 0"
        except Exception as e:
            logger.error(f"Erreur deactivate_shop_item {item_id}: {e}")
            return False
    
    async def has_purchased_item(self, user_id: int, item_id: int) -> bool:
        """Vérifie si un utilisateur a déjà acheté un item"""
        self._ensure_connected()
        
        try:
            async with self.pool.acquire() as conn:
                exists = await conn.fetchval('''
                    SELECT 1 FROM user_purchases 
                    WHERE user_id = $1 AND item_id = $2 
                    LIMIT 1
                ''', user_id, item_id)
                return bool(exists)
        except Exception as e:
            logger.error(f"Erreur has_purchased_item {user_id}: {e}")
            return False