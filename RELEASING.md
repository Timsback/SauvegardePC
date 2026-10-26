# Publier une version

Le dépôt public de référence est **ARBJ-prevention/SauvegardePC**. Les PC ne
contiennent aucun jeton GitHub. Ils consultent uniquement les versions publiques
stables et téléchargent l'installeur associé après confirmation de l'utilisateur.

## Première installation sur les PC

Installez une fois la version **1.10.1 ou ultérieure** depuis
[la dernière version](https://github.com/ARBJ-prevention/SauvegardePC/releases/latest).
Les anciennes versions sans mécanisme de mise à jour ne peuvent pas se mettre à
jour seules. Les réglages restent dans `%APPDATA%\SauvegardePC` et les sauvegardes
restent à l'emplacement choisi par l'utilisateur.

## Publier les modifications suivantes

1. Modifiez le code puis augmentez `version` dans `app/release.json` (par exemple
   `1.10.1`). Conservez le nom du dépôt de référence.
2. Décrivez les nouveautés dans `RELEASE_NOTES.md` et lancez les tests :
   `python -m unittest discover -s tests -v`.
3. Enregistrez et envoyez les modifications, puis le tag correspondant :

```powershell
git add app tests scripts .github README.md RELEASE_NOTES.md RELEASING.md installer.iss requirements-build.txt
git commit -m "Prepare release 1.10.1"
git push origin main
git tag v1.10.1
git push origin v1.10.1
```

GitHub Actions vérifie le code, compile sous Windows, génère l'installeur et son
empreinte SHA-256, puis crée une release. La release reste en brouillon tant que
ses deux fichiers n'ont pas été envoyés. Les tests et la compilation doivent
réussir pour qu'elle soit publiée. Un lancement manuel du workflow compile une
version de test sans la publier (sauf s'il est exécuté sur un tag de version).

Ne modifiez pas un tag ou les fichiers d'une version déjà publiée. En cas de
correction, publiez un nouveau numéro. Si une publication échoue après la
création du brouillon, vérifiez le journal Actions et supprimez uniquement ce
brouillon incomplet avant de relancer le workflow ; ne supprimez pas une release
publique utilisée par des PC.

## Fonctionnement sur les PC

- Recherche au lancement et toutes les six heures si l'application reste ouverte.
- Proposition lorsque aucune sauvegarde/restauration n'est en cours.
- « Mettre à jour » télécharge l'installeur, vérifie taille et SHA-256, puis ouvre
  l'assistant et ferme l'application. L'installation reste confirmée par l'utilisateur.
- Une indisponibilité réseau n'empêche pas d'utiliser l'application.
- Recherche manuelle et désactivation de la recherche automatique dans Paramètres.
- Les versions antérieures, brouillons, préversions et téléchargements altérés
  ne sont pas installés.

Les téléchargements proviennent du dépôt configuré, en HTTPS. Le SHA-256 protège
contre une corruption du téléchargement ; il ne remplace pas la sécurité du
compte GitHub. Activez l'authentification à deux facteurs pour les comptes qui
peuvent publier.

## Signature de code (optionnelle)

Sans certificat, les exécutables ne sont pas signés et SmartScreen peut demander
une confirmation lors de l'installation. Pour signer l'exécutable et l'installeur :

1. Obtenez un certificat de signature de code (.pfx) auprès d'une autorité reconnue.
2. Dans GitHub → Settings → Secrets and variables → Actions, ajoutez
   `SIGN_PFX_BASE64` (le .pfx encodé en base64 :
   `[Convert]::ToBase64String([IO.File]::ReadAllBytes('certificat.pfx'))`) et
   `SIGN_PFX_PASSWORD`.

Le workflow signe alors automatiquement les deux fichiers avec `signtool`
(horodatage DigiCert). En local, définissez `SIGN_PFX` (chemin du .pfx) et
`SIGN_PFX_PASSWORD` avant `python scripts/build_release.py`.

## Compiler localement

Avec Python et Inno Setup 6 installés :

```powershell
python -m pip install -r requirements-build.txt
python scripts/build_release.py
```

Les résultats sont `dist_release/SauvegardePC.exe`, `SauvegardePC-Setup.exe` et
`SauvegardePC-Setup.exe.sha256`. Les binaires, journaux, configurations personnelles
et sauvegardes sont exclus du dépôt Git. Les installeurs sont distribués via Releases.
