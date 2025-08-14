import asyncio
import asyncpg
import json
from dotenv import load_dotenv
import os

# Charger les variables d'environnement
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def setup_cooldown_reset_item():
    """Script pour ajouter l'item Reset Cooldowns au shop"""
    
    if not DATABASE_URL:
        print("❌ DATABASE_URL manquant dans le fichier .env")
        return
    
    try:
        # Connexion à la base de données
        conn = await asyncpg.connect(dsn=DATABASE_URL)
        print("✅ Connecté à la base de données")
        
        # Données de l'item
        item_data = {
            "name": "⏰ Reset Cooldowns",
            "description": "Désactive instantanément TOUS tes cooldowns en cours ! (Daily, Vol, Give, etc.)",
            "price": 200,
            "type": "cooldown_reset",
            "data": json.dumps({
                "instant_use": True,
                "description": "Remet à zéro tous les cooldowns du joueur"
            })
        }
        
        # Vérifier si l'item existe déjà
        existing = await conn.fetchrow("""
            SELECT id FROM shop_items 
            WHERE type = 'cooldown_reset' AND is_active = TRUE
        """)
        
        if existing:
            print(f"⚠️ Un item Reset Cooldowns existe déjà (ID: {existing['id']})")
            response = input("Voulez-vous le remplacer ? (o/N): ")
            if response.lower() != 'o':
                await conn.close()
                return
            
            # Désactiver l'ancien item
            await conn.execute("""
                UPDATE shop_items SET is_active = FALSE 
                WHERE id = $1
            """, existing['id'])
            print(f"✅ Ancien item désactivé")
        
        # Ajouter le nouvel item
        item_id = await conn.fetchval("""
            INSERT INTO shop_items (name, description, price, type, data, is_active)
            VALUES ($1, $2, $3, $4, $5, TRUE)
            RETURNING id
        """, 
        item_data["name"], 
        item_data["description"], 
        item_data["price"], 
        item_data["type"], 
        item_data["data"])
        
        print(f"✅ Item 'Reset Cooldowns' ajouté avec succès !")
        print(f"   📋 ID: {item_id}")
        print(f"   💰 Prix: {item_data['price']} PrissBucks")
        print(f"   🎯 Type: {item_data['type']}")
        print(f"   📝 Description: {item_data['description']}")
        
        await conn.close()
        print("🔌 Connexion fermée")
        
    except Exception as e:
        print(f"❌ Erreur: {e}")

if __name__ == "__main__":
    asyncio.run(setup_cooldown_reset_item())
