# Utiliser Python 3.11 slim comme image de base
FROM python:3.11-slim

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers de requirements en premier (pour optimiser le cache Docker)
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Copier tout le code source
COPY . .

# Créer un utilisateur non-root pour la sécurité
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

# Exposer le port pour le health check
EXPOSE 3000

# Commande par défaut pour lancer le bot
CMD ["python", "main.py"]
