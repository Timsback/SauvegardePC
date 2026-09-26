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
lancez l'application avec un clic droit → « Exécuter en tant qu'administrateur ».

## Fonctionnalités

- **Fichiers personnels** : Bureau, Documents, Images, Téléchargements,
  Vidéos, Musique (détection correcte même si OneDrive redirige ces
  dossiers), + sauvegardes de jeux (`Saved Games`) et dossiers personnalisés
  (VPN, certificats, logiciels métier, presets, photos de téléphone...).
- **Navigateurs** : détecte Chrome / Edge / Firefox et copie leur profil
  (favoris, mots de passe chiffrés, historique) en filet de sécurité — la
  synchronisation de compte reste la méthode principale recommandée.
- **Licences** : récupère la clé Windows OEM (PowerShell) et permet de
  saisir manuellement d'autres clés logicielles, enregistrées dans le
  dossier de sauvegarde.
- **Programmes** : exporte la liste des logiciels installés via `winget`
  (réinstallation automatique possible) et une liste complète pour repérer
  les logiciels non pris en charge par winget.
- **E-mail** : copie les fichiers Outlook (`.pst`/`.ost`) et les profils
  Thunderbird trouvés localement.
- **Wi-Fi & pilotes** : exporte les profils Wi-Fi enregistrés (avec mots de
  passe) et les pilotes installés (nécessite les droits administrateur).
- **Checklist finale** : coche les étapes à ne pas oublier, état sauvegardé
  automatiquement, avec génération d'un rapport texte dans le dossier de
  sauvegarde.
- **Tout sauvegarder en un clic** : enchaîne toutes les étapes
  automatisables vers le dossier choisi. L'écran d'accueil permet de choisir
  les catégories à inclure dans cette sauvegarde.

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
    ├── main.py            → ancienne interface Tkinter (conservée)
    └── backup_tasks.py    → logique de sauvegarde (fichiers, navigateurs,
                              licences, programmes, e-mail, Wi-Fi, pilotes)
```

La configuration (dossiers personnalisés, état de la checklist, dernier
dossier de destination) est stockée dans
`%APPDATA%\SauvegardePC\config.json`.

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
