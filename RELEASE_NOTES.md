# Sauvegarde PC 1.15.0

## Migration vers une session de domaine

Nouvel outil (Outils → Migration vers un domaine) pour restaurer une sauvegarde d'un
ancien PC — en groupe de travail, données en local — dans la session de l'utilisateur
actuellement connecté, sur un nouveau PC joint à un domaine Active Directory dont le
dossier personnel vit sur un lecteur réseau (NAS, quota par propriétaire).

- **Détection de l'environnement** : domaine, utilisateur, dossier personnel réseau et
  redirection des dossiers connus affichés avant de commencer. Avertissement clair et
  migration bloquée si l'application est lancée en administrateur, pour que les fichiers
  copiés restent bien la propriété de l'utilisateur (et dans son quota).
- **Assistant de tri avant la copie** : doublons détectés par empreinte SHA-256 (le
  chemin le plus propre est conservé), installeurs, images disque, gros fichiers,
  vidéos, raccourcis et fichiers système décochés par défaut. Chaque groupe se coche ou
  se décoche en un clic, avec un détail fichier par fichier. Export du tri en CSV.
- **Estimation de l'espace** avant de lancer la copie, comparée à l'espace disponible de
  chaque destination choisie.
- **Destination par catégorie** (Bureau, Documents, Images, Téléchargements, Vidéos,
  Musique) : dossier personnel réseau dans un sous-dossier « Migration – <ancien PC> –
  <date> » (par défaut, pour ne rien mélanger avec l'existant), dossier connu de la
  session, ou dossier choisi manuellement.
- **Navigateurs** : seuls les favoris sont restaurés par défaut (jamais les mots de
  passe, qui restent liés à la session Windows d'origine) — copie directe pour
  Chrome/Edge, export HTML standard pour Firefox (sans jamais toucher à un profil
  Firefox actif).
- **Messagerie** : copie optionnelle des fichiers Outlook (.pst).
- **Programmes** : export d'un inventaire propre (CSV), filtré des mises à jour KB, des
  redistribuables Visual C++ et des composants système — pas de réinstallation
  automatique.
- **Mode simulation** (par défaut) : aucune copie, un rapport de ce qui serait fait.
  Copie réelle avec reprise après interruption et vérification finale par empreinte
  SHA-256 ; la sauvegarde source n'est jamais modifiée.

Vos réglages et vos sauvegardes sont conservés. Windows x64 et Microsoft WebView2 sont requis.
