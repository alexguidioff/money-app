# Money

[Italiano](README.md) · [English](README.en.md) · [Deutsch](README.de.md) · [Español](README.es.md) · **Français**

Une application de finances personnelles qui tourne **chez toi** : tes
mouvements, tes comptes et tes investissements restent dans ta propre base de
données, sur une machine que tu contrôles. Aucun service externe ne voit tes
chiffres.

Elle est née pour remplacer un tableur qui avait grandi pendant des années, et
elle en garde l’obsession : **les chiffres doivent tomber juste**. Chaque total
est calculé à partir des mouvements, jamais recopié d’ailleurs, et les soldes
calculés peuvent être comparés aux soldes déclarés pour voir tout de suite
quand quelque chose ne colle pas.

## Ce qu’elle fait

- **Mouvements** — revenus, dépenses, virements entre comptes, récurrences.
  Import des relevés bancaires en PDF ou CSV, avec un aperçu avant
  l’enregistrement : le PDF est lu même quand la banque ne dessine pas de
  tableau et se contente d’aligner le texte en colonnes.
- **Comptes** — façon bilan, répartis entre banques, actifs, passifs et
  investissements, avec le solde calculé à côté du solde attendu.
- **Budget** — planification par catégorie et par mois, comparaison avec les
  dépenses, évolution annuelle.
- **Objectifs** — combien il manque, à quel rythme, pour quand.
- **Dettes** — prêts et lignes de crédit, avec le plan d’amortissement théorique
  à côté de ce qui a réellement été débloqué, prélevé et remboursé.
- **Patrimoine** — la série mensuelle du patrimoine net, lisible aussi dans
  d’autres devises au taux du mois concerné, pas à celui d’aujourd’hui.
- **Investissements** — portefeuille valorisé aux prix du marché téléchargés via
  les tickers, répartition par secteur et par titre sous-jacent, historique de
  chaque instrument avec tes propres achats et ventes dessus.
- **Retraite et FIRE** — combien de capital il faut, en quelle année tu y
  arrives et ce qui le fait bouger : taux d’épargne, rentes et pensions futures,
  dépenses qui changent à la retraite, avec des notes fiscales pour dix pays
  européens.
- **Notes** — des notes libres pour te rappeler pourquoi tu as décidé quelque
  chose.
- **Ensemble** — qui le souhaite partage ses *totaux* avec les autres comptes de
  la même installation. Jamais les mouvements, jamais les catégories, jamais les
  comptes.
- **Alertes** — budgets dépassés, cours figés, sauvegardes qui ne tournent pas.
  Une fois fermées, elles ne reviennent pas.

En haut de chaque page, un **« ? »** explique à quoi sert la page et ce dont
elle a besoin pour se remplir de chiffres.

L’interface existe en italien, anglais, allemand, espagnol et français.

## Démarrer

Il te faut seulement Docker.

```bash
git clone https://github.com/alexguidioff/money-app.git money
cd money
./setup.sh                    # crée le .env avec deux mots de passe aléatoires
docker compose up -d --build
```

Ouvre ensuite <http://localhost:3010> et **crée le premier compte** sur l’écran
qui s’affiche. L’application démarre vide : il n’y a les données de personne
d’autre.

Pour ajouter d’autres personnes, passe par le même écran de connexion : chacune
aura ses propres mouvements, séparés. Le mot de passe est facultatif — tant que
personne n’en définit un, l’application s’ouvre sans rien demander.

## Les deux mots de passe de la base de données

Ce ne sont pas ceux avec lesquels tu te connectes à l’application : ce sont les
clés avec lesquelles l’application parle à la base de données, et **personne ne
doit jamais les taper**. `setup.sh` en génère deux au hasard et les écrit dans le
fichier `.env`, que Docker lit au démarrage.

Une personne n’en a besoin que pour ouvrir la base à la main ou pour restaurer
une sauvegarde en dehors de l’application : dans les deux cas, on les lit dans
`.env`.

Si le `.env` est perdu alors que les données sont toujours là, rien n’est
perdu : à l’intérieur du conteneur, la base accepte la connexion locale sans mot
de passe.

```bash
./setup.sh                                  # un nouveau .env, avec de nouveaux mots de passe
docker compose exec db psql -U money -d money \
  -c "alter role money password '<la POSTGRES_PASSWORD du nouveau .env>'"
docker compose up -d                        # l’application réécrit l’autre toute seule
```

