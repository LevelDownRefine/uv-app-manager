"""Bundle the uv executable used by source-app-manager."""

from uv import find_uv_bin

binaries = [(find_uv_bin(), ".")]
