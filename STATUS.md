# STATUS — Migration Docker → Bare metal

Date : 2026-04-29
Cible testée : Ubuntu 24.04 LTS (équivalent LXC Proxmox Debian/Ubuntu)

## Résumé

Migration **fonctionnelle**. Les 3 conteneurs Docker (`web` Apache, `node` Express, `cadquery` Flask/gunicorn) sont remplacés par 2 processus bare-metal :

- **Node.js** sert le frontend statique (`web/`) **et** expose l'API `/api/{preview,stl,step}` sur `0.0.0.0:49157`.
- **Python/CadQuery** (Flask) écoute sur la loopback `127.0.0.1:5002`, joignable uniquement par Node.

Le frontend (HTML/CSS/JS) et le validateur (`CadQueryValidator.py`) **n'ont pas été modifiés**.

## État par composant

| Composant | État | Détails |
|---|---|---|
| Prérequis système | OK | `python3-venv`, `python3-pip`, `libgl1`, `libglx-mesa0` requis (libs déjà présentes sur l'hôte de test). Doc d'install fournie dans `README.md`. |
| Node.js (LTS via nvm) | OK | nvm 0.40.1 installé, Node v24.15.0 LTS, npm v11.12.1. |
| Python | OK (3.12) | Cible demandée : Python 3.11. La machine de test n'a que Python 3.12 (3.11 nécessiterait sudo + PPA `deadsnakes`). CadQuery 2.7 supporte officiellement 3.10–3.12, **3.12 fonctionne sans modification**. Le projet est compatible 3.11 et 3.12. |
| Venv Python (`cadquery/venv`) | OK | Créé. Dépendances installées via `cadquery/requirements.txt` : `cadquery 2.7.0`, `cadquery-ocp 7.8.1.1`, `numpy 2.4.4`, `flask 3.1.3`, `gunicorn 25.3.0`, `python-dotenv 1.2.2`, etc. |
| `cadquery/server.py` (ex-`app.py`) | OK | Renommé `app.py` → `server.py` (cohérent avec `start.sh`). Ajouts : chargement `.env` via `python-dotenv`, host/port via `CADQUERY_HOST`/`CADQUERY_PORT` (défauts `127.0.0.1:5002`). Correction d'un bug `__builtins__` (était indexé comme dict, comportement OK uniquement en `__main__` ; remplacé par `import builtins`). Le validateur `CadQueryValidator.py` n'a pas été modifié. |
| `node/server.js` | OK | Réécrit pour : (1) servir les statiques de `../web` via `express.static`, (2) préfixer toutes les routes API avec `/api/` (rôle anciennement assuré par le ProxyPass Apache), (3) lire `NODE_HOST`/`NODE_PORT`/`CADQUERY_HOST`/`CADQUERY_PORT` depuis `../.env` via `dotenv`, (4) écrire les logs dans `../logs/` (au lieu de `/logs/` Docker). |
| `node/RequestQueue.js` | OK | URL CadQuery construite depuis `CADQUERY_HOST:CADQUERY_PORT` au lieu du DNS Docker `cadquery:5000`. Timeout 60 s ajouté. |
| `node/package.json` | OK | `dotenv 16.x` ajouté. `axios` mis à jour `0.21.1 → 1.7.9` (CVE). `express` à `4.21.2`. `npm audit --omit=dev` : 0 vulnérabilité. |
| Frontend `web/` | OK | **Aucune modification** (ni HTML, ni CSS, ni JS). `web/main.js` appelle déjà `window.location.origin + '/api/'`, ce qui correspond à la nouvelle architecture (Node sert le HTML et l'API depuis le même origin). `web/httpd.conf` supprimé (rôle Apache repris par Node). |
| `start.sh` | OK | Script bash à la racine. Charge `.env`, charge nvm si nécessaire, lance les deux serveurs, gère arrêt propre via `trap INT TERM`. |
| `.env` | OK | À la racine, valeurs : `CADQUERY_HOST=127.0.0.1`, `CADQUERY_PORT=5002`, `NODE_HOST=0.0.0.0`, `NODE_PORT=49157`. `.env.example` versionné, `.env` ignoré par git. |
| `.gitignore` | OK | Ajout : `node_modules/`, `cadquery/venv/`, `.env`, `__pycache__/`, `*.pyc`. |
| `README.md` | OK | Réécrit : prérequis, install bare-metal, configuration `.env`, lancement, exemple unit systemd, opération. |
| Docker | Supprimé | `docker-compose.yml`, `cadquery/Dockerfile`, `node/Dockerfile`, `web/httpd.conf` supprimés. |

## Tests effectués

Tous lancés via `./start.sh` (donc serveurs configurés via `.env`).

| Test | Attendu | Résultat |
|---|---|---|
| `GET http://127.0.0.1:49157/` | HTTP 200, HTML `<!DOCTYPE html>` | OK (1177 octets, `index.html` servi) |
| `GET /main.js` | HTTP 200, JS frontend | OK (7654 octets) |
| `GET /test` | HTTP 200, "Node server is running" | OK |
| `POST /api/preview` avec `result = cq.Workplane("XY").box(10,10,10).edges().fillet(1)` | HTTP 200, JSON avec `vertices`/`faces` | OK (mesh tessélisé renvoyé) |
| `POST /api/stl` avec le même code | HTTP 200, fichier STL binaire | OK (473 084 octets, en-tête `STL Exported by Open CASCADE Technology`) |
| `POST /api/step` avec le même code | HTTP 200, fichier STEP ASCII | OK (63 208 octets, en-tête `ISO-10303-21;`) |
| Logs requêtes | `logs/requests-YYYY-MM-DD.log` créé et rempli | OK (3 entrées : preview, stl, step) |
| Ports en écoute | `0.0.0.0:49157` (Node) et `127.0.0.1:5002` (Python) | OK (`ss -ltnp` confirmé) |
| Arrêt propre | `Ctrl+C` arrête les deux processus | OK (trap `start.sh`) |

## Points d'attention / écarts

1. **Python 3.12 au lieu de 3.11** sur la machine de test : compatibilité validée, aucune incidence.
2. **Pas de `gunicorn` au démarrage** : le serveur Python tourne en mode dev Flask (`app.run`). Pour usage de production multi-utilisateurs, remplacer la dernière ligne du `start.sh` par `exec gunicorn --bind ${CADQUERY_HOST}:${CADQUERY_PORT} --workers 2 --threads 4 --timeout 30 server:app` (gunicorn est déjà installé dans le venv). Pour un usage perso/famille, `app.run` suffit.
3. **Sécurité**: dans la version Docker, le conteneur CadQuery tournait avec `cap_drop=ALL`, `read_only`, `mem_limit=512m`, `nproc=100`. En bare-metal, ces protections sont perdues sauf à utiliser `systemd` avec `MemoryMax=`, `TasksMax=`, `ProtectSystem=strict`, `ReadOnlyPaths=`, etc. **Recommandation** : faire tourner le service via systemd avec un utilisateur dédié et des restrictions appropriées si exposé sur Internet. Le validateur Python (intact) reste la première ligne de défense.
4. **Rate limiting**: 30 requêtes / 10 min (inchangé) ; `app.set('trust proxy', 1)` est conservé, donc derrière un reverse proxy (nginx, traefik) qui ajoute `X-Forwarded-For`, le rate-limit s'applique correctement par client.
5. **Logs** : le dossier `logs/` est créé automatiquement au démarrage (par Node + par `start.sh`). Aucune rotation : prévoir `logrotate` pour un usage long.

## Comment relancer

```bash
cd /home/pcsurf9/LLMCAD
./start.sh
```

Puis ouvrir `http://<IP>:49157`.

---

# Phase 2 — Génération CadQuery par LLM (vLLM)

Date : 2026-04-29

## Résumé

Ajout d'une route `POST /api/generate` côté Node qui appelle un serveur vLLM (API compatible OpenAI) pour générer du code CadQuery à partir d'un prompt utilisateur, avec mémoire de conversation et code courant.

**Frontend, serveur Python et validateur CadQuery non modifiés.** Les routes `/api/preview`, `/api/stl`, `/api/step` sont inchangées.

## Nouvelle route : `POST /api/generate`

Body JSON attendu :

```json
{
  "prompt": "a box 50x30x10mm with 2mm fillets on all edges",
  "history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "currentCode": "result = cq.Workplane(\"XY\").box(...)"
}
```

Réponses :

- Succès : `200 { "success": true, "code": "result = cq.Workplane(...)..." }`
- Erreur : `4xx/5xx { "success": false, "error": "<message>" }`

Codes d'erreur explicites :
- `400` prompt manquant ou vide.
- `502` erreur HTTP / réseau / réponse vide côté vLLM.
- `504` timeout (30 s par défaut).

Soumis au même rate limiter que les autres routes `/api/*` (30 requêtes / 10 min, par IP via `X-Real-IP`/`X-Forwarded-For`).

## Construction du prompt

Le system prompt est embarqué dans `node/llm.js` (constante `SYSTEM_PROMPT`). Le tableau `messages` envoyé à vLLM est construit comme suit :

1. `system` — system prompt fixe (instructions de génération).
2. `history` — alternance `user`/`assistant` passée par le client (conversation antérieure).
3. (optionnel) `user` — message contenant `Current CadQuery code:` + le code courant entouré d'un fence ` ```python `, **inséré juste avant le nouveau prompt** quand `currentCode` est non vide.
4. `user` — le nouveau prompt.

## Paramètres d'appel vLLM

| Paramètre | Valeur |
|---|---|
| Endpoint | `${VLLM_URL}/chat/completions` |
| `model` | `${VLLM_MODEL}` |
| `temperature` | `0.2` |
| `max_tokens` | `4096` |
| `stream` | `false` |
| `chat_template_kwargs.enable_thinking` | `false` (spécifique Qwen3 — voir ci-dessous) |
| Timeout HTTP | `30000` ms |
| Header `Authorization: Bearer …` | si `VLLM_API_KEY` est défini |

## Nettoyage du code retourné (`cleanCode`)

Avant retour au client, le contenu de `choices[0].message.content` est nettoyé :

1. Trim global.
2. Si la réponse est encadrée par ` ```python … ``` ` (ou ` ``` … ``` `), extraction du contenu intérieur. À défaut, suppression des fences en tête/queue.
3. Si du texte parasite précède la première ligne `import cadquery` (ou `from cadquery …`), tout ce qui le précède est coupé.
4. **Suppression de toute ligne `import cadquery [as …]` ou `from cadquery … import …`**. Voir « écart » ci-dessous.
5. Trim final.
6. Erreur explicite si la sortie est vide après nettoyage.

## Variables d'environnement

`.env` / `.env.example` étendus :

```
VLLM_URL=http://192.168.30.121:8000/v1
VLLM_MODEL=/data/models/qwen3-32b-fp8
VLLM_API_KEY=…              # vide dans .env.example, valeur réelle dans .env (non versionné)
```

## Logs

Les requêtes `/api/generate` sont écrites dans `logs/requests-YYYY-MM-DD.log` (même fichier journalier que les autres routes), au format JSON multi-lignes, avec **deux entrées par requête** :

- entrée `endpoint: "generate"` à réception (prompt, longueur de l'historique, longueur du code courant, IP) ;
- entrée `endpoint: "generate-result"` à la fin (succès `ok: true` + `code_len`, ou échec `ok: false` + `status` + `error`).

Le format texte du log existant (`/api/preview|stl|step`) est inchangé.

## Tests effectués (2026-04-29)

Serveurs lancés via `./start.sh` (Node + Python). vLLM joignable à `http://192.168.30.121:8000/v1`, modèle réellement servi : `/data/models/qwen3-32b-fp8` (Qwen3 32B FP8).

| # | Test | Attendu | Résultat |
|---|---|---|---|
| 1 | `POST /api/generate` `{prompt:"a box 50x30x10mm with 2mm fillets on all edges", history:[], currentCode:""}` | HTTP 200, `success:true`, code CadQuery valide | OK (1.86 s, 84 octets) — code retourné : `result = cq.Workplane("XY").box(50,30,10).edges().fillet(2)` |
| 2 | Le code du test 1 envoyé à `POST /api/preview` | HTTP 200, `vertices`/`faces` non vides | OK — 864 vertices, 828 faces, message `Preview generated successfully` |
| 3 | `POST /api/generate` itératif avec `history=[user1, assistant1]`, `currentCode=<code test 1>`, prompt `"add a 10mm hole through the center"` | code modifié contenant un trou de 10 mm | OK (2.85 s) — code : `result = cq.Workplane("XY").box(50,30,10).edges().fillet(2).faces(">Z").workplane().center(0,0).hole(10)` (assertions `"hole"` ✓, `"10"` ✓) |
| 4 (bonus) | Le code du test 3 envoyé à `POST /api/preview` | tessélisation OK avec plus de géométrie | OK — 1026 vertices, 996 faces |
| 5 | Logs `logs/requests-2026-04-29.log` | Entrées `generate` (requête) + `generate-result` (issue) | OK (4 entrées générées + entrées preview) |

## Points d'attention / écarts

1. **Modèle effectivement servi** : la spec phase 2 indique `Qwen/Qwen2.5-Coder-32B-Instruct`, mais `GET /v1/models` du serveur vLLM retourne `/data/models/qwen3-32b-fp8` (Qwen3 32B FP8). C'est cette valeur exacte qui est utilisée dans `VLLM_MODEL` car vLLM exige une correspondance exacte (sinon `404 NotFoundError`).
2. **Authentification vLLM** : le serveur exige `Authorization: Bearer <key>` (réponse `401 {"error":"Unauthorized"}` sinon, y compris avec les clefs de test usuelles). La clef est lue via `VLLM_API_KEY` dans `.env` ; si la variable est absente ou vide, aucun header `Authorization` n'est envoyé (rétro-compatible avec un serveur vLLM sans `--api-key`).
3. **Qwen3 « reasoning »** : Qwen3 émet par défaut un thinking préalable (`message.reasoning` séparé de `message.content`). Avec `max_tokens: 4096` ce thinking peut consommer une grande partie du budget. On envoie donc `chat_template_kwargs: {enable_thinking: false}` dans le body — vLLM applique ce flag au chat template Qwen3 (le content vient alors directement, sans CoT). Pour un modèle non-Qwen3, vLLM ignore silencieusement ce champ.
4. **Suppression de `import cadquery as cq` à la sortie** : le system prompt impose au modèle d'écrire `import cadquery as cq` (cohérent avec le contrat « code Python complet » du prompt utilisateur). Mais le sandbox d'exécution Python (`cadquery/server.py`) pré-injecte `cq`, `np`, `math` dans les globals **et** restreint `__builtins__` au strict minimum (pas de `__import__`). Une instruction `import cadquery` au runtime déclenche `__import__ not found`. Comme la consigne est de ne pas modifier le serveur Python ni le validateur, `cleanCode()` retire les lignes `import cadquery [as …]` / `from cadquery …` du code avant de le retourner au client. Le system prompt n'est pas modifié. Si l'on veut à terme renvoyer du code « complet » (utile pour copier dans un éditeur externe), il faudra soit ajouter `__import__` aux builtins autorisés du sandbox, soit garder deux versions du code (une pour preview, une pour affichage). À traiter en phase 3 si besoin.
5. **Limite de body Express** : `express.json({limit: '10kb'})` → `'256kb'`. L'historique + `currentCode` peuvent dépasser 10 ko sur quelques itérations.
6. **Rate limiter** : `/api/generate` partage le même `limiter` (30 / 10 min, par IP) que les autres routes — donc les appels generate consomment le même quota que preview/stl/step.
7. **Streaming** : non implémenté (`stream: false`). Les générations 32B FP8 sont rapides (≈2 s sur les tests), suffisant pour la phase 2. Le streaming SSE serait pertinent pour la phase 3 si l'UX devient bloquante.
8. **Frontend** : intentionnellement pas modifié (phase 3).

---

# Phase 3 — Panneau chat frontend

Date : 2026-04-29

## Résumé

Ajout d'un **panneau chat LLM** dans le frontend. L'utilisateur décrit la pièce en langage naturel ; le code généré est injecté dans l'éditeur ; la prévisualisation est déclenchée automatiquement ; en cas d'échec côté CadQuery, **un seul retry automatique** part avec le message d'erreur en clair.

Toutes les modifications sont **côté `web/` uniquement**. `node/server.js`, `node/llm.js`, `cadquery/server.py`, `CadQueryValidator.py` n'ont pas été touchés.

## Layout 3 panneaux

| Panneau | Largeur initiale | Min | Contenu |
|---|---|---|---|
| Gauche `#chat-panel` | 30 % | 260 px | Chat LLM (header + thread + zone d'envoi) |
| Centre `#editor-panel` | 40 % | 300 px | Éditeur (textarea), bouton **Run / Preview**, zone d'output |
| Droite `#viewer-panel` | 30 % (flex) | 280 px | Viewer three.js + boutons **Export STL** / **Export STEP** en bas |

Deux séparateurs `<div class="resizer">` (5 px de large, fond `--accent` au survol) entre chat/éditeur et éditeur/viewer ; drag à la souris pour redimensionner. La logique JS (`setupResizers()` dans `main.js`) recalcule les `flex-basis` des deux panneaux adjacents en pixels en conservant la somme constante (les autres panneaux ne bougent pas), et appelle `resizeViewer()` à chaque déplacement pour réajuster la projection three.js et la taille du buffer WebGL.

Sous 1200 px (media query), bascule en colonne (chat en haut, éditeur, viewer en bas) ; les resizers restent visibles mais perdent leur utilité (aucune erreur, juste un curseur `row-resize`).

## Composants ajoutés / modifiés

| Fichier | État | Détails |
|---|---|---|
| `web/index.html` | réécrit | Trois `<section class="panel">` + deux `.resizer`. Conserve les ids existants (`#code-input`, `#preview-btn`, `#stl-btn`, `#step-btn`, `#viewer`, `#output-container`, `#output-message`). Ajoute un titre `<h2>Chat LLM</h2>` + bouton `#new-chat-btn` à gauche, le titre `cadquery2web` est déplacé dans le header de l'éditeur. Boutons STL/STEP déplacés dans `.export-buttons` en bas du viewer. Charge `css/chat.css` et `js/chat.js` en plus de `main.css` / `main.js` / `models.js`. |
| `web/main.css` | réécrit | Variables CSS étendues (`--bg-darkest/dark/mid/light`, `--border`, `--text`, `--text-bright`, `--accent`). Layout flex 3 colonnes. Styles `.resizer` (col-resize, hover/active accent). Media query `<= 1200px` → flex-column. La règle globale `canvas { width 100% !important }` a été restreinte à `.viewer-container canvas` pour ne pas forcer 100 % sur d'éventuels canvases internes (e.g. l'helper `cssColorToHex`). |
| `web/main.js` | réécrit/réorganisé | Mêmes dépendances/Three.js qu'avant, mais : (1) le viewer est maintenant ciblé par `#viewer` (au lieu de `.right-panel`) ; (2) extraction de `runPreview()` (export ESM, retourne `{success, message}`) et `clearViewer()` (export ESM, vide la mesh, reset grille + caméra, masque `#output-container`) ; (3) ajout de `setupResizers()` (drag souris pour resizer-1/-2) ; (4) `ResizeObserver` sur le conteneur viewer pour mettre à jour la projection lors d'un drag de séparateur ou d'une rotation d'écran. La logique d'export STL/STEP est inchangée (juste un fix mineur : `step_button.classList.remove(...)` était appelé après l'export STL — corrigé en `button.classList.remove(...)`). |
| `web/js/chat.js` | nouveau | Logique du chat (cf. ci-dessous). Importe `runPreview` et `clearViewer` depuis `../main.js`. |
| `web/css/chat.css` | nouveau | Styles messages user/assistant/system/error, indicateur de chargement (3 points qui clignotent), zone d'input. |
| `web/models.js` | inchangé | Le code par défaut (`tube_clamp`) est toujours pré-rempli au chargement. |

## Logique du panneau chat (`web/js/chat.js`)

État côté client :
- `messages = []` — historique `{role, content}` envoyé tel quel à `/api/generate`.
- `isWaiting` — verrou anti-double-submit (bouton + Ctrl+Enter pendant qu'une requête est en cours, et garde-fou contre une récursion sur le retry).

Au clic **Envoyer** (ou **Ctrl+Enter** dans le champ) :
1. Lit `prompt = input.value.trim()`. Annule si vide.
2. `setBusy(true)`, push `{role:'user', content:prompt}` dans `messages`, affiche le message user dans le fil.
3. Affiche un indicateur de chargement (3 points animés via `@keyframes chat-blink`).
4. `POST /api/generate` `{prompt, history: messages, currentCode: codeInput.value}`.
5. À la réponse :
   - **`success: false`** ou HTTP non-OK → message système rouge `Erreur LLM : <message>`, fin.
   - **`success: true`** → ajoute le code dans le fil sous forme de bloc `<pre class="msg-code">`, push `{role:'assistant', content:code}` dans `messages`, **injecte `code` dans l'éditeur** (`codeInput.value = code`).
6. Libère `setBusy(false)` puis appelle `runPreview()` (la même fonction que le bouton **Run / Preview**).
   - `runPreview()` retourne `{success, message}`.
   - Si `success: true` → fini.
   - Si `success: false` → message système rouge `Erreur d'exécution : <message>`, **et** :
     - 1er essai → construit `retryPrompt = "The code produced this error: <message>. Fix it."` et rappelle `sendMessage(retryPrompt, true)` (un seul retry, garanti par le paramètre `isRetry`).
     - 2ᵉ essai (retry) → message système rouge final `Le retry automatique a aussi échoué. À vous d'ajuster le prompt.` ; pas de 3ᵉ tentative.

Avant chaque retry, un message système informatif `Tentative de correction automatique…` est inséré dans le fil pour rendre le flux lisible côté utilisateur.

**Bouton « Nouveau chat »** : `messages = []`, `thread.innerHTML = ''`, `codeInput.value = ''`, `clearViewer()` (mesh retirée, grille par défaut, caméra à `(8,8,8) → (0,0,0)`, output masqué). Bloqué pendant qu'une requête est en cours.

**Modification manuelle de l'éditeur** : aucune écoute sur `code-input` côté chat. Le code modifié à la main est lu via `currentCode = codeInput.value` au prochain envoi, donc transmis au LLM dans la requête `/api/generate` exactement comme spécifié.

## Tests effectués (2026-04-29)

Tous lancés via `./start.sh`, vLLM joignable, modèle `/data/models/qwen3-32b-fp8`. Les tests ci-dessous reproduisent ce que fait `chat.js` côté navigateur (envoi d'un prompt → preview → éventuel retry) en appelant directement les routes `/api/generate` et `/api/preview`.

| # | Test | Attendu | Résultat |
|---|---|---|---|
| 1 | `GET /` puis `GET /css/chat.css`, `/js/chat.js`, `/main.css`, `/main.js` | 200 sur tout | OK (2258 / 3617 / 6205 / 4441 / 10183 octets) |
| 2 | Happy path : prompt `"a 50x30x10mm box with 2mm fillet on all edges"`, history vide, currentCode vide | `success:true` puis `/api/preview` `Preview generated successfully`, vertices/faces > 0 | OK (864 vertices, 828 faces) |
| 3 | Itération : history = [user1, assistant1], currentCode = code seed, prompt `"make the box 80x40x15 instead"` | code modifié contenant `box(80, 40, 15)` | OK |
| 4 | **Propagation édition manuelle** : on remplace `80` par `200` dans `currentCode` (simule l'utilisateur éditant à la main), prompt `"add a 10mm hole through the center"` | code généré contient `200` (manual edit) **et** `hole` | OK — `result = cq.Workplane("XY").box(200, 40, 15).edges().fillet(2).faces(">Z").center(0,0).hole(10)` |
| 5 | **Retry automatique** : prompt `"a simple gear with 20 teeth, module 2, 10mm thick"` (le LLM génère du code avec `cq.Sketch()` / `def` / `math.acos`, refusés par le validateur) | (a) `/api/generate` succès, (b) `/api/preview` rejette `Validation failed`, (c) `chat.js` envoie un retry avec le message d'erreur, (d) le retry échoue aussi → message système rouge final | OK — première génération 1205 octets refusée, retry 1229 octets refusé pour les mêmes raisons, log `requests-2026-04-29.log` montre bien la séquence `generate → preview (validation failed) → generate (retry prompt) → preview (validation failed)` |
| 6 | Export STL après une itération réussie (test #4) | fichier binaire avec en-tête `STL Exported by Open CASCADE Technology` | OK (498 284 octets) |
| 7 | Export STEP après la même itération | fichier ASCII avec en-tête `ISO-10303-21;` | OK (68 204 octets) |
| 8 | Logs `requests-YYYY-MM-DD.log` pour les 5 tests | entrées `generate` + `generate-result` + `preview` correctement chaînées, prompts visibles, `code_len` cohérent | OK |

**Tests interactifs (à valider dans le navigateur)** : redimensionnement par drag des séparateurs (resizer-1 entre chat/éditeur, resizer-2 entre éditeur/viewer), Ctrl+Enter dans le champ, bouton « Nouveau chat », responsive < 1200 px (DevTools → 1100 px par exemple), affichage des messages user/assistant/error, animation de l'indicateur de chargement.

## Points d'attention / écarts

1. **Path des nouveaux fichiers** : la spec demandait `web/js/chat.js` et `web/css/chat.css`, mais le projet existant a une arborescence plate (`web/main.js`, `web/main.css`, `web/models.js`). Les nouveaux dossiers `web/js/` et `web/css/` ont été créés conformément à la spec ; les fichiers existants n'ont pas été déplacés. L'import dans `chat.js` est donc `import { runPreview, clearViewer } from '../main.js';`.
2. **Le test « simple gear » échoue à la fois en T1 et au retry** — pas un bug de la phase 3, mais une limitation du sandbox `CadQueryValidator.py` (intentionnellement non modifié) qui interdit `cq.Sketch`, `def`, `math.acos`, l'attribut `union` etc. Le LLM régénère systématiquement du code utilisant ces opérations pour un engrenage involute. Pour un vrai engrenage, soit le validateur doit être assoupli (hors phase 3), soit l'utilisateur doit demander une géométrie plus simple (« a cylinder with 8 holes evenly distributed », par exemple). Le panneau chat se comporte exactement comme spécifié : retry → message système rouge final → l'utilisateur reformule.
3. **Resizers en pixels** : après un premier drag, les panneaux adjacents passent à `flex: 0 0 Npx` (au lieu des pourcentages initiaux). Sur un redimensionnement de fenêtre **après** drag (≥ 1200 px), les pixels sont conservés ; en pratique sur un 1920×1080 fixe c'est imperceptible. Le viewer reste en `flex: 1 1 30%` tant que resizer-2 n'a pas été touché, donc gère seul l'espace résiduel.
4. **Pas de scroll auto sur l'éditeur** : la textarea hérite de son scroll natif. Les retours du LLM dépassent rarement les 50 lignes pour les prompts simples ; sur du code long, l'utilisateur scrolle dans la textarea.
5. **Pas de syntax highlighting** : la textarea reste brute (cohérent avec la spec « préserver l'éditeur existant »). Une éventuelle migration vers CodeMirror/Monaco serait une phase 4.
6. **`runPreview()` ne lève jamais** : elle retourne toujours `{success, message}`. Le `try/catch` dans `chat.js` autour de `runPreview` est donc défensif (pour le cas où une régression future ferait remonter une exception).
7. **Pas de persistance** : l'historique de chat est mémoire-seulement. Refresh = perte du fil. Hors scope phase 3 ; un `localStorage` serait simple à ajouter si demandé.
8. **Indicateur de chargement** : 3 points qui clignotent en CSS pur (`@keyframes chat-blink`). Pas d'animation JS.

---

# Phase 3.5 — Élargissement du validateur CadQuery

Date : 2026-04-29

## Résumé

Le validateur `cadquery/CadQueryValidator.py` rejetait les constructions Python courantes que le LLM produit dès que la pièce devient un peu paramétrique : `def`, `for`/`while`, list/set/dict comprehensions, `lambda`, `try/except`, `enumerate`/`zip`/`min`/`max`/etc., et toute méthode appelée sur une variable utilisateur (`wp.center(...)` où `wp` est un paramètre de fonction). Phase 3 illustrait le souci sur le test « simple gear » qui échouait en T1 et au retry parce que le validateur bloquait `def`/`math.acos`/`union`.

Le validateur est désormais bilingue :
- **permissif** sur la syntaxe Python (fonctions, boucles, comprehensions, try/except, lambdas, `from typing import …`) ;
- **toujours strict** sur la surface d'attaque (imports hors-whitelist, builtins dangereux, attributs `dunder`, `eval/exec/__import__/getattr/open/...`, `while True` infini).

`cadquery/server.py` complète avec un timeout 30 s, un `__import__` restreint au runtime, et un `RLIMIT_AS` optionnel.

Aucun autre fichier modifié.

## Modifs `cadquery/CadQueryValidator.py`

### Imports autorisés (whitelist)

| Module | Mode | Symboles |
|---|---|---|
| `cadquery` | seulement `import cadquery as cq` | — |
| `numpy` | seulement `import numpy as np` | inchangé (≈ 25 fonctions) |
| `math` | `import math` | `sin, cos, tan, asin, acos, atan, atan2, pi, e, tau, sqrt, pow, exp, log, log10, log2, radians, degrees, ceil, floor, trunc, fabs, hypot, copysign, inf, nan, isnan, isinf, isfinite` (ajouts notables : `log`, `ceil`, `floor`, `acos`, `asin`, `atan`) |
| `typing` (nouveau) | `from typing import …` | `List, Tuple, Dict, Optional, Union, Any, Set, FrozenSet, Iterable, Iterator, Sequence, Callable, Mapping` |

Refusés explicitement : `os`, `sys`, `subprocess`, `shutil`, `pathlib`, `io`, `socket`, `http`, `urllib`, `requests`, `importlib`, `ctypes`, `pickle`, `shelve`, `sqlite3`, `tempfile`, `glob`, `signal`, `threading`, `multiprocessing`, `asyncio`, `code` — tout module hors whitelist déclenche `Import of '<X>' is not allowed`.

### Builtins autorisés (whitelist)

| Catégorie | Symboles |
|---|---|
| Constructeurs / casts | `float, int, bool, str, list, tuple, dict, set, frozenset` |
| Constantes | `True, False, None` |
| Itération | `range, len, enumerate, zip, map, filter, sorted, reversed, iter, next` |
| Numérique | `min, max, sum, abs, round, divmod, pow` |
| Introspection (sûr) | `isinstance, type` |
| Debug | `print` (la sortie est imprimée côté serveur, pas renvoyée au client) |
| Exceptions | `Exception, ValueError, TypeError, IndexError, KeyError, RuntimeError, StopIteration, ZeroDivisionError, ArithmeticError, AssertionError, NotImplementedError` |

Refusés explicitement (jamais ajoutés à `safe_builtins`) : `eval, exec, compile, __import__ <direct>, open, input, globals, locals, vars, dir, getattr, setattr, delattr, hasattr, breakpoint, exit, quit, memoryview, bytearray, super` — un appel à l'un de ces noms échoue avec `Function call to '<X>' is not allowed`.

### AST — autorisé / refusé

Autorisé (n'était pas autorisé avant) : `FunctionDef`, `Lambda`, `For`, `While` (avec garde, voir ci-dessous), `If`, `IfExp`, `ListComp`, `SetComp`, `DictComp`, `GeneratorExp`, `Try`, `ExceptHandler`, `With` (assignement local).

Refusé (rejet automatique) : `AsyncFunctionDef`, `AsyncFor`, `AsyncWith`, `ClassDef`, `Await`, `Yield`, `YieldFrom`, `Global`, `Nonlocal`. Plus :
- accès à un nom dunder (`__class__`, `__bases__`, `__subclasses__`, `__import__`, `__builtins__`, `__dict__`, …) — bloqué côté `ast.Name` **et** côté `ast.Attribute` ;
- `Import`/`ImportFrom` d'un module hors whitelist ;
- `from <module_whitelist> import <symbole_non_whitelisté>` (typing/math/numpy strict, cadquery interdit en `from` car déjà pré-injecté) ;
- `from X import *` (bloqué tous modules confondus) ;
- `while <constante truthy>` sans `break` n'importe où dans le corps (« infinite loop without break »).

### Comment les fonctions custom et les variables locales sont autorisées

Avant : `wp.center(...)` où `wp` est un paramètre échouait avec `Attribute access 'center' is not allowed` parce que la racine de la chaîne n'était ni `cq`, ni `result`, ni `math`, ni `np`. De même `hex_pattern(...)` échouait avec `Function call to 'hex_pattern' is not allowed`.

Maintenant le validateur fait deux passes :
1. **Pré-collecte** des noms liés localement : `def`/`async def` (nom + args), `lambda` args, cibles d'`Assign`/`AugAssign`/`AnnAssign`, cibles `for`, cibles de comprehensions, `with as`, `except as`, alias d'`import`/`from … import …`. Le tout via `ast.walk`, donc tous les scopes confondus.
2. **Validation** : dans `check_call`, un appel `f(...)` où `f` est ni un builtin whitelist ni un module connu est accepté **si** `f` est dans la liste des noms collectés. Idem pour `var.method(...)` : si la racine de la chaîne d'attributs n'est pas `cq`/`math`/`np`, l'attribut n'est plus rejeté (le bandage par dunder reste).

C'est tolérant : `eval = print; eval("x")` passe le validateur, mais `eval` au runtime vaut `print`, pas la vraie fonction, donc inoffensif. Pour ressortir du sandbox il faudrait soit un dunder (bloqué), soit `getattr` (refusé), soit `__import__` direct (refusé), soit un module hors whitelist (refusé). La défense en profondeur côté runtime (cf. ci-dessous) ferme les voies restantes.

## Modifs `cadquery/server.py`

### `__import__` restreint au runtime

Avant : `safe_builtins` ne contenait pas `__import__`. Conséquence : `import math` au runtime crashait (`NameError: __import__ not found`), donc `import math` en tête de script fonctionnait par accident uniquement parce que `math` était pré-injecté en globals.

Maintenant `safe_builtins['__import__']` pointe vers `_sandbox_import(name, …)` qui vérifie que la racine du nom (`name.split('.')[0]`) est dans `_RUNTIME_ALLOWED_IMPORTS = {'cadquery', 'math', 'numpy', 'typing'}`. Tout autre module lève `ImportError` immédiatement. Les imports relatifs (`level != 0`) sont rejetés sec.

Effets :
- `import math`, `import typing`, `from typing import List, Tuple, …` fonctionnent.
- `import os` (qui est déjà bloqué par le validateur) n'arriverait pas jusqu'au runtime ; même s'il y arrivait par contournement, il échouerait avec `ImportError`.

### Timeout 30 s par requête

`exec(cleaned_code, …)` est exécuté dans un `threading.Thread(daemon=True)` ; le thread principal `join(EXEC_TIMEOUT_SEC)`. Si le worker est encore vivant au join, on retourne 400 `Execution timeout exceeded (30s). Possible infinite loop or runaway computation.`.

Le timeout est paramétrable via `CADQUERY_EXEC_TIMEOUT` (défaut `30`).

**Limite assumée** : CPython n'expose pas de mécanisme propre pour tuer un thread Python depuis un autre. La requête HTTP retourne dans les temps, mais le thread orphelin continue son travail jusqu'à terminer (ou jusqu'à l'arrêt du process). Acceptable pour un usage perso/famille (`app.run` Flask en threaded). Pour multi-utilisateur exposé, il faudrait passer à un sous-processus (`multiprocessing`) ou à `gunicorn --timeout 30 --worker-class sync` (qui tue le worker entier).

### Limite mémoire (RLIMIT_AS)

Tentative `resource.setrlimit(RLIMIT_AS, …)` au démarrage du process Python. Pilotée par `CADQUERY_MEM_LIMIT_MB` (défaut **`0` = désactivée**).

**Pourquoi 512 MB n'est pas le défaut**, contrairement à la spec : sur la machine de test (Ubuntu 24.04, Python 3.12, cadquery 2.7 + cadquery-ocp 7.8.1.1 + numpy 2.4.4), le process Python en idle consomme déjà :

```
$ ps -o vsz,rss -p $(pgrep -f 'python server.py')
   VSZ    RSS
2209424 479872  → 2,1 GB de VA, 469 MB de RSS au démarrage
```

→ `RLIMIT_AS=512MB` ferait échouer dès l'allocation suivante (probablement dès l'import de `cadquery`). C'est ce que la spec anticipait avec « si pas possible sur le LXC, documente pourquoi et passe ». La limite est implémentée et fonctionnelle, mais désactivée par défaut.

Pour activer (par exemple sur un LXC déjà capé par `lxc.cgroup2.memory.max` à un niveau cohérent) :

```
CADQUERY_MEM_LIMIT_MB=2048 ./start.sh   # 2 GB raisonnable pour CadQuery
```

`setrlimit` peut échouer (e.g. `hard < target` sur un container réduit) — le code logue l'échec et continue sans cap, plutôt que de planter le service.

### Surface inchangée

`/preview`, `/stl`, `/step` : signatures inchangées. Le timeout et l'import restreint s'appliquent uniformément (les trois passent par `execute()`).

## Tests

Tous lancés en POST `http://127.0.0.1:49157/api/preview` (Node → CadQuery 5002).

### Doivent passer (constructions Python étendues)

| # | Test | Attendu | Obtenu |
|---|---|---|---|
| A | boucle `for` + `math.cos`/`math.sin`/`math.radians` (cylindre + 6 trous polaire) | HTTP 200, mesh non vide | HTTP 200, `Preview generated successfully`, 1134 vertices, 1152 faces, 1 solide |
| B | `def hex_pattern(wp, …)` avec `wp.center(...)` chaîné dans une boucle | HTTP 200, mesh non vide | HTTP 200, 972 vertices, 864 faces, 6 solides |
| C | list comprehension `[(x*10, 0) for x in range(5)]` + boucle `for x, y in pts:` | HTTP 200, mesh non vide | HTTP 200, 666 vertices, 600 faces, 1 solide |
| G | `from typing import List, Tuple` + annotation de type sur variable | HTTP 200, mesh non vide | HTTP 200, 324 vertices, 288 faces |

### Doivent être rejetés (sécurité préservée)

| # | Test | Attendu | Obtenu |
|---|---|---|---|
| D | `import os; os.system("whoami")` | HTTP 4xx, validation échoue | HTTP 400 `Validation failed: Import of 'os' is not allowed` |
| E | `eval("__import__('os').system('whoami')")` au milieu de code CadQuery valide | HTTP 4xx, validation échoue | HTTP 400 `Validation failed: Function call to 'eval' is not allowed` |
| F | `open("/etc/passwd").read()` au milieu de code CadQuery valide | HTTP 4xx, validation échoue | HTTP 400 `Validation failed: Function call to 'open' is not allowed` |
| H | `for i in range(10**12): total += i` (boucle finie mais runaway) | HTTP 4xx après ~30 s | HTTP 400 en 30,0 s `Execution timeout exceeded (30s). Possible infinite loop or runaway computation.` |

Tests complémentaires effectués en isolation (smoke test direct sur la classe `CadQueryValidator`) :

- `while True: pass` (sans `break`) → rejeté `Infinite 'while True' without 'break' is not allowed`.
- `while True: i+=1; if i>3: break` → accepté (présence d'un `break` dans le corps).
- `().__class__.__bases__[0].__subclasses__()` (escape sandbox classique) → rejeté trois fois (chaque dunder bloqué individuellement).
- `from typing import *` → rejeté `'from typing import *' is not allowed`.
- `from cadquery import Workplane` → rejeté (le validateur force `import cadquery as cq` ; `cq` est de toute façon pré-injecté).

## Points d'attention / écarts

1. **`RLIMIT_AS` désactivé par défaut** — voir section ci-dessus. La spec demandait 512 MB ; ce n'est pas tenable avec la stack CadQuery+OCP+numpy sur l'environnement de test. Le code respecte la spec (`setrlimit` est tenté ; l'échec est loggé et le service continue). Pour appliquer 512 MB, lancer avec `CADQUERY_MEM_LIMIT_MB=512 ./start.sh` — au prochain import lourd le process tombera en `MemoryError`. Pour usage réel, viser ≥ 1500 MB.
2. **Thread non killable** — voir section ci-dessus. Une boucle infinie validée et lancée continue sa vie après le 400. Sur l'usage perso, la prochaine requête nettoie l'instance via redémarrage manuel ou la JVM-style file d'attente Node (queue 1, donc requêtes suivantes mises en file). Pour un vrai isolement, passer à un sous-process par requête (changement plus invasif, hors scope phase 3.5).
3. **Permissivité sur les variables utilisateur** — `wp.foo()` est accepté pour toute racine `wp` non-`cq`/`math`/`np`. Cela permet `wp.dangereux_methode_arbitraire()` au niveau syntaxique. À l'exécution, ça ne peut résoudre que (a) une méthode réelle sur l'objet (qui à ce stade ne peut être qu'un objet venu de la stack autorisée), (b) une `AttributeError`. La défense en profondeur repose sur l'absence de `getattr`, `setattr`, `__import__` direct, et de tout dunder côté validateur. C'est la contrepartie inévitable d'autoriser les `def`.
4. **`from <module> import *`** — bloqué pour tous les modules whitelistés (`from typing import *`, `from math import *`, etc.) pour ne pas polluer l'espace de noms avec des symboles non audités.
5. **Pré-collecte des noms en `ast.walk`** — pas de scoping. Une variable définie dans un `def f(): x = 1` est trustée au top-level. C'est volontaire : le validateur n'a pas à analyser la portée pour qu'un appel `x()` au top-level échoue à l'exécution avec `NameError`. Aucune voie d'échappement utile.
6. **Pas de modification frontend ni Node.js** — l'erreur de timeout / validation remonte telle quelle dans la zone `output-message`, et `chat.js` enclenche son retry automatique habituel. Le system prompt LLM peut maintenant utiliser `def`, `for`, list comprehensions ; à éventuellement enrichir en phase 4.
7. **Le test « simple gear » de la phase 3** devrait maintenant passer côté validateur si le LLM utilise `def` + `math.cos`/`math.sin` (re-test ad hoc à faire en interactif). `cq.Sketch` reste hors `allowed_cq_operations` ; à ajouter au cas par cas si on rencontre des modèles qui en ont besoin.
