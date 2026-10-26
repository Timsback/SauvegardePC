# Sauvegarde PC

Application de bureau (Python + interface HTML/CSS Aurora dans WebView2)
qui reprend les étapes du guide « Sauvegarder son PC avant une
réinstallation de Windows » et automatise ce qui peut l'être.

## Lancer l'application

Le plus simple : télécharge puis exécute `SauvegardePC-Setup.exe` depuis [la dernière version GitHub](https://github.com/ARBJ-prevention/SauvegardePC/releases/latest) — un vrai
installeur Windows (assistant, choix du dossier, raccourcis, désinstallation
propre depuis Paramètres → Applications).

En développement, double-clique sur [`Lancer.bat`](Lancer.bat) — nécessite
Python et `pywebview` installés (`pip install pywebview`). Ou en ligne
de commande :

```bash
python app/web_main.py
```

Pour les exports Wi-Fi et pilotes (qui exigent les droits administrateur),
utilisez **Relancer en administrateur** (pages Wi-Fi & pilotes et Paramètres).

## Fonctionnalités

- **Fichiers personnels** : Bureau, Documents, Images, Téléchargements,
  Vidéos, Musique (détection correcte même si OneDrive redirige ces
  dossiers), + sauvegardes de jeux (`Saved Games`) et dossiers personnalisés
  (VPN, certificats, logiciels métier, presets, photos de téléphone...).
  **Exclusions** par nom ou motif (`node_modules`, `*.iso`…) et taille
  maximale par fichier. Les fichiers OneDrive disponibles uniquement en
  ligne sont ignorés par défaut (option pour les télécharger).
- **Navigateurs** : Chrome, Edge, Firefox, Brave, Vivaldi, Opera et Opera GX
  (favoris, mots de passe chiffrés, historique, sans les caches) en filet de
  sécurité — la synchronisation de compte reste la méthode principale.
- **Licences** : récupère la clé Windows OEM (PowerShell) et permet de
  saisir manuellement d'autres clés logicielles, enregistrées dans le
  dossier de sauvegarde. Le journal et le rapport n'affichent qu'une clé masquée.
- **Programmes** : exporte la liste des logiciels installés via `winget`
  (réinstallation automatique possible) et une liste complète pour repérer
  les logiciels non pris en charge par winget.
- **E-mail** : profils Thunderbird, fichiers Outlook `.pst` (dossier Outlook
  et `Documents\Fichiers Outlook`) et signatures Outlook. Les `.ost` (copies
  locales de boîtes en ligne) ne sont pas copiés.
- **Wi-Fi & pilotes** : exporte les profils Wi-Fi enregistrés (avec mots de
  passe) et les pilotes installés (nécessite les droits administrateur).
- **Checklist finale** : coche les étapes à ne pas oublier, état sauvegardé
  automatiquement, avec génération d'un rapport texte dans le dossier de
  sauvegarde.
- **Tout sauvegarder en un clic** : enchaîne toutes les étapes
  automatisables vers le dossier choisi. L'écran d'accueil permet de choisir
  les catégories et d'**estimer la taille** (total, part à copier, espace libre)
  avant de lancer. L'espace libre est contrôlé avant toute copie.
- **Progression globale et annulation** : une seule barre pour toute la
  sauvegarde, avec le temps restant estimé et un bouton **Annuler**.

### Versions datées et incrémentales

Chaque sauvegarde crée un dossier `SauvegardePC_<PC>_<date>` dans la
destination, avec `sauvegarde.json` (résumé), `fichiers.json` (taille, date et
SHA-256 de chaque fichier), `rapport_sauvegarde.txt` et `journal.txt`. Sur un
disque NTFS, les fichiers inchangés depuis la version précédente sont ajoutés
par **lien physique** : chaque version est complète et lisible seule, mais seules
les nouveautés prennent de la place. Sur exFAT/FAT32, chaque version est une
copie complète. Le nombre de versions conservées se règle dans **Destination**
(3 par défaut) ; une sauvegarde annulée ou en échec ne sert jamais de référence.

Ne modifiez pas les fichiers à l'intérieur d'une version : grâce aux liens
physiques, un même fichier peut appartenir à plusieurs versions.

Chaque fichier copié est relu et comparé à son empreinte (désactivable dans
**Paramètres**). **Vérifier** (page Destination) recontrôle une version
entière plus tard, par exemple avant de réinstaller Windows.

Une alerte s'affiche lorsque la destination est sur le même disque physique
que Windows.

### Restauration complète

Dans **Restauration**, choisissez le disque de sauvegarde (la version la plus
récente est proposée, les autres restent sélectionnables), une version précise
ou une ancienne sauvegarde 1.10. Cochez les éléments à restaurer :

- **Fichiers personnels** : remis dans les dossiers Windows de l'utilisateur
  courant ; les dossiers personnalisés retrouvent leur chemin d'origine (ou
  `Dossiers restaurés` si le disque d'origine n'existe plus). Un fichier
  identique est ignoré, un fichier différent déjà présent est conservé sous le
  nom `… (avant restauration <date>)`.
