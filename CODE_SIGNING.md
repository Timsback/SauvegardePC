# Politique de signature de code

Free code signing provided by [SignPath.io](https://about.signpath.io/),
certificate by [SignPath Foundation](https://signpath.org/).

## Ce qui est signé

Seuls `SauvegardePC.exe` et l'installeur `SauvegardePC-Setup.exe` publiés dans les
[Releases GitHub](https://github.com/Timsback/SauvegardePC/releases) sont
signés. Ils sont compilés par GitHub Actions à partir du code de ce dépôt, sur un
tag `vX.Y.Z` (voir [RELEASING.md](RELEASING.md)). Aucun fichier compilé sur un PC
personnel n'est signé.

## Rôles

- **Contributeurs et relecteurs** : [Timsback](https://github.com/Timsback),
  propriétaire du dépôt. Toute modification proposée par une autre personne est
  relue par le propriétaire avant d'être intégrée.
- **Approbateurs** : [Timsback](https://github.com/Timsback). Chaque demande de
  signature d'une version publique est approuvée manuellement.

Ce compte utilise l'authentification à deux facteurs.

## Confidentialité

Sauvegarde PC ne transmet aucune donnée personnelle. Les fichiers sauvegardés,
la configuration, les licences et les journaux restent sur le PC et sur la
destination choisie par l'utilisateur.

La seule connexion réseau automatique est la recherche de mises à jour : au
lancement puis toutes les six heures, l'application interroge l'API publique de
GitHub (`api.github.com`) pour connaître la dernière version de ce dépôt. Elle ne
télécharge l'installeur que si l'utilisateur clique sur « Mettre à jour ». Cette
recherche peut être désactivée dans **Paramètres → Mises à jour de l'application**.

La sauvegarde et la restauration de la liste des programmes utilisent winget,
qui peut contacter les sources de Microsoft pendant ces opérations, lancées par
l'utilisateur ou par la sauvegarde automatique qu'il a activée.
