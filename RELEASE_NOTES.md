# Sauvegarde PC 1.11.0

## Nouveautés

- Sauvegardes datées et incrémentales : un dossier par sauvegarde, seuls les fichiers
  modifiés sont copiés (disques NTFS), nombre de versions conservées réglable.
- Restauration complète : fichiers personnels, navigateurs, messagerie, programmes
  (winget), profils Wi-Fi et pilotes, depuis une version au choix ou une ancienne sauvegarde.
- Vérification de chaque copie (SHA-256) et revérification d'une version à tout moment.
- Estimation de la taille avant sauvegarde et contrôle de l'espace libre.
- Exclusions personnalisables (noms, motifs, taille maximale) et option pour les fichiers
  OneDrive disponibles uniquement en ligne.
- Sauvegarde automatique quotidienne ou hebdomadaire, dès que le disque est branché.
- Progression globale avec temps restant et bouton Annuler.
- Brave, Vivaldi, Opera et Opera GX ; fichiers .pst de Documents\Fichiers Outlook
  et signatures Outlook.
- Alerte lorsque la destination est sur le même disque que Windows.
- Bouton « Relancer en administrateur ».

## Corrections

- Les profils Thunderbird sont de nouveau sauvegardés.
- Le rapport contient l'intégralité de la sauvegarde en cours.
- La clé Windows n'apparaît plus en clair dans le journal ni dans le rapport.
- winget, dism et netsh ne peuvent plus bloquer l'application indéfiniment.
- Une destination placée dans Documents n'empêche plus la sauvegarde de Documents.
- La configuration ne peut plus être perdue en cas d'arrêt brutal.
- Firefox et Thunderbird s'ouvrent sur le profil restauré.
- L'interface ne perd plus le focus à chaque rafraîchissement.

Les sauvegardes faites avec la version 1.10 restent restaurables. Windows x64 et
Microsoft WebView2 sont requis.