- **Navigateurs et messagerie** : le profil existant est mis de côté en entier,
  puis le profil sauvegardé est copié. Firefox et Thunderbird sont déclarés dans
  `profiles.ini` / `installs.ini` pour s'ouvrir directement sur le profil
  restauré. La restauration refuse de démarrer si le programme est ouvert.
  Les `.pst` sont placés dans `Documents\Fichiers Outlook` (à ouvrir dans Outlook).
- **Programmes** : `winget import` (Internet requis, peut être long).
- **Wi-Fi** : `netsh wlan add profile`. **Pilotes** : `pnputil /add-driver`
  (droits administrateur).

### Sauvegarde automatique

Dans **Paramètres → Sauvegarde automatique**, choisissez chaque jour ou chaque
semaine. Une tâche du Planificateur de tâches Windows (`Sauvegarde PC -
sauvegarde automatique`) lance `SauvegardePC.exe --auto-backup` toutes les 30
minutes, sans fenêtre : la sauvegarde ne démarre que si elle est due et si le
disque de destination est branché, donc peu après son branchement. Le résultat
est affiché dans Paramètres (`%APPDATA%\SauvegardePC\sauvegarde_auto.json`).
L'interface et la tâche ne peuvent jamais travailler en même temps.

## Structure du projet

### Sauvegarde intelligente par logiciel

Dans **Logiciels**, chargez les applications installées, recherchez un nom puis
cliquez sur **Analyser**. L'aperçu affiche les fichiers, paramètres et la méthode
d'installation sur un PC neuf. Si nécessaire, utilisez **Choisir un installeur
MSI / EXE** pour fournir l'installeur officiel de la même version. Fermez le
logiciel et choisissez **Sauvegarder le logiciel et ses paramètres**.
Chaque exécution crée un dossier `Nom du logiciel-date-identifiant` dans `Logiciels`, avec les fichiers,
leurs empreintes SHA-256, les métadonnées de réinstallation et un `manifest.json`.
Les fichiers inaccessibles produisent une sauvegarde explicitement partielle.

Sur le nouveau Windows, installez Sauvegarde PC et ouvrez la session de
l'utilisateur destinataire. Dans **Restauration**, choisissez le dossier principal
de sauvegarde, son sous-dossier `Logiciels` ou une sauvegarde individuelle.
Cliquez sur **Installer et restaurer** :

- MSI inclus : installation silencieuse depuis le support, puis paramètres.
- Winget identifié : téléchargement et installation de la version sauvegardée,
  puis paramètres. Internet et Winget (App Installer de Microsoft) sont requis.
- EXE inclus : lancement de son assistant ; terminez-le sans ouvrir le logiciel.
  L'installation doit être confirmée par Windows avant d'appliquer les paramètres.
  Un EXE peut être un téléchargeur et nécessiter Internet ; aucun argument
  silencieux n'est deviné pour les installeurs inconnus.
- Installeur manquant : utilisez **Ajouter / remplacer l'installeur** pour
  compléter une sauvegarde existante. Sur un PC neuf, la restauration s'arrête
  tant que l'installation n'est pas possible. Si le logiciel est déjà installé,
  elle restaure directement les paramètres.

Une installation annulée, échouée ou non confirmée interrompt la restauration
avant d'appliquer les paramètres. Si l'installeur exige un redémarrage, redémarrez
puis relancez la restauration. Des droits administrateur peuvent être nécessaires.
Fermez le logiciel s'il est déjà installé. Les anciennes sauvegardes de format 1
restent compatibles mais nécessitent une installation préalable du logiciel.

