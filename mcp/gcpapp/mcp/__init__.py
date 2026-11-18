from __future__ import annotations
from pkgutil import extend_path

# Ensure our local 'mcp' namespace package extends the installed 'mcp' SDK package
# so that both 'mcp.server' (from pip) and 'mcp.<service>_mcp' (local) are discoverable.
__path__ = extend_path(__path__, __name__)
