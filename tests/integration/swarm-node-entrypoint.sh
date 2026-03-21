#!/bin/bash
# Entrypoint for the Docker Swarm test node.
# Starts the Docker daemon, waits for it to be healthy, then starts sshd.
set -e

# Start Docker daemon in the background
dockerd &

# Wait until the Docker daemon is responsive
echo "Waiting for Docker daemon to be ready..."
until docker info >/dev/null 2>&1; do
    sleep 1
done
echo "Docker daemon is ready."

# Start SSH daemon in the foreground
exec /usr/sbin/sshd -D