Les chemins AppData sont adaptés à l'utilisateur Windows courant. Les fichiers
existants remplacés et les valeurs de registre précédentes sont conservés dans
un sous-dossier `Avant-restauration-*` de la source (qui doit être accessible en
écriture). Cette copie permet une récupération manuelle ; la restauration n'est
pas transactionnelle et une erreur pendant l'écriture peut laisser un état partiel.
Les fichiers supplémentaires déjà présents ne sont pas supprimés.

Profils d'emplacements connus : VS Code (dont `.vscode`), Firefox, Chrome, Edge,
VLC, Notepad++, OBS Studio, Thunderbird et registre utilisateur de 7-Zip.
Pour les autres programmes, l'analyse utilise les traces d'installation Windows,
les noms de dossiers et les clés de registre attribuables au produit. Elle
ne trace pas l'activité des logiciels et ne découvre pas tous les dossiers externes.
Les applications Store et certains logiciels portables peuvent manquer à l'inventaire.
Les liens symboliques et jonctions sont ignorés et signalés comme copie partielle.

Les services et composants sont recréés par l'installeur ; les clés système ne
sont pas importées aveuglément. Les données des emplacements externes sont
conservées dans la sauvegarde mais nécessitent une restauration manuelle.
L'export Winget reste disponible séparément. Les licences, connexions et secrets
chiffrés par Windows peuvent nécessiter une nouvelle activation ou saisie. Les
chemins absolus contenus *dans* les préférences ne sont pas réécrits. Une copie
terminée signifie que les éléments détectés ont été copiés, pas que l'application
est intégralement portable. Ces sauvegardes individuelles ne sont pas incluses
automatiquement dans « Tout sauvegarder ».

Tests de cette fonction : `python -m unittest discover -s tests -v`.

Compilation Windows (PyInstaller et Inno Setup 6 installés), depuis la racine :

```powershell
python -m pip install -r requirements-build.txt
python scripts/build_release.py
```

L'exécutable portable est `dist_release/SauvegardePC.exe` ; l'installeur généré
est `SauvegardePC-Setup.exe`. Fermez les exécutables de sortie avant de les reconstruire.

```
Application-sauvegardePC/
├── Lancer.bat            → lanceur pratique
├── README.md
└── app/
    ├── web_main.py        → passerelle WebView2 / Python
    ├── web/               → interface Aurora HTML, CSS et JavaScript
    ├── backup_engine.py   → versions, copie incrémentale, vérification,
    │                         estimation et restauration complète
    ├── backup_tasks.py    → briques Windows (dossiers connus, navigateurs,
    │                         messagerie, licences, winget, Wi-Fi, pilotes, disques)
    ├── scheduler.py       → sauvegarde automatique (Planificateur de tâches)
    ├── software_*.py      → sauvegarde et réinstallation par logiciel
    └── app_updates.py     → mises à jour depuis GitHub Releases
```

La configuration (dossiers personnalisés, exclusions, versions conservées,
checklist, dernier dossier de destination…) est stockée dans
`%APPDATA%\SauvegardePC\config.json` (écriture atomique ; un fichier illisible
est conservé sous `config.corrompu.json`).

## Mises à jour et publication

À partir de la version 1.10.1, l'application recherche les nouvelles versions
publiques sur GitHub au lancement et toutes les six heures. Une mise à jour est
proposée sans interrompre une sauvegarde ou restauration. Le téléchargement est
vérifié avant l'ouverture de l'assistant d'installation.

Installez cette version une première fois sur chaque PC. Les versions antérieures
ne possèdent pas ce mécanisme. La recherche peut être déclenchée ou désactivée
dans **Paramètres → Mises à jour de l'application**.

Le code et les publications sont sur [GitHub](https://github.com/ARBJ-prevention/SauvegardePC).
Pour publier une nouvelle version, suivez [RELEASING.md](RELEASING.md) : un tag
`vX.Y.Z` déclenche les tests, la compilation et la publication de l'installeur.
Le script `check_update.py` reste un lanceur de développement local ; il ne
sert pas au mécanisme de mise à jour distribué aux utilisateurs.

## Notes importantes

- Fermez les navigateurs et Outlook/Thunderbird avant de lancer leur
  sauvegarde respective (fichiers verrouillés sinon ignorés, pas bloquants).
- Les mots de passe de navigateurs restent chiffrés par Windows (DPAPI) :
  la restauration fiable passe par la synchronisation de compte, la copie
  du profil n'est qu'un filet de sécurité.
- Utilisez un support **externe** au PC (disque USB, clé USB, cloud) comme
  destination — jamais une seconde partition du même disque physique.
