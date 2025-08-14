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
        
        # Données de l'item Reset Cooldowns
        item_data = {
            "name": "⏰ Reset Cooldowns",
            "description": "Désactive instantanément TOUS tes cooldowns en cours ! (Daily, Vol, Give, etc.) - Usage immédiat à l'achat",
            "price": 200,  # Prix de base (sans taxe)
            "type": "cooldown_reset",
            "data": json.dumps({
                "instant_use": True,
                "effect": "reset_all_cooldowns",
                "description": "Remet à zéro tous les cooldowns du joueur immédiatement après l'achat"
            })
        }
        
        # Vérifier si l'item existe déjà
        existing = await conn.fetchrow("""
            SELECT id, name, price FROM shop_items 
            WHERE type = 'cooldown_reset' AND is_active = TRUE
        """)
        
        if existing:
            print(f"⚠️ Un item Reset Cooldowns existe déjà :")
            print(f"   📋 ID: {existing['id']}")
            print(f"   📝 Nom: {existing['name']}")
            print(f"   💰 Prix: {existing['price']} PrissBucks")
            print()
            response = input("Voulez-vous le remplacer par la nouvelle version ? (o/N): ")
            if response.lower() != 'o':
                print("❌ Opération annulée")
                await conn.close()
                return
            
            # Désactiver l'ancien item
            await conn.execute("""
                UPDATE shop_items SET is_active = FALSE 
                WHERE id = $1
            """, existing['id'])
            print(f"✅ Ancien item désactivé (ID: {existing['id']})")
        
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
        print(f"   💰 Prix: {item_data['price']} PrissBucks (base, sans taxe)")
        print(f"   🏛️ Prix avec taxe 5%: {item_data['price'] + int(item_data['price'] * 0.05)} PrissBucks")
        print(f"   🎯 Type: {item_data['type']}")
        print(f"   📝 Description: {item_data['description']}")
        print()
        print("🎮 **Comment l'item fonctionne :**")
        print("   • L'utilisateur achète l'item avec `/buy` ou `e!buy`")
        print("   • L'effet se déclenche automatiquement après l'achat")
        print("   • Tous les cooldowns (daily, vol, give, etc.) sont supprimés")
        print("   • L'utilisateur peut immédiatement utiliser toutes ses commandes")
        print()
        print("📊 **Statistiques prévues :**")
        print("   • Prix attractif pour un effet puissant")
        print("   • Usage unique par achat (consommable)")
        print("   • Visible dans l'inventaire comme 'item consommable utilisé'")
        
        await conn.close()
        print("🔌 Connexion fermée")
        
    except Exception as e:
        print(f"❌ Erreur: {e}")

async def verify_item_setup():
    """Vérifie que l'item a été correctement ajouté"""
    try:
        conn = await asyncpg.connect(dsn=DATABASE_URL)
        
        # Récupérer tous les items cooldown_reset actifs
        items = await conn.fetch("""
            SELECT id, name, description, price, type, data, created_at 
            FROM shop_items 
            WHERE type = 'cooldown_reset' AND is_active = TRUE
            ORDER BY created_at DESC
        """)
        
        print("\n🔍 **Vérification des items Reset Cooldowns :**")
        if not items:
            print("❌ Aucun item Reset Cooldowns trouvé")
        else:
            for item in items:
                print(f"   ✅ ID: {item['id']} | Nom: {item['name']}")
                print(f"      💰 Prix: {item['price']} PB | Date: {item['created_at'].strftime('%d/%m/%Y %H:%M')}")
                
                # Vérifier les données JSON
                try:
                    data = json.loads(item['data']) if item['data'] else {}
                    print(f"      📊 Données: {data}")
                except json.JSONDecodeError:
                    print(f"      ⚠️ Données JSON invalides: {item['data']}")
                print()
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ Erreur lors de la vérification: {e}")

async def main():
    """Fonction principale"""
    print("🛍️ **Setup Item Reset Cooldowns**")
    print("=" * 50)
    
    # 1. Ajouter l'item
    await setup_cooldown_reset_item()
    
    # 2. Vérifier que ça a marché
    await verify_item_setup()
    
    print("=" * 50)
    print("🎉 **Setup terminé !**")
    print()
    print("📋 **Prochaines étapes :**")
    print("1. Lance ton bot")
    print("2. Utilise `/shop` pour voir l'item")
    print("3. Teste l'achat avec `/buy <id>`")
    print("4. Vérifie que les cooldowns sont supprimés avec `e!cooldowns`")

if __name__ == "__main__":
    asyncio.run(main())