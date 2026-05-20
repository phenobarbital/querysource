"""
Root conftest.py for the feat-095-multiquery-documentation worktree.

Ensures that the worktree's Python modules take priority over any
installed or other-repo copies of querysource, so that tests exercise
the code modified in this branch rather than the main working tree.
"""
import sys
import os

# Prepend worktree root so its querysource package shadows the main repo's.
_worktree_root = os.path.dirname(__file__)
if _worktree_root not in sys.path:
    sys.path.insert(0, _worktree_root)
