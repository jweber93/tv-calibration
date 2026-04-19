#!/bin/bash
# Uses devcontainer exec so we don't need to hardcode the container name
exec devcontainer exec --workspace-folder "$(cd "$(dirname "$0")/.." && pwd)" -- bash "$@"
