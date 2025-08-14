import asyncio
import asyncpg
from dotenv import load_dotenv
import os
import json

# Charger les variables d'environnement
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def check_shop_items():
    """Vérifie tous les items du shop"""
    
    if not DATABASE_URL:
        print("❌ DATABASE_URL manquant dans le fichier .env")
        return
    
    try:
        # Connexion à la base de données
        conn = await asyncpg.connect(dsn=DATABASE_URL)
        print("✅ Connecté à la base de données")
        
        # Récupérer TOUS les items du shop (actifs et inactifs)
        all_items = await conn.fetch("""
            SELECT id, name, description, price, type, data, is_active, created_at 
            FROM shop_items 
            ORDER BY created_at DESC
        """)
        
        if not all_items:
            print("❌ Aucun item trouvé dans la base de données !")
            print("🔧 Solution : Exécutez le script setup_cooldown_reset.py")
        else:
            print(f"📊 **{len(all_items)} item(s) trouvé(s) dans la base de données :**\n")
            
            active_items = [item for item in all_items if item['is_active']]
            inactive_items = [item for item in all_items if not item['is_active']]
            
            print(f"✅ **ITEMS ACTIFS ({len(active_items)}) :**")
            for item in active_items:
                print(f"   📋 ID: {item['id']} | Nom: '{item['name']}'")
                print(f"      💰 Prix: {item['price']} PB | Type: {item['type']}")
                print(f"      📅 Créé: {item['created_at'].strftime('%d/%m/%Y %H:%M')}")
                
                # Décoder les données JSON
                if item['data']:
                    try:
                        data = json.loads(item['data']) if isinstance(item['data'], str) else item['data']
                        print(f"      📊 Données: {data}")
                    except:
                        print(f"      ⚠️ Données brutes: {item['data']}")
                print()
            
            if inactive_items:
                print(f"❌ **ITEMS INACTIFS ({len(inactive_items)}) :**")
                for item in inactive_items:
                    print(f"   📋 ID: {item['id']} | Nom: '{item['name']}' | Type: {item['type']}")
                print()
            
            # Rechercher spécifiquement les items cooldown_reset
            cooldown_items = [item for item in all_items if item['type'] == 'cooldown_reset']
            if cooldown_items:
                print("⏰ **ITEMS RESET COOLDOWNS TROUVÉS :**")
                for item in cooldown_items:
                    status = "✅ ACTIF" if item['is_active'] else "❌ INACTIF"
                    print(f"   📋 ID: {item['id']} | {status}")
                    print(f"      📝 Nom: '{item['name']}'")
                    print(f"      💰 Prix: {item['price']} PrissBucks")
                    print(f"      📅 Créé: {item['created_at'].strftime('%d/%m/%Y %H:%M')}")
            else:
                print("❌ **AUCUN ITEM RESET COOLDOWNS TROUVÉ !**")
                print("🔧 Solution : Exécutez le script setup_cooldown_reset.py")
        
        await conn.close()
        print("🔌 Connexion fermée")
        
    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(check_shop_items())
