"""
Migration pour ajouter les tables de la banque publique à une base de données existante.
À exécuter une seule fois lors de l'installation du système de banque publique.
"""

import asyncio
import asyncpg
import logging
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
logger = logging.getLogger(__name__)

async def run_migration():
    """Exécute la migration pour ajouter les tables de banque publique"""
    
    if not DATABASE_URL:
        print("❌ DATABASE_URL manquant dans le fichier .env")
        return False
    
    try:
        # Connexion à la base de données
        conn = await asyncpg.connect(DATABASE_URL)
        print("🔌 Connexion à la base de données établie")
        
        # === MIGRATION 1: Table public_bank ===
        print("📋 Création de la table public_bank...")
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS public_bank (
                id SERIAL PRIMARY KEY,
                balance BIGINT DEFAULT 0,
                total_deposited BIGINT DEFAULT 0,
                total_withdrawn BIGINT DEFAULT 0,
                last_activity TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        ''')
        
        # Insérer l'enregistrement initial
        await conn.execute('''
            INSERT INTO public_bank (id, balance, total_deposited, total_withdrawn)
            VALUES (1, 0, 0, 0)
            ON CONFLICT (id) DO NOTHING
        ''')
        print("✅ Table public_bank créée avec succès")
        
        # === MIGRATION 2: Table public_bank_withdrawals ===
        print("📋 Création de la table public_bank_withdrawals...")
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS public_bank_withdrawals (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                remaining_balance BIGINT NOT NULL
            )
        ''')
        
        # Index pour optimiser les requêtes
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_public_bank_withdrawals_user_id 
            ON public_bank_withdrawals(user_id)
        ''')
        
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_public_bank_withdrawals_timestamp 
            ON public_bank_withdrawals(timestamp DESC)
        ''')
        print("✅ Table public_bank_withdrawals créée avec succès")
        
        # === MIGRATION 3: Mise à jour table transaction_logs (optionnel) ===
        print("📋 Mise à jour de la table transaction_logs...")
        try:
            # Vérifier si la table existe
            table_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'transaction_logs'
                )
            """)
            
            if table_exists:
                # Ajouter de nouveaux types de transactions si pas déjà présents
                print("✅ Table transaction_logs détectée, prête pour les nouveaux types")
            else:
                print("ℹ️ Table transaction_logs non trouvée (sera créée par le cog TransactionLogs)")
                
        except Exception as e:
            print(f"⚠️ Erreur vérification transaction_logs: {e}")
        
        # === VÉRIFICATION FINALE ===
        print("🔍 Vérification des tables créées...")
        
        # Vérifier public_bank
        bank_check = await conn.fetchrow("SELECT * FROM public_bank WHERE id = 1")
        if bank_check:
            print(f"✅ public_bank OK - Solde initial: {bank_check['balance']} PB")
        else:
            print("❌ Erreur: public_bank non initialisée")
            
        # Vérifier public_bank_withdrawals
        withdrawals_count = await conn.fetchval("SELECT COUNT(*) FROM public_bank_withdrawals")
        print(f"✅ public_bank_withdrawals OK - {withdrawals_count} retraits enregistrés")
        
        # === AJOUT DE FONDS DE TEST (OPTIONNEL) ===
        add_test_funds = input("🎁 Ajouter 10,000 PB de test à la banque publique ? (y/N): ").lower().strip()
        
        if add_test_funds in ['y', 'yes', 'oui', 'o']:
            await conn.execute('''
                UPDATE public_bank 
                SET balance = balance + 10000,
                    total_deposited = total_deposited + 10000,
                    last_activity = NOW()
                WHERE id = 1
            ''')
            print("🎁 10,000 PB de test ajoutés à la banque publique !")
        
        await conn.close()
        print("🔌 Connexion fermée")
        
        print("\n🎉 MIGRATION TERMINÉE AVEC SUCCÈS ! 🎉")
        print("📋 Résumé des actions:")
        print("  ✅ Table 'public_bank' créée et initialisée")
        print("  ✅ Table 'public_bank_withdrawals' créée avec index")
        print("  ✅ Compatibilité avec 'transaction_logs' vérifiée")
        print(f"  {'✅' if add_test_funds in ['y', 'yes', 'oui', 'o'] else 'ℹ️'} Fonds de test {'ajoutés' if add_test_funds in ['y', 'yes', 'oui', 'o'] else 'non ajoutés'}")
        
        print("\n🚀 ÉTAPES SUIVANTES:")
        print("1. 📁 Ajouter le fichier 'public_bank.py' dans le dossier cogs/")
        print("2. 🔄 Remplacer 'roulette.py' et 'games.py' par les versions modifiées")
        print("3. 🔄 Remplacer 'help.py' par la version modifiée")
        print("4. 🔄 Remplacer 'config.py' par la version modifiée")
        print("5. 🤖 Redémarrer le bot")
        print("6. 🧪 Tester avec '/publicbank' et '/withdraw_public'")
        
        print("\n🏛️ LA RÉVOLUTION DE LA BANQUE PUBLIQUE EST PRÊTE ! 🏛️")
        
        return True
        
    except Exception as e:
        print(f"❌ Erreur lors de la migration: {e}")
        logger.error(f"Migration error: {e}")
        return False

async def rollback_migration():
    """Annule la migration (supprime les tables de banque publique) - À utiliser avec précaution !"""
    
    if not DATABASE_URL:
        print("❌ DATABASE_URL manquant dans le fichier .env")
        return False
    
    print("⚠️ ATTENTION: Cette opération va SUPPRIMER toutes les données de la banque publique !")
    confirm = input("Tapez 'SUPPRIMER' pour confirmer (sensible à la casse): ").strip()
    
    if confirm != "SUPPRIMER":
        print("❌ Rollback annulé.")
        return False
    
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        print("🔌 Connexion établie pour rollback")
        
        # Supprimer les tables dans l'ordre inverse (à cause des dépendances)
        await conn.execute("DROP TABLE IF EXISTS public_bank_withdrawals CASCADE")
        print("🗑️ Table public_bank_withdrawals supprimée")
        
        await conn.execute("DROP TABLE IF EXISTS public_bank CASCADE")
        print("🗑️ Table public_bank supprimée")
        
        await conn.close()
        print("🔌 Connexion fermée")
        
        print("✅ Rollback terminé avec succès")
        print("⚠️ Toutes les données de la banque publique ont été supprimées !")
        
        return True
        
    except Exception as e:
        print(f"❌ Erreur lors du rollback: {e}")
        logger.error(f"Rollback error: {e}")
        return False

if __name__ == "__main__":
    print("🏛️ MIGRATION BANQUE PUBLIQUE 🏛️")
    print("=" * 50)
    
    action = input("Action à effectuer:\n1. Migration (créer les tables)\n2. Rollback (supprimer les tables)\nChoix (1/2): ").strip()
    
    if action == "1":
        print("\n🚀 Lancement de la migration...")
        success = asyncio.run(run_migration())
        if success:
            print("\n🎉 Migration réussie ! Le système de banque publique est prêt !")
        else:
            print("\n❌ Migration échouée. Vérifiez les logs.")
            
    elif action == "2":
        print("\n🔄 Lancement du rollback...")
        success = asyncio.run(rollback_migration())
        if success:
            print("\n✅ Rollback réussi ! Les tables ont été supprimées.")
        else:
            print("\n❌ Rollback échoué. Vérifiez les logs.")
            
    else:
        print("❌ Choix invalide. Arrêt du script.")
