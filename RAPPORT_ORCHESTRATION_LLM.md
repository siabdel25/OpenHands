# Rapport d'expérience — Orchestration multi-modèles (cloud + local) avec le harnais OpenHands

> Instance : http://localhost:3000 | Période : 01–02 juillet 2026 | Conteneur : `openhands-app-`

---

## 1. Résumé exécutif

Objectif : router automatiquement chaque tâche vers le modèle LLM le plus adapté (confidentialité, complexité, coût) parmi un panel mixte cloud (OpenRouter) + local (Ollama), sans intervention manuelle dans l'UI.

Résultat : un script d'orchestration (`scripts/route_llm.py`) route la tâche selon des règles simples, active le profil LLM correspondant via l'API OpenHands, démarre la conversation, et retombe sur un profil de secours en cas d'échec. Testé de bout en bout sur un vrai dépôt (`siabdel25/OpenHands`) : le pipeline clone le repo, exécute l'agent avec le modèle choisi (`deepseek`), et produit un commit de code fonctionnel pour ~$0.045.

Enseignement principal : la configuration LLM réelle de cette instance ne vit **pas** dans `config.toml` (fichier non monté dans le conteneur) mais dans `/.openhands/settings.json`, sous forme de *profils* nommés gérés par API REST. Toute tentative d'orchestration doit passer par cette API, pas par le fichier de config du dépôt.

---

## 2. Point de départ : diagnostic Gemma3 local

Symptôme initial : Gemma3 (27b, via Ollama) répondait avec son prompt système par défaut ("je suis Gemma de Google DeepMind") au lieu de suivre le harnais agent OpenHands — comme s'il "ignorait" les outils et instructions fournis.

Deux corrections apportées dans `config.toml` (fichier du dépôt) :

| Paramètre | Avant | Après | Raison |
|---|---|---|---|
| `base_url` | `http://localhost:11434` | `http://host.docker.internal:11434` | Depuis le conteneur, `localhost` pointe vers lui-même, pas vers l'hôte où tourne Ollama |
| `max_input_tokens` | `8192` | `32768` | Le system prompt OpenHands + définitions d'outils (bash, editor, browsing, jupyter...) pouvaient dépasser 8k tokens et être tronqués, faisant perdre au modèle les instructions du harnais |

Après `docker compose down && up -d`, le modèle a bien répondu dans le rôle attendu de l'agent.

