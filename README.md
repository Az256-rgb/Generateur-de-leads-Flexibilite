# Générateur de leads – Google Places + Surface OSM

## À quoi sert l’app

Cette application Streamlit permet de générer des leads B2B en :

1. Recherchant des établissements via l’API Google Places (text search) sur la France,
2. Enrichissant chaque fiche avec les coordonnées, le téléphone, le site web et le lien Google Maps,
3. Récupérant les géométries des bâtiments via l’API Overpass (OSM) autour de chaque lieu,
4. Calculant la surface en m² de chaque bâtiment,
5. Filtrant les résultats selon un seuil de surface et des filtres géographiques (région, département),
6. Visualisant les leads sur une carte interactive Folium,
7. Proposant l’export des données en CSV et l’export de la carte en HTML.

## Comment installer

1. Clonez le dépôt :

   ```bash
   git clone https://github.com/votre-utilisateur/leads-generator.git
   cd leads-generator
   ```
2. Installez les dépendances Python :

   ```bash
   pip install -r requirements.txt
   ```

## Où éditer le mot de passe et la clé API

Créez (ou éditez) le fichier `./streamlit/secrets.toml` (non versionné) et ajoutez :

```toml
APP_PASSWORD = "votre_mot_de_passe"
GOOGLE_API_KEY = "AIza..."
```

Votre mot de passe protège l’accès à l’application et la clé API permet d’interroger l’API Google Places.

## Comment lancer

Dans le dossier racine du projet, exécutez :

```bash
streamlit run app.py
```

L’interface s’ouvrira automatiquement dans votre navigateur par défaut. Vous pourrez alors renseigner votre mot de passe, définir vos filtres, lancer la recherche et exporter vos leads.")