## Supprimer une personne

Depuis **Paramètres → Compte**, et **seulement son propre compte** : il n’existe
pas d’administrateur capable d’effacer les chiffres de quelqu’un d’autre. Il faut
taper son propre nom pour confirmer, et avant la suppression l’application
exporte les données au format d’échange et sauvegarde la base — la copie reste
aussi à côté des sauvegardes, sur le serveur.

## Comment les données sont séparées entre les personnes

Pas par la discipline des requêtes, mais **par la base de données**. Chaque
table de données personnelles a une politique de ligne PostgreSQL liée à
l’utilisateur de la requête, et l’application se connecte avec un rôle sans
privilèges : une requête qui oublie le filtre ne voit pas les données des
autres, elle ne trouve tout simplement rien.

C’est un choix délibéré. Le risque d’une application comme celle-ci n’est pas de
tomber en panne de façon visible : c’est de mélanger en silence les chiffres de
deux personnes.

## Où sont les données

Dans un volume Docker avec PostgreSQL. Elles n’en sortent pas :

- **Sauvegardes automatiques** chaque jour, plus une avant chaque import qui
  remplace les données. On les restaure depuis l’interface, dans Rapport.
- **Export complet** au « format d’échange » : une feuille par entité, dates
  ISO, aucune formule. Il sert à tout emporter et à le recharger dans une autre
  installation — un test vérifie que l’aller-retour export-import rend
  exactement ce qu’il a pris.

Les seules choses qui partent vers internet sont les **cours** des instruments
que tu as configurés avec un ticker, et les **taux de change** des devises que
tu choisis.

## L’installer sur un NAS

L’application est pensée pour finir sur une machine toujours allumée,
accessible depuis les appareils de la maison.

1. `MONEY_BIND_ADDRESS=0.0.0.0` dans le `.env`
2. Place un réseau privé devant — [Tailscale](https://tailscale.com) est la voie
   la plus simple : trafic chiffré, aucun port ouvert sur la box, et avec
   `tailscale serve` un vrai certificat HTTPS
3. `MONEY_APP_ORIGIN=https://...` avec l’adresse depuis laquelle tu ouvriras
   l’application. Elle sert deux fois : le cookie de session comprend tout seul
   qu’il est derrière HTTPS, et l’API n’accepte les requêtes que de `localhost`,
   `127.0.0.1` et de cette adresse — un autre site ouvert dans le même
   navigateur ne peut ni lire ni modifier tes données
4. Construis les images **sur le NAS** (`docker compose up -d --build`), ne les
   copie pas : son architecture peut être différente de celle de ton ordinateur

Sans réseau privé devant, ne l’expose pas : mots de passe et mouvements
circuleraient en clair sur le réseau local. Mets un mot de passe sur chaque
compte : après dix tentatives erronées en un quart d’heure, la connexion à ce
nom est bloquée un moment.

## Comment elle est construite

- **API** — FastAPI et SQLAlchemy sur PostgreSQL 17
- **Interface** — React (avec vinext sur Vite), en cinq langues
- **Passerelle** — nginx devant les deux, pour que le navigateur parle à une seule origine

```bash
pnpm install
docker compose exec api python -m pytest tests/ -q   # tests du backend
pnpm test                                            # tests unitaires et contrats du frontend
pnpm test:e2e                                        # end-to-end avec Playwright sur une pile jetable
scripts/gate.sh                                      # tout à la suite, puis publie seulement si tout est vert
```

Les tests couvrent le moteur de calcul, la lecture des relevés, l’aller-retour
export-import et les totaux qui se sont cassés en silence par le passé : un
modèle récurrent compté comme une vraie dépense, le capital investi calculé en
oubliant les positions vendues. Les **contrats** comparent les vraies réponses
de l’API aux types que lit l’interface, et les requêtes envoyées par l’interface
à ce que l’API accepte : un champ renommé d’un seul côté bloque le gate.

Les tests end-to-end (`pnpm test:e2e`, après `pnpm exec playwright install
chromium`) tournent dans un projet Compose séparé, port 3011, base de données en
mémoire : ils ne touchent jamais l’installation que tu utilises. Ils ont besoin
des images `money-app-api` et `money-app-web`, que `docker compose build` crée.

## Licence

MIT.