**Correction ultérieure du diagnostic** (voir §3) : ces deux corrections dans `config.toml` n'étaient en réalité pas la cause du fix, puisque ce fichier n'est jamais lu par le conteneur. Le vrai correctif se trouvait ailleurs dans la stack (Ollama écoutant sur l'interface réseau du conteneur, DNS OpenRouter). `config.toml` reste néanmoins pertinent comme documentation d'intention pour un déploiement futur qui le monterait réellement.

---

## 3. Découverte architecturale : `config.toml` n'est jamais lu

En creusant l'API de création de conversation (`POST /api/v1/app-conversations`), constat clé :

- `config.toml`, avec ses sections `[llm]`, `[llm.claude]`, `[llm.gemini]`, `[llm.gemma_local]`, n'est **pas monté dans le conteneur** `openhands-app-`.
- La configuration réelle vit dans `/.openhands/settings.json`, gérée via l'UI (Settings → LLM) ou l'API `/api/v1/settings/profiles/*`.
- Les profils réellement actifs sur cette instance, listés via `GET /api/v1/settings/profiles`, sont différents des noms imaginés depuis `config.toml` :

| Profil réel | Modèle | Type |
|---|---|---|
| `deepseek` | `openrouter/deepseek/deepseek-chat-v3-0324` | cloud, économique |
| `GML-5` | `openrouter/z-ai/glm-5.1` | cloud, raisonnement |
| `gemma4` | `openai/gemma4:e4b` via `http://host.docker.internal:11434/v1/` | local, confidentiel |
| `openrouter_z-ai_glm-4.7-flash` | `openrouter/z-ai/glm-4.7-flash` | cloud, secours |

Il n'existe **pas** de profil `claude` ou `gemini` sur cette instance malgré leur présence dans `config.toml` — ils n'ont jamais été créés côté `settings.json`.

**Leçon** : toujours vérifier l'état réel via `GET /api/v1/settings/profiles` avant de construire une logique d'orchestration sur la base du fichier de config du dépôt.

---

## 4. Conception de l'orchestrateur

### 4.1 Mécanismes API utilisés

| Endpoint | Rôle |
|---|---|
| `GET /api/v1/settings/profiles` | Lister les profils disponibles et le profil actif |
| `POST /api/v1/settings/profiles/{name}/activate` | Basculer le profil LLM actif |
| `POST /api/v1/app-conversations` | Démarrer une conversation (utilise le profil actif) |
| `POST /app-conversations/{id}/switch_profile` | Changer de modèle en cours de conversation |
| `GET /api/v1/app-conversations/search` | Interroger l'état d'exécution d'une conversation |
| `GET /api/v1/app-conversations/{id}/git/changes?path=...` | Lister les fichiers modifiés dans le sandbox |

### 4.2 Règles de routage (`scripts/route_llm.py`)

Appliquées dans l'ordre, la première qui matche gagne :

1. **Confidentialité** — mots-clés sensibles (`confidentiel`, `mot de passe`, `données client`, `pii`...) → profil `gemma4` (local, aucune donnée envoyée à OpenRouter).
2. **Complexité** — mots-clés (`architecture`, `refactoring`, `sécurité`, `migration`...) → profil `GML-5` (modèle plus capable pour du raisonnement).
3. **Défaut** — toute autre tâche → profil `deepseek` (le moins cher, suffisant pour du code courant).

En cas d'échec (429, timeout, connexion) sur le profil choisi, le script bascule automatiquement sur la chaîne de secours `["deepseek", "GML-5", "gemma4", "openrouter_z-ai_glm-4.7-flash"]`.

### 4.3 Usage

```bash
python3 scripts/route_llm.py --task "Refactorer le module d'authentification" --dry-run
# → Profil choisi (règle): GML-5

python3 scripts/route_llm.py --task "Corrige ce bug d'affichage" --repo org/repo --branch main
# → Conversation démarrée avec le profil 'deepseek': <conversation_id>
```

---

## 5. Test en conditions réelles

Tâche envoyée : *"Ajoute un test unitaire simple pour scripts/route_llm.py"*, sur le dépôt `siabdel25/OpenHands`, branche `main`.

| Étape | Résultat |
|---|---|
| Règle appliquée | `deepseek` (aucun mot-clé confidentiel/complexe) |
| Activation profil | OK via `/api/v1/settings/profiles/deepseek/activate` |
| Démarrage conversation | OK, sandbox `oh-agent-server-2NVMvxcLbpDMc7fKaSCD6K` créé, repo cloné dans `/workspace/project/OpenHands` |
| Exécution agent | Statut `finished`, coût **$0.045** |
| Fichier produit | `tests/unit/test_route_llm.py` — 3 tests unitaires (`test_confidential_keywords`, `test_complex_keywords`, `test_default_profile`), tous verts après relecture |
| Nettoyage manuel | Suppression d'un import `patch` inutilisé avant commit |

Le fichier généré a été récupéré depuis le sandbox (`docker exec ... cat`), relu, corrigé, puis commité (`589aa39`) et poussé sur le fork `siabdel25/OpenHands`.

---

## 6. Épisode 2 (02 juillet) — Rendre le modèle local réellement agentique

Suite du rapport : la validation approfondie de `gemma4` (et l'essai de `qwen2.5-coder:14b`) a révélé une chaîne de trois pannes indépendantes, toutes masquées les unes par les autres.

### 6.1 Cause racine n°1 : troncature de contexte Ollama (la vraie cause du comportement "chatbot")

Le prompt système OpenHands v1 (instructions agent + définitions d'outils + skills) pèse **~17,9k tokens** à vide, et **jusqu'à ~26k** avec un repo cloné. Or Ollama servait tous les modèles avec `n_ctx=4096` (défaut). Les logs montraient :

```
msg="truncating input prompt" limit=4096 prompt=26549 keep=4 new=4096
```

Le début du prompt (= tout le harnais, dont le schéma des outils) était coupé. Conséquences selon le modèle :
- **gemma4** retombait sur un comportement conversationnel Google DeepMind (plausible mais non agentique) ;
- **qwen2.5-coder:14b** recrachait des pseudo-appels d'outils en JSON texte (`{"name": "finish", ...}` dans le chat), par réflexe de son fine-tuning function-calling.

Les corrections `max_input_tokens`/`num_ctx` passées par le profil OpenHands (`litellm_extra_body.options`) sont **sans effet** : l'endpoint OpenAI-compatible d'Ollama (`/v1/chat/completions`) ignore le champ `options` (paramètre de l'API native `/api/chat` uniquement).

**Fix effectif** : variable d'environnement serveur, dans l'override systemd d'Ollama :
```
Environment="OLLAMA_CONTEXT_LENGTH=24576"
```
(16384 testé d'abord : encore insuffisant face aux 17,9k tokens du prompt à vide.)

### 6.2 Cause racine n°2 : CUDA cassé au niveau système (erreur 999)

Après un restart d'Ollama, plus aucune couche n'était déchargée sur GPU (`total_vram="0 B"`, génération CPU à plusieurs minutes par tour), alors que `nvidia-smi` fonctionnait. Diagnostic : `cuInit(0)` retournait **999 (CUDA_ERROR_UNKNOWN)** pour tout programme — état corrompu du module noyau `nvidia_uvm` (que `nvidia-smi` n'utilise pas, d'où le faux air de normalité).

**Fix** (sans reboot) :
```
sudo rmmod nvidia_uvm && sudo modprobe nvidia_uvm && sudo systemctl restart ollama
```
Résultat : `cuInit: 0`, 43/43 couches gemma4 sur GPU avec le contexte 24576.

### 6.3 Cause racine n°3 : fuite de sandboxes

Chaque conversation OpenHands crée un conteneur `oh-agent-server-*` qui **ne s'arrête jamais**. Accumulation constatée : 20 sandboxes actifs, RAM système à 27 Go/31 Go, load average 15,5 → interface web en "connection failed". Nettoyage par `docker stop` des sandboxes obsolètes (réversible). À surveiller régulièrement : `docker ps | grep agent-server | wc -l`.

### 6.4 Comparatif agentique local : gemma4 vs qwen2.5-coder:14b

Test isolé de function-calling natif (API Ollama directe, sans harnais, donc sans troncature) :

| Modèle | `tool_calls` structuré | Verdict |
|---|---|---|
| `gemma4:e4b` (8B) | ✅ vrai champ `tool_calls` | Agentique natif — la réputation médiatique est méritée |
| `qwen2.5-coder:14b` | ❌ JSON en texte dans `content` | Template de chat Ollama sans transformation tool-call ; inutilisable tel quel comme agent |

`qwen2.5-coder:14b` est par ailleurs trop lourd pour la RTX 3060 12 Go une fois le KV-cache 24k ajouté (offload CPU, 4-5 min/tour). **gemma4 reste le modèle local agentique retenu.**

### 6.5 Validation finale de bout en bout

Tâche : *"Tâche confidentielle : liste les fichiers du répertoire de travail avec l'outil bash et dis-moi combien il y en a."*

- Routage automatique → `gemma4` (mot-clé confidentiel), coût **$0.00**
- Vrai `TerminalAction` (`ls -a`) exécuté dans le sandbox, observation renvoyée au modèle
- Réponse finale exacte (3 entrées, `.git` expliqué)
- **1 min 03 au total** (sandbox + 2 tours LLM sur GPU), contre 15+ min de timeouts avant les fixes

### 6.6 Pattern maître/esclave (orchestration par Claude Code)

Objectif d'usage : Claude Code = maître d'œuvre ; OpenHands + modèles = exécutants (doc, résumés, code de masse). Le flag `--wait` ajouté à `route_llm.py` boucle la chaîne : il résout l'id de start-task en id de conversation (piège : `POST /app-conversations` retourne un id de *tâche de démarrage*, pas l'id final), attend la fin d'exécution, puis récupère les events via l'API de l'agent-server (`{conversation_url}/events/search` + header `X-Session-API-Key`) et imprime la réponse finale + le coût :

```bash
python3 scripts/route_llm.py --task "Rédige la doc du module X" --wait
```

Répartition naturelle des rôles : `gemma4` = confidentiel/offline (gratuit), `deepseek`/`GML-5` = volume pas cher (~$0.005-0.05/tâche), le profil `claude` OpenRouter devient peu utile quand Claude Code est déjà le maître.

---

## 7. Points d'attention pour la suite

- **Aucune authentification API sur cette instance** (mono-utilisateur local) : le script n'implémente pas de gestion de clé/session. À revoir avant tout déploiement multi-utilisateur ou exposé au réseau.
- **Récupération du diff** : l'endpoint `GET /{conversation_id}/git/diff` a renvoyé une erreur 400 sur un fichier nouvellement ajouté ; `git/changes` fonctionne mais nécessite le chemin absolu réel du repo dans le sandbox (`/workspace/project/<repo>`, pas `/workspace`).
- **Pas de garde-fou coût** : `max_budget_per_task` existe dans les settings mais n'est pas exploité par `route_llm.py`.
- **Fuite de sandboxes** (§6.3) : prévoir un nettoyage périodique des `oh-agent-server-*` arrêtables.
- **Matériel** : sur RTX 3060 12 Go, le harnais complet (~18-26k tokens) impose un modèle ≤8-9 Go de poids pour rester sur GPU avec le KV-cache 24k. Un harnais allégé (moins d'outils/skills) réduirait le prompt et ouvrirait la porte à des modèles plus gros ou des contextes plus longs.
- **`OLLAMA_KEEP_ALIVE`** par défaut à 5 min : le premier tour après une pause repaye le chargement du modèle (~2-3 min sur CPU, quelques secondes sur GPU). À allonger si gemma4 devient l'esclave principal.

---

## 8. Fichiers livrés

- `scripts/route_llm.py` — orchestrateur de routage + délégation `--wait` (commits `096e577`, `64b5a49`, `5cf7502`)
- `tests/unit/test_route_llm.py` — tests unitaires de la logique de routage (commit `589aa39`)
- Config serveur : `OLLAMA_CONTEXT_LENGTH=24576` dans l'override systemd Ollama (hors repo)
- Profils OpenHands créés : `claude`, `gemini`, `qwen_local` (dans `/.openhands/settings.json`, hors repo)
- Fork de référence : https://github.com/siabdel25/OpenHands (branche `main`)
