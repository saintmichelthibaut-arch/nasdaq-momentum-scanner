# Nasdaq Momentum Scanner

Repère les actions tech US qui **commencent** à accélérer (volume qui monte en rampe, force relative qui démarre, volatilité qui se comprime sous une résistance), avant que le mouvement soit évident.

- Aucun serveur. GitHub Actions fait la collecte chaque soir, GitHub Pages affiche la page.
- Aucune donnée inventée. Si une valeur manque, la page affiche `N/A` avec la raison.
- Chaque écran indique la date de la dernière collecte réussie, et affiche un bandeau si elle date de plus de 24 h.

Page : **https://saintmichelthibaut-arch.github.io/nasdaq-momentum-scanner/** (active une fois les étapes ci-dessous faites)

---

## Mise en route (une seule fois, environ 5 minutes)

1. **Rendre le dépôt public.** Sur GitHub : `Settings` → `General` → tout en bas, section *Danger Zone* → `Change visibility` → `Public`.
2. **Activer la page web.** `Settings` → `Pages` → *Build and deployment* → *Source* : choisir **GitHub Actions**.
3. **Lancer la première collecte.** Onglet `Actions` → *Collecte quotidienne* (colonne de gauche) → bouton `Run workflow` → `Run workflow`.
   - Si GitHub demande d'autoriser les workflows, clique sur le bouton vert pour les activer.
   - La première collecte télécharge **5 ans de cours d'un coup** : RSI, SMA200, RVOL et plus hauts 52 semaines fonctionnent dès le premier jour. Elle reconstruit aussi les **60 derniers jours de score**, donc la section Momentum Acceleration est remplie immédiatement.
   - Durée : 2 à 4 minutes. Le backtest tourne dans la foulée.
4. Ouvre la page. Ensuite, tout est automatique : **du lundi au vendredi à 22h30 UTC** (00h30 à Paris en été, 23h30 en hiver), après la clôture de Wall Street.

Si une collecte échoue (Yahoo indisponible, par exemple), GitHub t'envoie un e-mail, et la page affiche un bandeau rouge avec la date des dernières données valides.

---

## Utiliser la page

| Section | Ce qu'elle montre |
|---|---|
| **Alertes** | Titres qui franchissent un score de 75, RVOL > 2, breakout avec volume, ou forte accélération du volume. Également dans `data/alerts.json`. |
| **Top Momentum** | Les 20 meilleurs scores, avec les sous-scores P (prix), V (volume) et B (breakout). |
| **Volume Surge** | Les 20 volumes les plus anormaux par rapport à l'historique propre du titre (z-score). |
| **Before Breakout** | Titres pas encore cassés, proches d'une résistance, qui réunissent au moins 5 des 8 conditions (consolidation, compression, volume qui monte, SMA20/50 en hausse, RSI 50-70, séances positives, RS en amélioration). |
| **Momentum Acceleration** | Trié par **progression du score** sur 5 séances, pas par score absolu. |
| **New Names to Watch** | Entrées récentes dans le haut du classement. |
| **Next MU / SNDK** | Profil A (amélioration progressive) et profil B (accélération confirmée), avec une phrase chiffrée qui explique ce qui correspond. |
| **Heatmap secteurs** | Performance jour / 5 j / 1 mois, RVOL moyen, score moyen, progression du score. |

- **Swing / Standard / Long terme** (en haut à droite) : change la pondération du score. *Swing* pour toi, *Long terme* pour ton père.
- **Clic sur une ligne** : fiche complète avec graphique 1 an, RSI, MACD, supports/résistances, breakouts, anomalies, *What changed?*, et le détail du score.
- **Tous les titres** : tableau triable (clic sur un en-tête), recherche, filtres, watchlist (★, mémorisée dans ton navigateur).
- **Backtest** : performance des 10 meilleurs scores chaque semaine, comparée au QQQ.

---

## Modifier le scanner

- **Pondérations du score** : `config/weights.json`. Chaque profil doit totaliser 100. Enregistre le fichier sur GitHub : une collecte se relance automatiquement, et le backtest applique les nouveaux poids à tout l'historique.
- **Ajouter un titre** : une ligne dans `scripts/universe.py`, par exemple `"NFLX": ("Netflix", "Other", "core"),`. Rien d'autre à changer. Plusieurs centaines de titres passent sans problème.
- **Seuils d'alerte** : section `alerts` de `config/weights.json`.

