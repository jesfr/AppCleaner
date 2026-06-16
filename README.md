# 🗑 AppCleaner

**Gérez et nettoyez vos applications Windows en toute simplicité.**

AppCleaner liste toutes vos applications installées, vous permet de filtrer celles que vous n'utilisez plus depuis un moment, de les sélectionner et de les désinstaller automatiquement — le tout dans une interface moderne.

---

## ⬇️ Téléchargement

> Pas besoin d'installer Python ou quoi que ce soit. Télécharge le fichier qui correspond à ton PC, double-clique dessus, c'est tout.

| Ton PC | Fichier à télécharger |
|---|---|
| **Windows 10** ou PC classique (Intel/AMD) | [AppCleaner-Windows10-x64.exe](https://github.com/jesfr/AppCleaner/releases/download/v2.0/AppCleaner-Windows10-x64.exe) |
| **Windows 11 ARM** (Surface Pro X, PC Snapdragon…) | [AppCleaner-Windows11-ARM64.exe](https://github.com/jesfr/AppCleaner/releases/download/v2.0/AppCleaner-Windows11-ARM64.exe) |

> ⚠️ **Windows peut afficher un avertissement SmartScreen** au premier lancement (app non signée). Clique sur **"Informations complémentaires"** puis **"Exécuter quand même"**.

---

## 🖥️ Fonctionnalités

### 🏪 Applications détectées
- Applications classiques (registre Windows, 32 et 64 bit)
- **Applications Microsoft Store** (apps tierces uniquement, les apps système Microsoft sont exclues)
- Applications portables (sans installateur)

### 🧹 Nettoyage
- **Liste toutes les applications installées** avec leur taille sur le disque, l'éditeur et l'emplacement
- **Filtre par durée d'inactivité** — 30 jours, 6 mois, 1 an, 2 ans…
- **Recherche** par nom ou éditeur
- **Tri** par nom, taille ou date d'utilisation
- **Cases à cocher** pour sélectionner les apps à supprimer
- **Désinstallation silencieuse** automatique (MSI, NSIS, Inno Setup…)
- **Apps portables** (sans installateur) : suppression du dossier entier
- **Bilan final** : nombre d'apps désinstallées + espace libéré
- Applications système exclues automatiquement (drivers, runtimes Microsoft…)

### 📅 Détection de date (améliorée)
- Source principale : **UserAssist** (registre Windows) — enregistre les vrais lancements de programmes
- Fallback : date d'accès des fichiers `.exe`

### 🗺️ Espace disque (style WinDirStat)
- Onglet dédié avec **treemap interactif** — chaque rectangle = une app, proportionnel à sa taille
- Couleurs par éditeur
- Tooltip au survol : nom, taille, dernière utilisation
- **Clic sur un rectangle** : fiche détaillée de l'app (éditeur, version, taille, dernière utilisation, emplacement, type) avec bouton de désinstallation directe
- Barre de stats du disque (utilisé / libre / total)

### ⬆️ Mise à jour
- Bouton **"Mettre à jour tout"** qui lance `winget upgrade --all` en un clic
- Affichage en temps réel de la progression dans une fenêtre dédiée
- Résumé du nombre de mises à jour effectuées à la fin

---

## 📸 Aperçu

![AppCleaner interface](app_cleaner_img.png)

---

## 🛠️ Pour les développeurs

### Prérequis

```
Python 3.10+
customtkinter >= 5.2.0
```

### Installation

```bash
git clone https://github.com/jesfr/AppCleaner.git
cd AppCleaner
pip install -r requirements.txt
python AppCleaner.py
```

### Compiler un exe

```bash
# Windows x64
pip install pyinstaller
pyinstaller --onefile --windowed --name "AppCleaner" AppCleaner.py
```

---

## 📋 Compatibilité

| OS | Architecture | Support |
|---|---|---|
| Windows 10 | x64 (Intel/AMD) | ✅ |
| Windows 11 | x64 (Intel/AMD) | ✅ |
| Windows 11 | ARM64 (Snapdragon) | ✅ |
| Windows 7/8 | — | ❌ |

---

## ⚠️ Avertissement

AppCleaner filtre les composants système connus, mais **vérifiez toujours la liste avant de désinstaller**. Certaines applications peuvent être des dépendances d'autres logiciels.

---

## 📄 Licence

MIT — libre d'utilisation, de modification et de distribution.
