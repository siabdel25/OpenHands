#!/usr/bin/env bash
# Déplace le stockage Docker + containerd de / vers /data/ollama/docker-root
# Usage :
#   sudo ./script_docker_mv.sh           # migration complète
#   sudo ./script_docker_mv.sh --purge   # supprime les anciennes données (après vérification)
set -euo pipefail

DEST=/data/ollama/docker-root
OLD_CONTAINERD=/var/lib/containerd
OLD_DOCKER=/var/lib/docker

[ "$(id -u)" -eq 0 ] || { echo "Lancer avec sudo" >&2; exit 1; }

# --- purge des anciennes données (2e passe, une fois que tout est vérifié) ---
if [ "${1:-}" = "--purge" ]; then
    root_dir=$(docker info --format '{{.DockerRootDir}}')
    [ "$root_dir" = "$DEST/docker" ] || { echo "Docker n'utilise pas encore $DEST/docker (actuel: $root_dir) — purge annulée" >&2; exit 1; }
    docker ps >/dev/null || { echo "Docker ne répond pas — purge annulée" >&2; exit 1; }
    rm -rf "$OLD_CONTAINERD" "$OLD_DOCKER"
    echo "Anciennes données supprimées."
    df -h /
    exit 0
fi

# --- garde-fous ---
mountpoint -q /data/ollama || { echo "/data/ollama n'est pas un point de montage — abandon" >&2; exit 1; }
[ -d "$DEST/containerd" ] && { echo "$DEST existe déjà — migration déjà faite ?" >&2; exit 1; }

echo "==> Arrêt des conteneurs et services"
docker ps -q | xargs -r docker stop
systemctl stop docker docker.socket containerd

echo "==> Copie vers $DEST (peut prendre plusieurs minutes)"
mkdir -p "$DEST"
rsync -aHAX "$OLD_CONTAINERD" "$OLD_DOCKER" "$DEST/"

echo "==> Configuration containerd"
cp /etc/containerd/config.toml /etc/containerd/config.toml.bak
sed -i "s|^#root = .*|root = \"$DEST/containerd\"|" /etc/containerd/config.toml
grep -q "^root = \"$DEST/containerd\"" /etc/containerd/config.toml \
    || { echo "Échec de la modification de config.toml — abandon" >&2; exit 1; }

echo "==> Configuration docker (daemon.json)"
if [ -f /etc/docker/daemon.json ]; then
    echo "/etc/docker/daemon.json existe déjà — fusionner à la main : \"data-root\": \"$DEST/docker\"" >&2
    exit 1
fi
cat > /etc/docker/daemon.json <<EOF
{
  "data-root": "$DEST/docker",
  "features": { "containerd-snapshotter": true }
}
EOF

echo "==> Redémarrage"
systemctl start containerd docker

echo "==> Vérification"
docker info --format 'Docker Root Dir: {{.DockerRootDir}}'
docker images | head -5
docker ps

cat <<EOF

Migration OK. Les anciennes données sont toujours dans $OLD_CONTAINERD et $OLD_DOCKER.
1. Relance tes stacks (docker compose up -d) et vérifie que tout fonctionne.
2. Puis récupère l'espace : sudo $0 --purge
EOF
