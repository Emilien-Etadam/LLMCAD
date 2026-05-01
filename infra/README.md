# Déploiement LLMCAD sur Proxmox (LXC)

Ce dossier contient un script exécutable **sur un nœud Proxmox VE** pour créer un conteneur LXC Debian, y installer le dépôt [LLMCAD](https://github.com/Emilien-Etadam/LLMCAD), configurer **nvm** (Node.js LTS en espace utilisateur), les environnements Python (`cadquery/venv`, optionnellement `rag/.venv`), un service **systemd** `llmcad.service`, puis vérifier la connectivité vers **Qdrant**, **TEI** et **vLLM** sur le réseau `192.168.30.0/24`.

## Prérequis sur le nœud Proxmox

- Accès **root** (ou `sudo`) sur le nœud où le conteneur sera créé.
- Un **pont** `vmbr0` joignant le conteneur au réseau où se trouvent les services distants (routage / firewall assurés par votre infra).
- Le stockage configuré sous le nom **`sas600`** (type attendu : LVM-thin, ZFS, ou autre stockage compatible conteneurs ; le script vérifie sa présence via `pvesm status`).
- Le template **Debian 13** téléchargé sur le stockage **local** (exemple) :

  ```bash
  pveam update
  pveam available | grep debian-13-standard
  pveam download local debian-13-standard_13.1-1_amd64.tar.zst
  ```

  Le nom par défaut du template dans le script est :

  `local:vztmpl/debian-13-standard_13.1-1_amd64.tar.zst`

  Adaptez `CT_TEMPLATE` si le nom de fichier ou la version diffère sur votre nœud.

- Sortie Internet (HTTPS) depuis le nœud et depuis le conteneur pour **git**, **curl**, **apt**, et le script d’installation **nvm**.

## Utilisation

1. Copier ce dépôt (ou au minimum le dossier `infra/`) sur le nœud Proxmox, ou cloner le dépôt GitHub sur la machine hôte.
2. Rendre le script exécutable si besoin : `chmod +x infra/create-llmcad-lxc.sh`.
3. Lancer en **root** :

   ```bash
   ./infra/create-llmcad-lxc.sh
   ```

### Écraser un conteneur existant sans invite

Si `CT_ID` existe déjà, le script demande confirmation avant destruction. Pour l’automatisation (CI, Ansible) :

```bash
LLMCAD_DESTROY_EXISTING=1 CT_ID=210 ./infra/create-llmcad-lxc.sh
```

### Exemples de surcharge

```bash
CT_RAM=8192 CT_CORES=8 CT_DISK=40 \
VLLM_MODEL=/data/models/mon-modele \
./infra/create-llmcad-lxc.sh
```

```bash
GITHUB_REPO=https://github.com/MonOrg/LLMCAD.git \
CT_HOSTNAME=llmcad-dev \
./infra/create-llmcad-lxc.sh
```

## Paramètres configurables (variables d’environnement)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `CT_ID` | *(vide → premier ID libre via `pvesh get /cluster/nextid`)* | Identifiant du conteneur sur Proxmox. |
| `CT_HOSTNAME` | `llmcad` | Nom d’hôte du CT. |
| `CT_DISK` | `20` | Taille du rootfs en Go sur `CT_STORAGE`. |
| `CT_RAM` | `4096` | Mémoire (Mo). |
| `CT_CORES` | `4` | Nombre de vCPU. |
| `CT_STORAGE` | `sas600` | Identifiant du stockage pour le disque racine du CT. |
| `CT_TEMPLATE` | `local:vztmpl/debian-13-standard_13.1-1_amd64.tar.zst` | Référence du template (storage `local`, format vztmpl) pour `pct create`. |
| `GITHUB_REPO` | `https://github.com/Emilien-Etadam/LLMCAD.git` | URL Git du projet à cloner. |
| `QDRANT_URL` | `http://192.168.30.127:6333` | URL de base Qdrant (utilisée pour `rag/.env` si présent). |
| `TEI_URL` | `http://192.168.30.121:8080` | URL TEI (idem). |
| `VLLM_URL` | `http://192.168.30.121:8000/v1` | Point d’accès OpenAI-compatible ; patché dans `.env`. |
| `VLLM_BASE_URL` | *(identique à `VLLM_URL` par défaut)* | Valeur pour `VLLM_BASE_URL` dans `.env`. |
| `VLLM_MODEL` | `/data/models/qwen3-32b-fp8` | Modèle exposé par vLLM ; patché dans `.env`. |
| `APP_USER` | `llmcad` | Utilisateur non-root propriétaire du dépôt et du service. |
| `CT_ROOT_PASSWORD` | *(vide → mot de passe aléatoire, non affiché)* | Mot de passe root du CT (`pct create --password`). Connexion console : préférer `pct enter` depuis l’hôte. |
| `LLMCAD_DESTROY_EXISTING` | `0` | Si `1`, détruit un CT existant avec le même `CT_ID` sans prompt. |
| `NVM_VERSION` | `v0.40.1` | Tag du dépôt [nvm-sh/nvm](https://github.com/nvm-sh/nvm) pour le script d’installation. |

## Comportement du script (résumé)

- Vérifie le stockage, le template, puis crée un CT **non privilégié** avec **`nesting=1`**, réseau **`eth0`** sur **`vmbr0`** en **DHCP**, **`onboot=1`**, rootfs sur **`CT_STORAGE:CT_DISK`**.
- Attend que `pct exec` réponde puis qu’une **IPv4 DHCP** soit visible (`hostname -I` ou adresse sur `eth0`).
- Met à jour le système, installe les paquets listés (dont **`sudo`** pour les étapes `sudo -u`), crée l’utilisateur applicatif, clone le dépôt, installe **nvm + Node LTS**, **`cadquery/venv`**, **`npm install`** dans `node/`, copie **`.env.example` → `.env`** et applique les valeurs **VLLM**.
- Si **`rag/requirements.txt`** existe : crée **`rag/.venv`**, installe les dépendances, écrit **`rag/.env`** (`QDRANT_URL`, `TEI_URL`, `QDRANT_COLLECTION=build123d_docs`).
- Installe et démarre **`llmcad.service`** (`ExecStart=/home/$APP_USER/LLMCAD/start.sh`).
- Teste depuis le CT : **`GET`** `${QDRANT_URL}/collections`, **`GET`** `${TEI_URL}/health`, **`GET`** `${VLLM_URL}/models` (URLs normalisées défense en profondeur sur les `/` finaux).

## Après exécution

- Interface web (par défaut Node écoute sur le port **49157**) : `http://<IP_DHCP>:49157`
- Entrer dans le CT : `pct enter <CT_ID>`
- Journaux du service **dans le CT** : `journalctl -u llmcad -f`

Les tests `curl` vers les services distants peuvent afficher **FAIL** si le pare-feu, le routage ou les chemins d’API (`/health`, `/collections`, `/v1/models`) ne correspondent pas à votre déploiement ; ajustez les URLs ou les règles réseau en conséquence.
