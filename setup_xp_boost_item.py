import asyncio
import asyncpg
import json
from dotenv import load_dotenv
import os

# Charger les variables d'environnement
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def setup_xp_boost_item():
    """Script pour ajouter l'item XP Boost au shop"""
    
    if not DATABASE_URL:
        print("❌ DATABASE_URL manquant dans le fichier .env")
        return
    
    try:
        # Connexion à la base de données
        conn = await asyncpg.connect(dsn=DATABASE_URL)
        print("✅ Connecté à la base de données")
        
        # Données de l'item XP Boost
        item_data = {
            "name": "⚡ XP Boost",
            "description": "Gagne instantanément 1000 XP via Arcane Premium ! Le bot enverra automatiquement la commande `/xp add` dans le canal. - Usage immédiat à l'achat",
            "price": 50,  # Prix de base (sans taxe)
            "type": "xp_boost",
            "data": json.dumps({
                "instant_use": True,
                "effect": "send_xp_command", 
                "xp_amount": 1000,
                "description": "Envoie automatiquement la commande /xp add à Arcane Premium après l'achat"
            })
        }
        
        # Vérifier si un item XP Boost existe déjà
        existing = await conn.fetchrow("""
            SELECT id, name, price FROM shop_items 
            WHERE type = 'xp_boost' AND is_active = TRUE
        """)
        
        if existing:
            print(f"⚠️ Un item XP Boost existe déjà :")
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
        
        print(f"✅ Item 'XP Boost' ajouté avec succès !")
        print(f"   📋 ID: {item_id}")
        print(f"   💰 Prix: {item_data['price']} PrissBucks (base, sans taxe)")
        print(f"   🏛️ Prix avec taxe 5%: {item_data['price'] + int(item_data['price'] * 0.05)} PrissBucks")
        print(f"   🎯 Type: {item_data['type']}")
        print(f"   ⚡ XP donné: 1000")
        print(f"   📝 Description: {item_data['description']}")
        print()
        print("🎮 **Comment l'item fonctionne :**")
        print("   • L'utilisateur achète l'item avec `/buy` ou `e!buy`")
        print("   • Le bot envoie automatiquement `/xp add @user 1000` dans le canal")
        print("   • Arcane Premium détecte et exécute la commande")
        print("   • L'utilisateur reçoit ses 1000 XP instantanément")
        print()
        print("📊 **Avantages :**")
        print("   • Aucune configuration XP nécessaire sur ton bot")
        print("   • Utilise directement Arcane Premium")
        print("   • Commande visible pour transparence")
        print("   • Fonctionne avec tous les systèmes XP d'Arcane")
        
        await conn.close()
        print("🔌 Connexion fermée")
        
    except Exception as e:
        print(f"❌ Erreur: {e}")

async def verify_item_setup():
    """Vérifie que l'item a été correctement ajouté"""
    try:
        conn = await asyncpg.connect(dsn=DATABASE_URL)
        
        # Récupérer tous les items xp_boost actifs
        items = await conn.fetch("""
            SELECT id, name, description, price, type, data, created_at 
            FROM shop_items 
            WHERE type = 'xp_boost' AND is_active = TRUE
            ORDER BY created_at DESC
        """)
        
        print("\n🔍 **Vérification des items XP Boost :**")
        if not items:
            print("❌ Aucun item XP Boost trouvé")
        else:
            for item in items:
                print(f"   ✅ ID: {item['id']} | Nom: {item['name']}")
                print(f"      💰 Prix: {item['price']} PB | Date: {item['created_at'].strftime('%d/%m/%Y %H:%M')}")
                
                # Vérifier les données JSON
                try:
                    data = json.loads(item['data']) if item['data'] else {}
                    print(f"      📊 XP donné: {data.get('xp_amount', 'Non défini')}")
                    print(f"      ⚡ Effet: {data.get('effect', 'Non défini')}")
                except json.JSONDecodeError:
                    print(f"      ⚠️ Données JSON invalides: {item['data']}")
                print()
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ Erreur lors de la vérification: {e}")

async def main():
    """Fonction principale"""
    print("⚡ **Setup Item XP Boost**")
    print("=" * 50)
    
    # 1. Ajouter l'item
    await setup_xp_boost_item()
    
    # 2. Vérifier que ça a marché
    await verify_item_setup()
    
    print("=" * 50)
    print("🎉 **Setup terminé !**")
    print()
    print("📋 **Prochaines étapes :**")
    print("1. Assure-toi qu'Arcane Premium est dans ton serveur")
    print("2. Lance ton bot avec le nouveau shop.py")
    print("3. Utilise `/shop` pour voir l'item XP Boost")
    print("4. Teste l'achat avec `/buy <id>`")
    print("5. Vérifie que la commande `/xp add` est bien envoyée")
    print("6. Arcane Premium devrait automatiquement ajouter l'XP")
    print()
    print("⚠️ **Prérequis :** Arcane Premium doit être configuré et actif sur le serveur !")

if __name__ == "__main__":
    asyncio.run(main())
