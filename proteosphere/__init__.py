"""ProteoSphere freestanding package.

A peer who downloads ``proteosphere/`` plus a warehouse directory should be
able to run any audit without modifying source code. The single entry point
for path resolution is :class:`Config`.
"""
from proteosphere.config import Config

__all__ = ["Config"]
__version__ = "1.0.0"
