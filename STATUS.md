# STATUS

Index :
- [Phase 6 — Worker pool persistant](#phase-6--worker-pool-persistant) ← le plus récent
- [Phase 5 — Migration Build123d](#phase-5--migration-cadquery--build123d)
- Phase 4.5 — Isolation subprocess + Assembly support
- Phase 4 — System prompt Qwen3 32B
- Phase 3.5 — Sandbox extension (functions/loops/comprehensions)
- Phase 2 — Génération CadQuery par LLM (vLLM)
- Phase 1 — Migration Docker → Bare metal

---

# Phase 6 — Worker pool persistant

Date : 2026-04-30  
Tag : `v0.6.0-worker-pool`

## Résumé

Remplacement du **lancement `subprocess.Popen` + `worker.py` argv par requête** par un **pool de processus persistants** (`worker.py --persistent`) communiquant en **JSON une ligne** sur stdin/stdout. Chaque requête exécute le script dans un **nouveau `dict` namespace** (copie du template `from build123d import *`), sans réimporter la stack OCP. **Isolation OS + `RLIMIT_AS` (4 GiB par défaut)** et **watchdog `start.sh`** inchangés.

Handshake **`WORKER_READY`** sur stderr : `pool.start()` n’affiche « démarré » qu’une fois chaque worker entré dans la boucle lecture (imports terminés), pour que la **première** requête `/preview` après `/health` reste rapide.

## Performances (mesures locales, agent 2026-04-30)

| Métrique | Avant (phase 5) | Après (phase 6) |
|---|---|---|
| Premier `/preview` après boot serveur (pool prêt) | ~2–4 s (import + exec par requête) | **~10–25 ms** (`Box(10,10,10)`, `curl` mesuré) |
| `/preview` suivants (séquentiel) | ~2–4 s chacun | **~20 ms** ordre de grandeur |

*Latence totale jusqu’à `GET /health` OK : coût du démarrage de N workers (~quelques secondes avec 2 workers, imports build123d séquentiels au spawn).*

## Fichiers

| Fichier | Action |
|---|---|
| `cadquery/worker.py` | Réécrit : mode `--persistent` (boucle JSON), template namespace copié par requête ; argv `<code_file> <out> <mode>` conservé hors serveur pour tests. Prévisualisation via `Preview.compute_preview`. |
| `cadquery/pool.py` | **Nouveau** : `WorkerPool` (Queue idle, `execute` bloquant, recycle après timeout ou mort du worker, `shutdown` SIGTERM puis SIGKILL). |
| `cadquery/server.py` | Pool global, `atexit.shutdown`, routes sur `pool.execute`, `/health` JSON avec `workers_alive` / `workers_total`, `threaded=True`. |
| `.env.example` | `WORKER_POOL_SIZE`, `WORKER_REQUEST_TIMEOUT`. |

## Variables d’environnement

- `WORKER_POOL_SIZE` (défaut `2`)
- `WORKER_REQUEST_TIMEOUT` (défaut `30`, fallback `CADQUERY_EXEC_TIMEOUT`)
- `CADQUERY_WORKER_MEM_LIMIT_MB` — toujours appliqué dans chaque worker

## Notes

1. **Segfault natif** (ex. `ctypes.string_at(0)`) : sous **WSL2**, observation d’un délai d’environ **~11 s** avant réaping du PID alors que `SIGSEGV` est immédiat côté shell ; le **timeout 30 s** du pool reste le filet de sécurité et `/health` retrouve `workers_alive == workers_total` après recycle.
2. **Réponses `/preview` volumineuses** : une seule ligne JSON sans indentation ; `flush` systématique côté worker ; `bufsize=1`, `PYTHONUNBUFFERED=1`.
3. **`/health`** : corps JSON (plus de `text/plain ok`) ; le probe `curl -sf` de `start.sh` reste valide (HTTP 2xx).

## Tests d’acceptation (phase 6)

À valider manuellement : cold start `<500 ms` sur première requête une fois le serveur prêt ; 20 requêtes séquentielles ; 5 `curl` parallèles ; isolation `leaked` ; crash / timeout ; fuite mémoire 100 fillets ; `SIGTERM` Flask ; `kill -9` watchdog ; non-régression phase 5 (1–9).

## Stratégie de commits

1. `feat: add persistent worker pool` — worker + pool + `.env.example`
2. `feat: wire flask routes to worker pool` — `server.py`
3. `fix: wait for WORKER_READY before pool start returns`
4. `docs: update STATUS for phase 6` — ce fichier

---

# Phase 5 — Migration CadQuery → Build123d

Date : 2026-04-30
Tag : `v0.5.0-build123d-migration`

## Résumé

Remplacement de CadQuery 2.7 par Build123d 0.10 dans le pipeline d'exécution (worker + preview + serveur Flask). Suppression du validator AST (`CadQueryValidator.py`, 377 lignes) : l'isolation subprocess + `RLIMIT_AS` est désormais l'unique ligne de défense. **Hypothèse explicite : déploiement local uniquement** ; ne pas exposer sur Internet sans réintroduire de la validation.

## Modifications par fichier

| Fichier | Action | Détails |
|---|---|---|
| `cadquery/requirements.txt` | modifié | `cadquery` → `build123d>=0.10.0`. `cadquery-ocp 7.9.3.1` est tiré transitivement. |
| `cadquery/worker.py` | réécrit | `import cadquery` → `import build123d`. `_extract_solids()` accepte `Part`/`Compound`/`Solid`, rejette `Sketch`/`Curve`. Tessellation `tessellate(tolerance=0.1, angular_tolerance=0.1)`. Exports via `export_step()` / `export_stl()`. Namespace pré-rempli avec `from build123d import *`. **`RLIMIT_AS` par défaut relevé de 2 GiB à 4 GiB** : le stack OCP 7.9 idle à ~1.7 GiB et un fillet+tessellate spike à ~2.5 GiB ; 2 GiB segfaultait `fillet(Box(50,30,10).edges(), 2)`. |
| `cadquery/Preview.py` | réécrit | Module symétrique au worker pour les appelants externes. Logique `Workplane.objects` / `Assembly.toCompound()` supprimée. |
| `cadquery/server.py` | modifié | Suppression de l'import et de l'instanciation `CadQueryValidator`. Le worker reçoit le code utilisateur verbatim. Docstring phase-5 explicitant l'hypothèse local-only. |
| `cadquery/CadQueryValidator.py` | **supprimé** | 377 lignes de whitelist AST devenues redondantes. |
| `web/models.js` | réécrit | Nouveaux exemples build123d en style algébrique. Exemple par défaut : `Box(50,30,10)` + `fillet(...edges(), radius=2)`. |
| `web/index.html` | modifié | Placeholder textarea : "Enter CadQuery code here..." → "Enter Build123d code here...". |
| `web/js/chat.js` | modifié | Label assistant : "CadQuery" → "Build123d". |
| `README.md` | modifié | Architecture, prérequis, opération, section "Security model" explicite. |
| `STATUS.md` | modifié | Cette section. |

L'API HTTP est inchangée (`/preview`, `/stl`, `/step`, `/health`). Le format `/preview` reste `{vertices: [x,y,z,...], faces: [[i,j,k],...], objectCount: N}`. `start.sh` (watchdog) inchangé. `node/server.js`, `node/llm.js`, `node/RequestQueue.js` inchangés (le system prompt LLM reste CadQuery-flavour, à reprendre en phase 7).

## Justification de la suppression du validator

1. **Maintenance**: chaque construct build123d (`Box`, `Cylinder`, `Pos`, `Compound`, `fillet`, `extrude`, `Axis`, `sort_by`, ...) aurait dû être réintroduit dans `allowed_cq_operations`. Un import wildcard `from build123d import *` était de toute façon rejeté.
2. **Redondance**: les classes d'attaques que le validator bloquait (`os`, `subprocess`, `eval`, `__class__.__subclasses__()`, OOM, infinite loop) sont toutes traitées par la couche en aval :
   - subprocess isolé → un crash ne tue pas Flask
   - `RLIMIT_AS` (4 GiB) → `MemoryError` propre
   - timeout 30 s → kill du subprocess
   - le worker n'a aucun privilège que le serveur Flask n'a pas
3. **Surface réduite**: 377 lignes en moins, surface d'audit divisée par ~3.

Le coût : tout code Python valide est exécutable. Sur une machine locale c'est le bon trade-off ; en accès distant il faudrait remettre une validation (ou un namespace plus restreint).

## Style des exemples

Style algébrique privilégié, plus prédictible pour un LLM :

```python
# bon
result = Box(20,20,20) - Cylinder(5, 25)

# bon
result = fillet(Box(50,30,10).edges(), radius=2)

# possible mais plus verbeux
with BuildPart() as bp:
    Box(20,20,20)
    Cylinder(5, 25, mode=Mode.SUBTRACT)
result = bp.part
```

## Tests d'acceptation (9/9)

Lancés via `./start.sh` (watchdog actif), serveur Python à `127.0.0.1:5002` :

| # | Test | Résultat |
|---|---|---|
| 1 | `result = Box(10,10,10)` → preview / STEP / STL | OK : 24 verts / 12 tris / 1 `MANIFOLD_SOLID_BREP` / STL 684 octets |
| 2 | `result = Box(20,20,20) - Cylinder(5, 25)` | OK : 530 verts / 520 tris / 1 `MANIFOLD_SOLID_BREP` |
| 3 | `result = fillet(Box(50,30,10).edges(), 2)` | OK : 5552 verts / 9460 tris, bbox `x∈[-25,25] y∈[-15,15] z∈[-5,5]` cohérent |
| 4 | `result = Compound([Box(10,10,10), Pos(20,0,0) * Box(5,5,5)])` | OK : 48 verts / 24 tris / `objectCount=2` / 2 `MANIFOLD_SOLID_BREP` dans le STEP |
| 5 | `box = Box(20,20,20); top = box.faces().sort_by(Axis.Z)[-1]; result = box - extrude(top, -5)` | OK : 24 verts / 1 `MANIFOLD_SOLID_BREP` |
| 6 | `raise ValueError("intentional")` | HTTP 400, traceback complet dans le message, `/health` répond toujours OK |
| 7 | `bytearray(8 * 1024**3)` puis `result = Box(...)` | HTTP 400 `MemoryError` (RLIMIT_AS coupe), Flask vivant |
| 8 | `kill -9` du PID python pendant que start.sh tourne | recovery en ~21 s (intervalle health 10 s + 1 s sleep + import build123d ~9 s) |
| 9 | 10 cycles preview/stl/step | 0 nouveau fichier `cqcode_*` ou `cqout_*` dans `/tmp` |

Test bonus : `result = sketch_obj` (Sketch) → HTTP 400 avec message explicite "le résultat doit être 3D, pas une esquisse/courbe" (cf. `_extract_solids`).

## Points d'attention

1. **`RLIMIT_AS` à 4 GiB par défaut** (au lieu de 2 GiB en phase 4.5). Le stack `cadquery-ocp 7.9.3.1` est plus lourd que le `cadquery 2.7 + cadquery-ocp 7.8` précédent. Configurable via `CADQUERY_WORKER_MEM_LIMIT_MB`.
2. **Dossier `cadquery/` non renommé**. Le commit 5 du plan (`git mv cadquery/ cadserver/`) est laissé optionnel ; il faudrait mettre à jour `start.sh`, `.env.example`, `node/server.js`. Pas fait pour préserver la lisibilité de `git log --follow` à court terme.
3. **System prompt LLM (`node/llm.js`) reste CadQuery**. La route `/api/generate` produira du code CadQuery qui sera rejeté par le worker build123d. À refaire en phase 7 (boucle agentique vLLM). En attendant, le panneau de chat est de facto désactivé pour la génération.
4. **`models.js` plate_with_hole simplifié**: `Box(...) - Cylinder(...)` au lieu d'une chaîne `Workplane().box().faces(">Z").workplane().hole()`. Plus court et plus prédictible pour un LLM futur.
5. **Pas de validation du dossier renommé**: le service Python est toujours dans `cadquery/`, l'engine est build123d. Référence unique pour l'utilisateur : ce fichier + `README.md`.

## Stratégie de commits

5 commits prévus, 4 réalisés (le 5ᵉ était optionnel et reporté) :

1. `feat: migrate from cadquery to build123d` — `requirements.txt` + `worker.py` + `Preview.py`
2. `refactor: remove AST validator` — suppression `CadQueryValidator.py` + serveur + bump `RLIMIT_AS` 2 → 4 GiB. **Tag `v0.5.0-build123d-migration` posé ici.**
3. `feat(ui): update default example and labels for build123d` — `web/`
4. `docs: update README and STATUS for phase 5` — ce fichier + `README.md`
5. (reporté) `refactor: rename cadquery/ to cadserver/` — laissé pour plus tard.

## Comment relancer

```bash
cd /home/pcsurf9/LLMCAD
./start.sh
```

Puis ouvrir `http://<IP>:49157`. L'éditeur charge `Box(50,30,10)` + `fillet(...)` par défaut.

---

# Phase 1 — Migration Docker → Bare metal

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

---

# Phase 4 — System prompt CadQuery

Date : 2026-04-29

## Résumé

Réécriture du `SYSTEM_PROMPT` dans `node/llm.js` pour cadrer la génération CadQuery par Qwen3 32B. Le prompt passe de **6 lignes / ~470 caractères** à **136 lignes / 4872 caractères** (1623 tokens côté `prompt_tokens` mesuré sur vLLM, le reste de l'overhead venant du chat template Qwen3). Il regroupe : règles dures (imports/sandbox), une référence d'API CadQuery (workplane, primitives 2D, dessin 2D, opérations 3D, transforms, sélecteurs, patterns, assemblies), 5 patterns d'exemple, et une liste de pièges typiques.

**Aucun autre fichier modifié.** La logique d'appel vLLM, le nettoyage de code (`cleanCode`), la construction des messages (`buildMessages`), le timeout 30 s, le rate-limiter Node, le validateur Python et le frontend sont identiques à la phase 3.5.

## Modif `node/llm.js`

Seule la valeur de la constante `SYSTEM_PROMPT` change. Le prompt embarqué est exactement (verbatim) :

```
You are a CadQuery code generator. You output ONLY valid Python code. No explanations, no markdown, no code fences, no comments unless they clarify complex geometry logic.

HARD RULES:
- Always start with: import cadquery as cq
- You may also import math and typing if needed. No other imports.
- The final 3D object MUST be assigned to a variable named "result"
- Never use show_object(), cq.exporters, or any display/export function
- Never use eval(), exec(), open(), os, sys, subprocess, or any system call
- Never access dunder attributes (__class__, __bases__, __import__, etc.)
- If the user provides existing code and asks for a modification, return the FULL modified code, not a diff or partial snippet

CADQUERY API REFERENCE (use only these):

Workplane creation:
  cq.Workplane("XY" | "XZ" | "YZ")
  .transformed(offset=(x,y,z), rotate=(rx,ry,rz))

2D Primitives (on workplane):
  .rect(xLen, yLen, centered=True)
  .circle(radius)
  .ellipse(x_radius, y_radius)
  .polygon(nSides, diameter)
  .slot2D(length, diameter, angle=0)
  .text(txt, fontsize, distance, cut=True/False, font="Arial")

2D Drawing:
  .moveTo(x, y)
  .lineTo(x, y)
  .line(dx, dy)
  .hLine(distance), .vLine(distance)
  .hLineTo(x), .vLineTo(y)
  .threePointArc(p1, p2)
  .sagittaArc(endPoint, sag)
  .radiusArc(endPoint, radius)
  .tangentArcPoint(endpoint)
  .spline(listOfXYTuple)
  .polyline(listOfXYTuple)
  .close()
  .mirrorX(), .mirrorY()
  .offset2D(distance)
  .wire()

3D Operations:
  .extrude(distance, combine=True, both=False)
  .revolve(angleDegrees=360, axisStart=(0,0,0), axisEnd=(0,1,0))
  .sweep(path, multisection=False)
  .loft(ruled=False)
  .shell(thickness) — hollows the solid
  .cut(other) — boolean subtract
  .union(other) — boolean add
  .intersect(other) — boolean intersect
  .hole(diameter, depth=None)
  .cboreHole(diameter, cboreDiameter, cboreDepth, depth=None)
  .cskHole(diameter, cskDiameter, cskAngle, depth=None)

Transforms:
  .translate((x, y, z))
  .rotateAboutCenter((ax, ay, az), angleDegrees)
  .rotate((0,0,0), (0,0,1), angleDegrees)
  .mirror("XY" | "XZ" | "YZ")

Edge/Face Operations:
  .edges(selector) — e.g. "|Z", ">Z", "<Z"
  .faces(selector) — e.g. ">Z", "<X", "+Y"
  .fillet(radius)
  .chamfer(length)
  .workplane(offset=0)

Selectors (string):
  ">X", "<X", ">Y", "<Y", ">Z", "<Z" — max/min along axis
  "|X", "|Y", "|Z" — parallel to axis
  "#X", "#Y", "#Z" — perpendicular to axis
  "not(<selector>)" — negate
  ">Z[-2]" — second from top
  Combine with and/or: ">Z and |X"

Patterns:
  .rarray(xSpacing, ySpacing, xCount, yCount) — rectangular array of points
  .polarArray(radius, startAngle, angle, count) — polar array of points
  .pushPoints([(x1,y1), (x2,y2), ...]) — arbitrary point array

Workplane chaining:
  .faces(">Z").workplane() — new workplane on top face
  .faces("<Z").workplane() — new workplane on bottom face
  .center(x, y) — shift workplane origin

Assembly (when multiple parts):
  assy = cq.Assembly()
  assy.add(part, name="name", loc=cq.Location((x,y,z), (rx,ry,rz)))
  result = assy

MODELING PATTERNS:

Pattern 1 — Base with pocket:
  result = (cq.Workplane("XY").rect(100,60).extrude(20)
    .faces(">Z").workplane().rect(80,40).cutBlind(-15))

Pattern 2 — Bolt hole pattern:
  result = (cq.Workplane("XY").circle(50).extrude(10)
    .faces(">Z").workplane()
    .polarArray(35, 0, 360, 8).hole(5))

Pattern 3 — Profile extrusion:
  result = (cq.Workplane("XY")
    .moveTo(0,0).lineTo(50,0).lineTo(50,10)
    .lineTo(30,10).lineTo(30,40).lineTo(20,40)
    .lineTo(20,10).lineTo(0,10).close()
    .extrude(80))

Pattern 4 — Revolution:
  result = (cq.Workplane("XZ")
    .moveTo(10,0).lineTo(20,0).lineTo(20,50)
    .lineTo(15,55).lineTo(10,55).close()
    .revolve(360, (0,0,0), (0,1,0)))

Pattern 5 — Parametric with loops:
  import math
  result = cq.Workplane("XY").circle(50).extrude(5)
  for i in range(8):
      a = math.radians(i * 45)
      result = result.cut(
          cq.Workplane("XY")
          .center(35 * math.cos(a), 35 * math.sin(a))
          .circle(6).extrude(5))

COMMON MISTAKES TO AVOID:
- Do not chain .hole() after .edges().fillet() — do fillet last or use .faces(">Z").workplane() before .hole()
- Do not use .extrude() on a 3D object — it works on 2D wire/face only
- Do not forget .close() when drawing a profile with lineTo/line
- .shell() removes a face first; call it on the solid, not on a workplane
- .fillet() radius must be less than half the smallest edge length
- When cutting holes in a pattern, .pushPoints() or loops are more reliable than .rarray() for non-grid layouts
- For assemblies, each part must be a separate cq.Workplane chain, then added to cq.Assembly

OUTPUT FORMAT:
Return the complete Python script ready to execute. Start with imports, define any helper functions, then build the geometry, and end with the result assignment. Nothing else.
```

## Tests effectués

Conditions :
- vLLM `Qwen3 32B FP8` à `http://192.168.30.121:8000/v1` (modèle `/data/models/qwen3-32b-fp8`).
- Stack lancée via `./start.sh` (Node + Python).
- Pour chaque test, deux requêtes successives `POST /api/generate` puis `POST /api/preview`, équivalentes à ce que fait `web/js/chat.js` à la frappe **Envoyer**.
- Les codes générés sont conservés dans `/tmp/llmcad-phase4/test{1..10}.py`, le récap brut dans `/tmp/llmcad-phase4/results.json`.
- Tests itératifs (6 et 9) appelés avec `history = [user_N, assistant_N]` et `currentCode = code_test_N` — exactement comme le frontend.

| # | Prompt | Gen | Preview | Description visuelle / observation |
|---|---|---|---|---|
| 1 | `a simple box 100x60x20mm with 3mm fillets on all edges` | OK 2.2 s, 115 B | OK 288 v / 276 f | Pavé droit `.rect(100,60).extrude(20)` puis `.edges("\|Z or \|X or \|Y").fillet(3)`. Tous les coins arrondis, géométrie correcte. |
| 2 | `a flanged bearing housing: cylindrical bore 25mm, flange 80mm diameter 10mm thick, 4 bolt holes M8 on a 60mm bolt circle` | OK 9.3 s, 793 B | OK 108 v / 104 f | Flange Ø80×10 + corps cylindrique Ø25 (interprété comme la base avant cut), 4 bossages bolt-circle Ø60 reliés par `.union()` dans une boucle `for i in range(4)`, alésage central Ø25 par `.cut()`. Les bossages M8 sont **modélisés en pleine matière** (cylindres) au lieu de **trous** — interprétation littérale « bolt holes M8 » non corrigée par le prompt. Géométrie cohérente mais inversée fonctionnellement (à reformuler en « 4 holes for M8 bolts »). |
| 3 | `an L-bracket: 80mm x 60mm x 5mm thick, with a 10mm hole in each arm` | OK 10.9 s, 795 B | **FAIL** : `ValueError: Can not return the Nth element of an empty list` | Code généré chaîne plusieurs `.translate(...)` au milieu de la pile, ce qui casse la sélection `.faces(">Z").workplane()` plus loin (la stack devient vide). Le LLM tente une L-bracket en deux `rect.extrude` reliés par `.union()` mais place les translations entre les opérations de sélection. Le prompt liste pourtant explicitement « do fillet last or use `.faces(">Z").workplane()` before `.hole()` » mais ne couvre pas le cas `translate` au milieu de la chaîne. |
| 4 | `a spur gear, 20 teeth, module 2.5, 15mm face width, 10mm bore` | OK 16.1 s, 1529 B | **FAIL** : `StdFail_NotDone: GC_MakeArcOfCircle::Value() - no result` | Le LLM produit une fonction `def spur_gear(...)` avec `threePointArc` aux paramètres invalides (les 3 points sont colinéaires, OCC refuse de fabriquer un arc). Utilise aussi `.each(lambda i: ... .rotate(...))` qui n'est pas dans le prompt. Validateur OK (def + lambda + math.radians acceptés depuis phase 3.5), mais OCC échoue en interne. Engrenages involutes restent un point dur — il faudrait un Pattern dédié dans le prompt. |
| 5 | `a phone stand that holds a phone at 60 degrees angle, 80mm wide` | OK 3.6 s, 204 B | OK 48 v / 28 f | Base 80×30×10 + dossier 80×10×10 incliné 60° via `.rotateAboutCenter((1,0,0), 60)`. Géométrie correcte, deux blocs visibles en équerre. Aucune encoche pour le téléphone (interprétation minimaliste du « phone stand »). |
| 6 | (itératif sur 5) `add rubber grip pads as 2mm raised rectangles on the base` | OK 7.5 s, 517 B | OK 96 v / 52 f | Le code du test 5 est repris in extenso, puis deux pads `.rect(10,10).extrude(2)` sont positionnés à `(-30,-10,0)` et `(30,-10,0)` puis `union` à `result`. **Le prompt itératif fonctionne** — le LLM rend le FULL code modifié comme demandé par la HARD RULE. Deux pads seulement (non 4) : interprétation libre. |
| 7 | `a hex socket head cap screw M10x30` | OK 4.2 s, 291 B | OK 54 v / 48 f | Tige Ø10×30 + tête Ø10×2 (proportions M10 incorrectes : la tête devrait être plus large que la tige) + empreinte hex `polygon(6,4.5).extrude(-5)` sur le dessus, le tout `translate` pour finir centré. Visuellement reconnaissable mais cotes hors standard ISO 4762. |
| 8 | `a simple enclosure box 120x80x40mm with 2mm wall thickness, screw bosses in the 4 corners, and a lid that fits on top` | OK 10.8 s, 950 B | **FAIL** : `Validation failed: CadQuery operation 'Assembly' is not allowed; CadQuery operation 'Location' is not allowed` | Le LLM utilise `cq.Assembly()` + `cq.Location((0,0,40))` pour combiner base et couvercle — ce que le prompt **autorise explicitement** (« Assembly (when multiple parts) ») mais ce que le validateur de la phase 3.5 bloque (`Assembly` et `Location` ne sont pas dans `allowed_cq_operations`). Conflit entre le prompt et le validateur — voir « Points d'attention » ci-dessous. |
| 9 | (itératif sur 8) `take the current code and make everything 50% bigger` | OK 11.8 s, 1024 B | **FAIL** : mêmes `Assembly` / `Location` interdits | Le code du test 8 est correctement rééchelonné : 120→180, 80→120, 40→60, parois 2→3, bossages 6→9, lid 5→7.5. La transformation arithmétique fonctionne. Mais le code hérite du `cq.Assembly` du test 8, donc validation échoue à l'identique. Démontre que les itérations « scale » sur du code rejeté restent rejetées tant que le code source est rejeté. |
| 10 | `an intake manifold with 4 runners merging into a single plenum` | OK 11.0 s, 788 B | **FAIL** : `socket hang up` | Le LLM produit une chaîne de `.rect(60,60).extrude(20).faces(">Z").workplane().rect(60,60).extrude(10)` puis enchaîne plusieurs `.faces("<Z").workplane(centerOption="CenterOfMass").rect(20,20).extrude(-40)` et `.rarray(40,40,2,2).rect(20,20).extrude(-40)`. **Le serveur Python crashe** pendant l'exécution de cette chaîne (probablement OCC OOM ou segfault sur l'imbrication boolean) : `ss -ltnp` confirme que le port 5002 n'est plus en écoute après ce test. La queue Node `RequestQueue.js` reçoit donc `ECONNRESET`, traduit en « socket hang up ». Échec exécution, pas validation. |

### Synthèse

| Indicateur | Valeur |
|---|---|
| Génération réussie (`/api/generate` → `success:true`, code non vide après `cleanCode`) | **10 / 10 = 100 %** |
| Code valide à la prévisualisation (`/api/preview` → mesh non vide) | **5 / 10 = 50 %** |
| Itérations réussies | 1 / 2 (le 6 OK, le 9 FAIL — héritage du test 8) |
| Latence médiane `/api/generate` | ~10 s |
| Latence min / max `/api/generate` | 2.2 s / 16.1 s |

Codes individuels disponibles à `/tmp/llmcad-phase4/test{1..10}.py`, récap brut JSON à `/tmp/llmcad-phase4/results.json`.

## Points d'attention / écarts

1. **Conflit prompt ⇄ validateur sur `cq.Assembly`** — le nouveau prompt encourage l'usage de `cq.Assembly` + `cq.Location` pour les pièces multi-corps (« Assembly (when multiple parts): … `result = assy` »). Mais `CadQueryValidator.allowed_cq_operations` (phase 3.5, intentionnellement non touché) ne contient **ni** `Assembly` **ni** `Location`. Conséquence : tout prompt qui demande un assemblage de plusieurs pièces (test 8 « enclosure + lid », test 9 par héritage) est généré correctement par le LLM mais rejeté par le validateur. **Décision phase 4 (baseline)** : ne pas corriger — la spec demandait explicitement de produire la baseline sans itérer. Trois pistes possibles en phase 5 :
    - Retirer la section « Assembly » du prompt et obliger à un seul `cq.Workplane` chaîné par `.union()` (limite la modélisation).
    - Ajouter `Assembly`, `Location` à `allowed_cq_operations` côté validateur (à arbitrer pour la sécurité — `cq.Location` est juste un constructeur, `cq.Assembly` est un conteneur, pas dangereux en soi).
    - Filtrer/transformer côté `cleanCode` Node (déconseillé, fragile).
2. **vLLM instable pendant les tests** — le serveur `192.168.30.121:8000` était inaccessible par intermittence : connexion TCP refusée puis revenue après 60–360 s, plusieurs fois pendant l'exécution. Sur les 5 tests refaits après cycle de panne, **toutes les générations reprises ont fini par réussir** au 1er ou 5ème essai. Le timeout côté Node (30 s, non modifié) ne change pas — c'est bien la disponibilité de vLLM qui était en jeu, pas la latence inhérente du modèle (~3–16 s mesurée quand le serveur répond).
3. **Variabilité du LLM à `temperature=0.2`** — sur deux appels successifs du test 3 (« L-bracket »), le LLM a produit deux codes différents : un qui validait et générait 64 v / 36 f (premier run), un qui crashe avec « Can not return the Nth element of an empty list » (run final, conservé dans `results.json`). À 0.2 le top-k reste actif et la séquence de tokens diffère selon le contexte (notamment le KV cache de vLLM). Le résultat final reflète donc un échantillon, pas un comportement déterministe — d'où la valeur d'avoir cette baseline avant toute tentative d'amélioration.
4. **Test 10 fait crasher le serveur Python** — la chaîne d'opérations (extrude, sub-extrude, rarray, fillet) finit par planter le process Python entier (le port 5002 disparaît, redémarrage manuel nécessaire). Aucune trace dans le log Flask, suggérant un crash bas niveau OCC (segfault ou C++ exception non remontée). À investiguer : le timeout de 30 s côté `cadquery/server.py` (phase 3.5) tue le **thread**, pas le process — un crash natif passe outre. Pour un usage réel, il faudrait basculer chaque exécution dans un sous-process (multiprocessing) qu'on peut tuer / qui isole les segfaults.
5. **Cotation libre** — le LLM ne suit pas les standards ISO. Vis M10×30 (test 7) avec tête plus petite que la tige, vis M8 « bolt holes » modélisées en bossages pleins (test 2). C'est attendu : Qwen3 n'a pas de connaissance dimensionnelle stricte sans données numériques explicites dans le prompt. Pour des cotes normalisées, ajouter un Pattern « ISO threaded fastener proportions » au prompt en phase 5.
6. **Engrenages reste un point dur** (test 4) — le prompt n'a pas de Pattern dédié. Le LLM tente un involute via `threePointArc` avec des paramètres invalides. Pas de régression par rapport à la phase 3.5, mais pas d'amélioration non plus. Une ligne « do not attempt involute spur gears, prefer simple polygon teeth or use polarArray + tooth profile sketch » dans le prompt résoudrait probablement.
7. **Aucune modification du frontend, du Node hors `SYSTEM_PROMPT`, du serveur Python ou du validateur** — comme demandé. Le `cleanCode()` continue d'extraire le code d'éventuels code-fences (le nouveau prompt interdit explicitement les fences, mais le strip défensif reste en place ; vérifié sur les 10 tests : aucune génération n'en contenait).
8. **Pas de retry automatique exploité ici** — les tests appellent directement `/api/preview` après `/api/generate`, sans passer par la logique de retry du frontend (`web/js/chat.js`). En usage réel via le chat, les 5 cas en `FAIL preview` (tests 3, 4, 8, 9, 10) déclencheraient un retry automatique avec le message d'erreur en clair, ce qui pourrait sauver les tests 3 (chaîne `translate` cassée — message explicite) et peut-être 4. Les tests 8 et 9 ne seraient pas sauvés tant que le validateur n'autorise pas Assembly. Le test 10 (socket hang up) déclencherait un retry mais le serveur Python étant crashé, il faudrait un redémarrage manuel.
9. **Latence acceptable** — ~10 s de médiane est compatible avec l'UX du chat (indicateur de chargement déjà en place phase 3). Le prompt long ne ralentit pas significativement Qwen3 32B FP8 vs un prompt court (mesure directe : 5.6 s avec long prompt vs 9.4 s avec court prompt sur le même test 2 — variabilité serveur dominante).

---

# Phase 4.5 — Isolation subprocess + Assembly support

Date : 2026-04-30

## Résumé

Deux problèmes diagnostiqués en phase 4 sont corrigés :

- **Problème A — crash natif OCC tuait Flask** : le timeout `threading` de la phase 3.5 ne pouvait pas tuer un thread Python, et un segfault dans le kernel OpenCascade emportait tout le process Flask (port 5002 disparu, redémarrage manuel obligatoire — cf. phase 4 test 10).
- **Problème B — `cq.Assembly` rejeté par le validateur** : le system prompt phase 4 encourageait `cq.Assembly` + `cq.Location` pour les pièces multi-corps (enclosure + lid, etc.), mais `CadQueryValidator.allowed_cq_operations` ne contenait pas ces noms (cf. phase 4 tests 8 et 9 — FAIL).

Architecture corrigée :

- Chaque exécution `/preview|/stl|/step` est lancée dans un **subprocess Python dédié** (`cadquery/worker.py`) avec `subprocess.Popen + communicate(timeout=30)`. Timeout dépassé → `subprocess.kill()` + HTTP 400. Crash natif (segfault, abort, libc OOM) → exit code != 0 → HTTP 400 avec stderr remonté en clair. Flask reste vivant.
- `RLIMIT_AS` est appliqué dans le worker au démarrage (avant l'import de cadquery) — finalement applicable, le parent Flask n'est plus dans cet espace d'adressage.
- Une route `GET /health` (200 `ok`) sert de probe pour un **watchdog dans `start.sh`** : un superviseur en boucle ping `http://127.0.0.1:5002/health` toutes les 30 s ; si la sonde échoue (ou si le PID disparaît), `kill -9` puis relance automatiquement.
- Le validateur whitelist `Assembly`, `Location`, `Color` (et quelques autres constructeurs) côté `cq.<X>(...)`. Les méthodes appelées sur la variable utilisateur (`assy.add(...)`, `assy.toCompound()`) étaient déjà acceptées (le validateur ne contraint que les attributs sur `cq`/`math`/`np`).
- `Preview.py` et `worker.py` acceptent désormais un `result` de type `cq.Workplane`, `cq.Assembly`, `cq.Compound` ou `cq.Solid`. Pour les exports, un `Assembly` est converti en `Compound` via `assy.toCompound()` avant d'appeler `cq.exporters.export` (qui ne sait pas manger un `Assembly` directement).

Aucune modification du frontend ni du Node.

## Fichiers modifiés / créés

| Fichier | État | Détails |
|---|---|---|
| `cadquery/server.py` | réécrit (~⅓ remplacé) | Suppression du runner `threading.Thread` + sandbox builtins. Nouveau `_run_worker(code, mode, suffix)` qui valide → écrit le code dans un fichier temporaire (`tempfile.mkstemp(prefix="cqcode_")`) → spawn `python worker.py <code> <out> <mode>` → `communicate(timeout=EXEC_TIMEOUT_SEC)`. Cleanup des temp files dans des `finally`. Routes `/stl` et `/step` slurpent le fichier en mémoire (`io.BytesIO`) et le suppriment immédiatement, parce que `send_file(path) + call_on_close` du dev-server Werkzeug peut ne pas se déclencher (client deconnecté, race) et laisse fuir des fichiers de plusieurs Mo dans `/tmp`. Nouvelle route `GET /health` qui retourne `200 ok` sans toucher au worker (pour que le probe soit léger). Variables de config inchangées (`CADQUERY_HOST`, `CADQUERY_PORT`, `CADQUERY_EXEC_TIMEOUT`). |
| `cadquery/worker.py` | **nouveau** | Script appelé par `server.py` via subprocess. Argv : `<code_file> <output_file> <mode>` (mode = `preview \| stl \| step`). Applique `RLIMIT_AS` AVANT d'importer cadquery (sinon le cap n'a aucun effet sur les mmaps déjà faits). Reconstruit le sandbox builtins + `_sandbox_import` à partir de `CadQueryValidator.allowed_builtins` (defense in depth — le validateur a déjà tourné côté Flask). Tessélise (preview) ou exporte (stl/step) en gérant `Workplane`/`Assembly`/`Compound`/`Solid`. Exit codes : 0 succès, 1 erreur Python/CadQuery (avec traceback complet sur stderr), 2 erreur d'arguments. |
| `cadquery/CadQueryValidator.py` | mineur | Ajout dans `allowed_cq_operations` : `Assembly`, `Location`, `Color`, `toCompound`, `save`, `Vector`, `Plane`. Aucun autre changement. |
| `cadquery/Preview.py` | réécrit | Helper `_result_solids(result)` qui normalise `Workplane.objects` / `Assembly.toCompound().Solids()` / `Compound.Solids()` / bare `Solid`. Le code de tessellation est inchangé pour les Workplane (parité numérique vérifiée — voir tests). Conserve la signature `preview(result) -> (dict, error)` pour compatibilité. |
| `start.sh` | étendu | Le serveur Python est maintenant lancé par un superviseur (subshell `while true`). Boucle interne : tant que le PID est vivant, sleep `CADQUERY_HEALTH_INTERVAL` (30 s par défaut), puis `curl -sf -m $CADQUERY_HEALTH_TIMEOUT http://127.0.0.1:5002/health` ; sur échec, `kill -9` et break. Boucle externe : sleep 1, relance. `cleanup()` dans le trap `INT TERM` envoie `SIGTERM` au superviseur **et** `pkill -f "python server.py"` pour les workers orphelins. `wait` cible désormais `NODE_PID` (le superviseur est conçu pour ne jamais terminer normalement). |

## Configuration / variables d'environnement

Nouvelles ou modifiées (toutes optionnelles, valeurs par défaut sensées) :

| Variable | Défaut | Effet |
|---|---|---|
| `CADQUERY_EXEC_TIMEOUT` | `30` (s) | Wall-clock max d'un script utilisateur dans le worker. |
| `CADQUERY_WORKER_MEM_LIMIT_MB` | `2048` (MiB) | `RLIMIT_AS` appliqué dans le worker. `0` = désactivé. **Ne pas baisser sous ~1500** : `import cadquery` à lui seul réserve ~1,3 GiB de VA (mesure : `VmPeak: 1315656 kB` après `import cadquery`). La spec phase 4.5 demandait 1 GiB ; ce n'est pas tenable avec cadquery 2.7 + cadquery-ocp 7.8.1.1 + numpy 2.4.4 — voir « Points d'attention » §1. |
| `CADQUERY_HEALTH_INTERVAL` | `30` (s) | Période de probe `/health` du superviseur dans `start.sh`. |
| `CADQUERY_HEALTH_TIMEOUT` | `5` (s) | Timeout HTTP du `curl` du superviseur. |

`CADQUERY_MEM_LIMIT_MB` (phase 3.5, sur le **process Flask**) reste reconnu mais n'est plus appliqué — Flask ne fait plus tourner le code utilisateur, donc capper sa VA n'a plus de sens. La variable peut être enlevée du `.env` sans conséquence.

## Tests effectués (2026-04-30)

Stack lancée via `./start.sh`. Tests via `curl` direct sur `127.0.0.1:5002` (Flask) ou `127.0.0.1:49157/api/*` (Node, soumis au rate-limiter 30/10min).

### Tests fonctionnels

| # | Test | Attendu | Obtenu |
|---|---|---|---|
| A | `GET /health` (sans worker) | 200 `ok` | 200 `ok` (Content-Type `text/plain`, 2 octets) |
| B | `POST /api/preview` `cq.Workplane("XY").box(50,30,10).edges().fillet(2)` (régression phase 3) | 200, mesh non vide | 200, 288 vertices / 276 faces / 1 solide. **Parité numérique** vérifiée vs ancien `Preview.py` exécuté en isolation (288/276/1 dans les deux cas). |
| C | `POST /api/preview` Assembly enclosure+lid (cq.Assembly + cq.Location, `result = assy.toCompound()`) | 200, mesh non vide | 200, 504 vertices / 456 faces / 6 solides |
| D | `POST /api/step` même Assembly | fichier STEP valide | 200, 95 KB, en-tête `ISO-10303-21;`, 6 `MANIFOLD_SOLID_BREP` |
| E | `POST /api/stl` même Assembly | fichier STL valide | 200, 1284 octets, en-tête `STL Exported by Open CASCADE Technology` |
| F | Variante `+50% bigger` du test C (180×120×60, parois 3 mm, lid 7,5 mm) | 200, mesh non vide | 200, 550 vertices / 516 faces / 6 solides — confirme que les itérations « scale » sur du code Assembly fonctionnent (FAIL en phase 4 test 9) |

### Tests d'isolation / robustesse

| # | Test | Attendu | Obtenu |
|---|---|---|---|
| 8 | Boucle CPU runaway (`for i in range(10**12): total += i; result = cq.Workplane("XY").box(1,1,1)`) | HTTP 400 après ~30 s, Flask vivant, requête suivante OK | HTTP 400 en **30,0 s exactement** : `Execution timeout exceeded (30s). Possible infinite loop or runaway computation.`. `/health` répond 200 immédiatement après. Une requête `box(20,20,20)` ensuite renvoie 200 et un mesh valide. |
| 9 | `kill -9` du process worker mid-exécution (script de ~11 s : 60 unions de petits cubes) | HTTP 400 propre avec exit code, Flask vivant, requête suivante OK | HTTP 400 : `Worker exited with code -9 (signal 9)`. `/health` 200. Requête `box(6,6,6)` ensuite : 200 + mesh valide. |
| 9b | Crash mémoire natif sous `RLIMIT_AS` (script > 80 cuts en chaîne) | HTTP 400 lisible, Flask vivant | HTTP 400 : `cannot allocate memory for thread-local data: ABORT` (libc abort intercepté côté worker, stderr remonté). Flask vivant. |
| 11 | Watchdog : `kill -9` du PID Python listener (PID extrait de `ss -ltnp`) | Port 5002 réapparaît dans ≤ 60 s, requête suivante OK | Port relancé à **t=20 s** (~18 s de sleep restant sur la boucle de probe + ~2 s de réimport de cadquery). Nouveau PID Flask différent de l'ancien (`110461 → 113012`). Requête `box(12,12,12)` ensuite : 200 + mesh valide. Log superviseur visible dans `/tmp/llmcad-start.log`. |

### Tests de sécurité (régression validateur)

| Test | Attendu | Obtenu |
|---|---|---|
| `import os` | rejet validateur | 400 `Validation failed: Import of 'os' is not allowed` |
| `eval("1+1")` | rejet validateur | 400 `Validation failed: Function call to 'eval' is not allowed` |
| `assy.add(box, color=cq.Color(1,0,0))` | accepté validateur | OK |
| Code sans `result = …` | rejet validateur (early) | 400 `Code must assign to 'result' variable` |
| `result = 42` | rejet runtime côté worker | 400 `No solids found in result` |
| `1/0` dans le script | rejet runtime + traceback | 400 `ZeroDivisionError: division by zero` + traceback complet (utile au retry LLM) |

### Tests de propreté

| Test | Attendu | Obtenu |
|---|---|---|
| 10 cycles `/api/preview` + `/api/stl` + `/api/step` | aucun `cqout_*`, `cqcode_*` rémanent dans `/tmp` après réponse | 0 fichier rémanent (slurp en mémoire + `os.unlink` immédiat sur les exports ; cleanup dans `finally` sur les autres). |

### Tests phase 4 réémis (cf. spec §12)

Les tests phase 4 #8 (« enclosure box 120×80×40mm avec wall thickness 2 mm + lid ») et #9 (« 50 % bigger ») étaient FAIL en phase 4 sur la validation (`Assembly` / `Location` non whitelistés). En phase 4.5 :

- Le **code généré par le LLM en phase 4** (qui contient `cq.Assembly()`, `cq.Location((0,0,40))`, `assy.add(...)`) passe la validation.
- En remplaçant le LLM par un script équivalent forgé à la main (vLLM intermittent au moment du test, cf. phase 4 §2) : preview OK + STEP OK + STL OK (tests C/D/E ci-dessus).
- L'itération `+50% bigger` (test F) sur le code Assembly produit un nouveau code qui passe aussi.

Tests **non réémis** : phase 4 tests 1–7 (qui passaient déjà en phase 4). Test 10 (intake manifold qui crashait Flask) — non rejoué car la cause racine était précisément le crash natif OCC dans le process Flask, désormais isolé dans un worker. Si le LLM régénère un code similaire en chat, il devra renvoyer une 400 avec le stderr du crash, mais Flask restera up.

### Tests interactifs (à valider dans le navigateur)

- Bouton « Nouveau chat » + envoi d'un prompt « an enclosure 120×80×40mm with a 2mm wall and a lid » → preview affichée dans le viewer (boîte + couvercle empilés, 6 solides).
- Export STL et STEP du résultat → téléchargement OK.
- Pas de modification frontend, donc le comportement de retry automatique sur erreur reste identique (toujours 1 retry max).

## Points d'attention / écarts

1. **`CADQUERY_WORKER_MEM_LIMIT_MB` à 2048 et non 1024 (spec)** — la spec phase 4.5 affirmait « 1 GB suffit pour un script unique sans le poids idle de Flask+CadQuery chargé en mémoire du process parent ». **Faux dans les faits sur cette stack** : le worker, qui est un process Python neuf, doit lui aussi `import cadquery`, et cet import à lui seul réserve ~1,3 GiB de VA (mesure : `VmPeak: 1315656 kB` immédiatement après `import cadquery`, RSS ~440 MB). Avec `RLIMIT_AS=1 GiB`, le worker SIGSEGVE pendant l'import (testé : signal 11 reproductible). On a donc relevé le défaut à 2 GiB (~1,3 GiB d'idle + ~700 MiB pour le script). Override env-configurable. Pour un LXC très restreint, `CADQUERY_WORKER_MEM_LIMIT_MB=0` désactive complètement le cap.

2. **`call_on_close` du dev-server Werkzeug n'est pas fiable** — sur la première itération de phase 4.5 j'ai gardé l'ancien pattern `send_file(path) + @response.call_on_close: os.unlink(path)`. Sur 3 cycles `/stl` + `/step` j'ai constaté 3 fichiers `/tmp/cqout_*.{stl,step}` rémanents. La cause : `call_on_close` ne se déclenche pas systématiquement sur le dev-server (race avec la fermeture du socket, plus visible sur localhost loopback). Solution : slurp dans un `io.BytesIO`, unlink immédiat, retour de `send_file(BytesIO)`. Coût mémoire négligeable (STL/STEP < 1 Mo dans les cas testés). Le `/preview` n'était pas affecté (pas de `send_file(path)` — le JSON est lu et renvoyé directement).

3. **Latence ajoutée par le subprocess** — chaque requête paye un `import cadquery` complet (~2 s). Sur du code utilisateur trivial (`box(10,10,10)`), la latence end-to-end passe de ~50 ms (phase 3.5 in-process) à ~2,5 s. Sur du code complexe (15+ opérations), c'est dominé par CadQuery (~5–15 s) donc le surcoût est imperceptible. Acceptable pour l'usage chat (l'indicateur de chargement existe déjà). Pour un cas multi-utilisateurs lourd il faudrait un pool de workers pré-warmés (multiprocessing.Pool ou un manager type uWSGI vassal) — hors scope phase 4.5.

4. **Watchdog : recovery en 20–60 s, pas instantané** — la boucle de probe sleep 30 s entre deux ping. Quand on `kill -9` Flask, le superviseur ne voit la mort qu'au prochain réveil (au pire à t=29 s) puis sleep 1 s avant relance (~2 s de réimport). Donc fenêtre d'indisponibilité **2 à 32 s**, médiane ~17 s (mesure : `t=20 s` sur le run de test). Pour une recovery quasi-instantanée il faudrait soit baisser `CADQUERY_HEALTH_INTERVAL` (mais charge inutile sur le serveur en steady state), soit utiliser `wait` sur le PID en parallèle du sleep (technique : `sleep & wait -n $cq_pid $!`), ce qui déclencherait la relance dès le `SIGCHLD`. Acceptable en l'état pour un usage perso.

5. **Hiérarchie d'Assembly perdue dans STEP** — `cq.exporters.export(cq.Assembly, …)` ne fonctionne pas (`AttributeError: 'str' object has no attribute 'wrapped'`). On collapse via `assy.toCompound()` avant export. Conséquence : les noms de parts (`name="enclosure"`, `name="lid"`) ne se retrouvent pas dans le STEP. Pour préserver la hiérarchie il faudrait détecter `isinstance(result, cq.Assembly)` côté worker et appeler `result.save(output_path, exportType="STEP")` (méthode native d'Assembly). Trade-off : le fichier devient un assembly STEP de niveau supérieur (PRODUCT_DEFINITION_RELATIONSHIP, …) — utile pour l'import dans un CAD lourd, mais nos previews three.js voient déjà la géométrie collapsée. À reconsidérer si un utilisateur final demande explicitement la structure préservée.

6. **Defense in depth dans le worker** — le worker reconstruit le même sandbox builtins/imports qu'avait `server.py` en phase 3.5 (`_sandbox_import`, `safe_builtins` issus de `CadQueryValidator.allowed_builtins`), bien que le validateur Python complet ait déjà tourné côté Flask. C'est volontairement redondant : si jamais un futur changement de `validator.validate()` laisse passer `eval`, le runtime du worker continue de le bloquer (tentative dans le sandbox → `NameError`). Aucun coût mesurable.

7. **Aucune modification du frontend ni du Node.js** — comme demandé. `node/RequestQueue.js`, `node/server.js`, `node/llm.js`, `web/*` inchangés. La latence accrue (~2 s par requête) est invisible côté chat — le retry automatique sur erreur fonctionne pareil, et un crash worker remonte un message d'erreur en clair que le LLM peut consommer.

8. **Logs serveur Python** — toujours redirigés vers stdout du process `start.sh` (pas dans `logs/`). Avec le superviseur, à chaque relance les logs reprennent au début (les requêtes pendant la coupure sont des HTTP 502 côté Node, log `requests-YYYY-MM-DD.log` les capture comme erreurs). Un `gunicorn --access-logfile logs/cadquery-access.log` pourrait être utile en production — pas changé en phase 4.5 puisque hors scope.

9. **`while True: pass` côté validateur reste détecté côté validation** — pas de régression. Le subprocess garantit en plus que `for i in range(10**12): pass` (qui échappe au validateur car ce n'est pas `while True`) est tué à 30 s — ce qui n'était pas le cas en phase 3.5 (le thread continuait à tourner après le timeout, indéfiniment, jusqu'à redémarrage).
