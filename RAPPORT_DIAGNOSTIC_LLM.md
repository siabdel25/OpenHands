# Rapport de diagnostic — Chat LLM sans réponse & 404 /automations

> Instance : http://localhost:3000 | Date : 01 juillet 2026 | Conteneur : `openhands-app-`

---

## 1. Résumé exécutif

Deux problèmes distincts remontés par l'utilisateur :

| # | Symptôme | Cause racine | Sévérité |
|---|----------|--------------|----------|
| 1 | 404 au chargement de `/automations` | Route frontend non implémentée dans cette version, exposée par un feature flag activé par défaut | Corrigé |
| 2 | Aucun modèle LLM ne répond en chat (Gemma4 local, GLM-5 via OpenRouter) | (a) Ollama n'écoute que sur `127.0.0.1` → inaccessible depuis les conteneurs Docker. (b) Pare-feu UFW (policy DROP par défaut sur INPUT) bloquait le trafic entrant depuis les sous-réseaux Docker (`172.17.0.0/16` et `172.20.0.0/16`) vers le port 11434, même après le fix du bind. (c) Panne DNS intermittente vers `openrouter.ai` depuis les conteneurs sandbox | (a) Corrigé. (b) Corrigé. (c) Transitoire, à surveiller |

Aucun des trois problèmes de chat n'est lié à la configuration `config.toml` du dépôt : celui-ci **n'est pas monté dans le conteneur** et n'est donc jamais lu. La configuration réelle vit dans `~/.openhands/settings.json`, gérée depuis l'UI OpenHands (Settings → LLM).

---

## 2. Diagnostic détaillé

### 2.1 — 404 sur `/automations`

**Constat :**
- `frontend/src/components/features/sidebar/sidebar.tsx:91` affiche un bouton "Automations" conditionné par `config?.feature_flags?.enable_automations`.
- `frontend/src/components/shared/buttons/automations-button.tsx:21` pointe en dur vers `href="/automations"`.
- `frontend/src/routes.ts` ne contient **aucune route** `/automations` — la page n'a jamais été implémentée côté frontend dans ce commit (`b897ce4`, 2026-06-28).
- Le flag est activé par défaut : `enable_automations: bool = True` (`openhands/app_server/web_client/web_client_models.py:39`), overridable via `ENABLE_AUTOMATIONS` (`default_web_client_config_injector.py:187`).

**Conclusion :** fonctionnalité en cours de développement en amont, exposée trop tôt par le feature flag. Pas un bug de configuration côté utilisateur.

**Correctif appliqué :** ajout de `ENABLE_AUTOMATIONS=false` dans `docker-compose.yml`, conteneur recréé (`docker compose up -d`). Le bouton "Automations" disparaît de la sidebar → plus de clic possible vers une page inexistante.

---

### 2.2 — Aucune réponse du chat (Gemma4 & GLM-5)

**Point clé de l'architecture :** l'exécution réelle des appels LLM ne se fait **pas** dans le conteneur `openhands-app-`, mais dans des conteneurs sandbox éphémères `oh-agent-server-*` (un par conversation), visibles via `docker ps` (image `ghcr.io/openhands/agent-server:1.29.0-python`). C'est dans **leurs** logs qu'apparaissent les vraies erreurs LLM, pas dans les logs du conteneur principal.

#### a) Gemma4 (local, via Ollama)

Profil actif dans `settings.json` :
```json
"gemma4": {
  "model": "openai/gemma4:e4b",
  "base_url": "http://localhost:11434/v1/"
}
```

Erreur observée dans les logs du sandbox (`docker logs oh-agent-server-<id>`) :
```
ConnectionRefusedError: [Errno 111] Connect call failed ('127.0.0.1', 11434)
httpx.ConnectError: Cannot connect to host localhost:11434
```

**Cause :** `localhost` / `127.0.0.1` dans un conteneur Docker pointe vers le conteneur lui-même, jamais vers l'hôte. Ollama tourne sur l'hôte, pas dans le conteneur.

Vérification que le service Ollama n'écoute que sur loopback :
```
$ ss -tlnp | grep 11434
LISTEN  127.0.0.1:11434   users:(("ollama",pid=3770))
```

