import os
import sys

print("🚀 Installation des dépendances en cours...")

# S'assure que pip est installé et à jour
os.system("python3 -m ensurepip --upgrade")

# Installation des dépendances depuis ton requirements.txt
# (adapter le chemin si ton fichier est ailleurs)
os.system("python3 -m pip install --upgrade pip")
os.system("python3 -m pip install -r Test-ecobot-main/requirements.txt")

# Vérifie que discord.py (ou py-cord) est bien présent
try:
    import discord
    print("✅ Module discord installé avec succès !")
except ImportError:
    print("❌ Module discord introuvable, vérifie le fichier requirements.txt")

print("🎉 Installation terminée ! Tu peux maintenant relancer ton bot normalement.")