---

## Combien d'historique faut-il ?

| Fonction | Disponible |
|---|---|
| RSI, SMA 20/50/200, RVOL, ATR, RS vs QQQ, plus hauts 52 s. | **Dès la première collecte** (5 ans téléchargés) |
| Momentum Acceleration (Δ score 1/3/5/10 j) | **Dès la première collecte** : les scores passés sont recalculés depuis les cours, avec uniquement les données connues à chaque date |
| Backtest | **Dès la première collecte** : environ 3,5 ans testables (5 ans moins 1 an de chauffe pour la SMA200 et les 52 semaines) |
| Historique « en direct » dans `data/history/` | S'accumule d'un fichier par jour. Les 60 premiers jours sont reconstruits et marqués `"backfilled": true` |

Exception : **SNDK** n'est coté que depuis février 2025. Certains indicateurs longs (performance 1 an, RS 1 an) restent en N/A tant qu'il n'a pas assez de séances, et la page l'indique.

---

## Ce que yfinance fournit, et ce qu'il ne fournit pas

**Disponible et utilisé :** cours quotidiens (ouverture, haut, bas, clôture, volume) ajustés des splits et dividendes, sur plusieurs années, pour les actions US, les actions de Paris (`.PA`), le QQQ et le CAC 40 (`^FCHI`).

**Non utilisé :**
- Intraday fiable sur longue durée : l'outil travaille en fin de journée uniquement.
- Carnet d'ordres, flux d'options, positions vendeuses à jour : absents ou peu fiables dans yfinance.
- Fondamentaux et estimations d'analystes : partiels et irréguliers. Ils sont exclus pour ne jamais afficher une donnée douteuse.
- Yahoo n'a pas d'API officielle. Un changement de leur côté peut casser la collecte un soir : elle s'arrête alors proprement, et la page le signale.

---

## Ce qui n'est pas implémenté, et pourquoi

- **Envoi d'alertes par e-mail** : `data/alerts.json` est prêt, mais il faudrait un compte d'envoi (SMTP) et un secret GitHub. Je ne l'ai pas activé sans ton accord.
- **Données en séance** : yfinance n'est pas fiable pour ça, et un robot qui tourne une fois par soir n'en a pas besoin.
- **Correction du biais du survivant dans le backtest** : il faudrait l'historique des compositions passées du Nasdaq, que yfinance ne fournit pas. Le biais est signalé en toutes lettres dans les résultats.
- **Change EUR/USD** : les titres de Paris sont analysés en euros, sans conversion.

---

## Fichiers

```
index.html                    la page (HTML + CSS + JS, aucune dépendance, graphiques SVG faits main)
config/weights.json           pondérations, profils, seuils d'alerte
scripts/universe.py           liste des titres + secteurs
scripts/collect.py            collecte yfinance → indicateurs → score → JSON
scripts/indicators.py         indicateurs (sans look-ahead : la valeur à T n'utilise que les séances ≤ T)
scripts/scoring.py            sous-scores, pénalité de surchauffe, profils
scripts/signals.py            breakouts, anomalies, explications, profils MU/SNDK, What changed
scripts/backtest.py           backtest hebdomadaire top 10
scripts/tests/                tests des formules (lancés avant chaque collecte)
data/latest.json              snapshot du jour (lu par la page)
data/alerts.json              alertes du jour
data/status.json              résultat de la dernière tentative de collecte
data/history/AAAA-MM-JJ.json  un fichier par jour
data/series/TICKER.json       1 an de cours + indicateurs pour les graphiques
data/backtest/results.json    résultats du backtest
.github/workflows/daily.yml   collecte lun-ven 22h30 UTC + lancement manuel + publication de la page
```

`data/prices.parquet` (cache des cours) est conservé dans le cache de GitHub Actions et pas dans le dépôt, sinon le dépôt grossirait de plusieurs Mo chaque jour. S'il disparaît, la collecte suivante retélécharge simplement l'historique.

### Lancer à la main (optionnel, si Python est installé)

```
pip install -r requirements.txt
python scripts/collect.py          # --full pour tout retélécharger
python scripts/backtest.py         # --top 5 --thresholds 0,60,70 --weights config/autre.json
python -m unittest discover -s scripts/tests -v
```