Donc même en remplaçant `localhost` par `host.docker.internal` (qui route vers l'IP hôte du pont Docker), la connexion échouerait quand même, car Ollama refuse les connexions non-loopback. **Il faut changer les deux : le bind d'Ollama ET l'URL côté OpenHands.**

#### a-bis) Pare-feu UFW — second blocage découvert après le fix du bind

Même après avoir mis Ollama en écoute sur `0.0.0.0`, les appels depuis les conteneurs restaient bloqués — mais avec un **timeout** (pas un "connection refused"), signe d'un blocage réseau/pare-feu plutôt qu'applicatif.

Diagnostic :
```bash
sudo iptables -L INPUT -n -v | head -5
# Chain INPUT (policy DROP ...)
```
UFW était actif avec une politique `DROP` par défaut sur `INPUT`, sans règle explicite autorisant les sous-réseaux Docker. Deux réseaux distincts sont concernés :
- `172.17.0.0/16` — réseau bridge Docker par défaut, où tournent les conteneurs sandbox `oh-agent-server-*` (c'est **ici** que s'exécutent réellement les appels LLM)
- `172.20.0.0/16` — réseau créé par Compose (`openhandharnai_default`), où tourne le conteneur principal `openhands-app-`

**Correctif :** voir procédure détaillée §3.1 Étape 5. Vérifié après application : `docker exec openhands-app- curl ... http://host.docker.internal:11434/api/tags` → `HTTP 200` depuis les deux réseaux.

#### b) GLM-5 (via OpenRouter)

Erreur observée (ponctuelle, à 11:38) :
```
socket.gaierror: [Errno -3] Temporary failure in name resolution
aiohttp.client_exceptions.ClientConnectorDNSError: Cannot connect to host openrouter.ai:443
```

Tests de validation effectués (tous positifs) :
- Clé API OpenRouter valide (`GET /api/v1/models` → 200)
- Modèle `z-ai/glm-5.1` existe bien côté OpenRouter
- Appel `chat/completions` direct avec cette clé et ce modèle → réponse correcte
- Re-test de résolution DNS de `openrouter.ai` depuis le conteneur sandbox quelques minutes plus tard → **succès**

**Conclusion :** panne DNS **intermittente**, côté résolveur (`192.168.1.1`, probablement la box/routeur du réseau local) ou côté DNS embarqué Docker sur le réseau bridge par défaut. Ce n'est pas un problème de configuration OpenHands — clé, modèle et endpoint sont corrects.

---

## 3. Procédure de correction complète

### 3.1 — Rendre Ollama accessible depuis Docker

**Étape 1 : configurer Ollama pour écouter sur toutes les interfaces**

Éditer le override systemd existant :
```bash
sudo tee -a /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
Environment="OLLAMA_HOST=0.0.0.0"
EOF
```

**Étape 2 : recharger et redémarrer le service**
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

**Étape 3 : vérifier que le bind a changé**
```bash
ss -tlnp | grep 11434
# Attendu : 0.0.0.0:11434 au lieu de 127.0.0.1:11434
```

**Étape 4 : vérifier l'accès depuis un conteneur**
```bash
docker exec openhands-app- curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  http://host.docker.internal:11434/api/tags
# Attendu : HTTP 200
```

⚠️ **Sécurité :** `OLLAMA_HOST=0.0.0.0` expose l'API Ollama à toutes les interfaces réseau de la machine, pas seulement à Docker. Restreindre l'accès via le pare-feu aux seules plages Docker (voir Étape 5) plutôt que de laisser le port ouvert sans filtrage.

**Étape 5 : autoriser le trafic Docker → hôte dans le pare-feu (UFW)**

Même avec Ollama en écoute sur `0.0.0.0`, la connexion depuis un conteneur restera bloquée (timeout, pas "connection refused") si UFW a une politique `DROP` par défaut sur `INPUT`. Vérifier :
```bash
sudo iptables -L INPUT -n -v | head -5
# Chain INPUT (policy DROP ...) → confirme le blocage
```
Autoriser explicitement les deux sous-réseaux Docker concernés (sandbox + app principale) :
```bash
sudo ufw allow from 172.17.0.0/16 to any port 11434   # réseau bridge par défaut (conteneurs oh-agent-server-*)
sudo ufw allow from 172.20.0.0/16 to any port 11434   # réseau Compose openhandharnai_default (openhands-app-)
```
Adapter les sous-réseaux à ceux réellement utilisés sur votre machine (`docker network inspect <réseau> | grep Subnet`).

**Étape 6 : mettre à jour le profil LLM dans OpenHands**

Dans l'UI (Settings → LLM → profil `gemma4`), remplacer :
```
base_url: http://localhost:11434/v1/
```
par :
```
base_url: http://host.docker.internal:11434/v1/
```

Ou directement dans `~/.openhands/settings.json` (section `llm_profiles.profiles.gemma4.base_url`), puis relancer une conversation pour qu'un nouveau conteneur sandbox soit provisionné avec la config à jour.

**Étape 7 : test de validation**

Démarrer une nouvelle conversation avec le profil `gemma4` actif et envoyer un message simple ("bonjour"). Vérifier dans les logs du sandbox nouvellement créé :
```bash
docker ps | grep agent-server   # repérer le conteneur le plus récent
docker logs <container_id> --since 2m | grep -iE "error|gemma"
```
Aucune erreur `ConnectionRefusedError` ne doit apparaître.

---

### 3.2 — Fiabiliser l'accès OpenRouter (GLM-5 et autres modèles cloud)

Le problème étant intermittent (DNS), il n'y a pas de "bug" à corriger dans la config, mais deux leviers pour réduire le risque de récidive :

**Option A — Forcer un DNS plus fiable pour Docker**

Éditer `/etc/docker/daemon.json` :
```json
{
  "dns": ["1.1.1.1", "8.8.8.8"]
}
```
Puis :
```bash
sudo systemctl restart docker
```
⚠️ Redémarrer Docker relance tous les conteneurs — à faire hors session active.

**Option B — Vérifier la stabilité du résolveur local**

Le résolveur observé dans les conteneurs sandbox était `192.168.1.1` (probablement la box internet). Si les coupures DNS se reproduisent, tester :
```bash
for i in {1..20}; do dig +short openrouter.ai @192.168.1.1; sleep 3; done
```
Des résultats vides intermittents confirmeraient une instabilité du résolveur réseau local, indépendante d'OpenHands.

**Option C — Compter sur le retry automatique**

OpenHands/litellm retente automatiquement les échecs de connexion (`retry_multiplier`, `num_retries: 5` déjà configurés dans les profils). Une panne DNS de quelques secondes est normalement absorbée par ce mécanisme — seule une coupure prolongée casse la conversation.

---

## 4. Vérifications post-correctif

| Test | Commande | Résultat attendu | Statut |
|------|----------|-------------------|--------|
| Ollama bind correct | `ss -tlnp \| grep 11434` | `0.0.0.0:11434` | ✅ Confirmé |
| UFW autorise le port 11434 | `sudo ufw status \| grep 11434` | Règles `ALLOW` pour `172.17.0.0/16` et `172.20.0.0/16` | ✅ Confirmé |
| Ollama joignable depuis le conteneur sandbox | `docker exec oh-agent-server-<id> curl -so /dev/null -w '%{http_code}' http://host.docker.internal:11434/api/tags` | `200` | ✅ Confirmé |
| Ollama joignable depuis le conteneur principal | `docker exec openhands-app- curl -so /dev/null -w '%{http_code}' http://host.docker.internal:11434/api/tags` | `200` | ✅ Confirmé |
| base_url gemma4 à jour | `docker exec openhands-app- cat /.openhands/settings.json \| grep base_url` | `http://host.docker.internal:11434/v1/` | ✅ Confirmé |
| Chat Gemma4 fonctionnel | Nouvelle conversation + message test dans l'UI | Réponse reçue, pas d'erreur dans les logs sandbox | ⏳ À valider par l'utilisateur |
| Chat GLM-5 fonctionnel | Nouvelle conversation + message test dans l'UI | Réponse reçue | ⏳ À valider par l'utilisateur |
| Page /automations | Recharger `http://localhost:3000` | Bouton absent de la sidebar (flag désactivé) | ✅ Confirmé |

---

## 5. Annexe — commandes de diagnostic utilisées

```bash
# Identifier le conteneur principal et les sandboxes
docker compose ps
docker ps -a | grep agent-server

# Config LLM réelle (pas config.toml, non monté)
docker exec openhands-app- cat /.openhands/settings.json

# Logs d'erreur réels (dans le sandbox, pas le conteneur principal)
docker logs <oh-agent-server-id> --since 30m | grep -iE "error|exception"

# Test direct API OpenRouter (hors OpenHands, pour isoler la cause)
curl -H "Authorization: Bearer <clé>" https://openrouter.ai/api/v1/models
curl -X POST https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer <clé>" -H "Content-Type: application/json" \
  -d '{"model":"z-ai/glm-5.1","messages":[{"role":"user","content":"say hi"}]}'

# Vérifier le bind réseau d'Ollama sur l'hôte
ss -tlnp | grep 11434

# Diagnostiquer un blocage pare-feu (policy DROP = trafic entrant bloqué)
sudo iptables -L INPUT -n -v | head -10
sudo ufw status verbose

# Identifier les sous-réseaux Docker à autoriser
docker network inspect bridge | grep Subnet
docker network inspect openhandharnai_default | grep Subnet

# Autoriser un sous-réseau Docker vers le port d'Ollama
sudo ufw allow from <subnet> to any port 11434
```
