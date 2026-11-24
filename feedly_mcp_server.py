#!/usr/bin/env python3
"""Feedly MCP server - launcher (stdio by default, `--transport http` for HTTP).

    python feedly_mcp_server.py [--version] [--transport stdio|http] [--host H] [--port P]

Needs the dependencies from requirements.txt in the Python that runs it
(`python scripts/install_dependencies.py`, or the setup wizard, installs them).
"""

import os
import sys

sys.dont_write_bytecode = True  # plain .py only: never write __pycache__/.pyc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feedly_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
