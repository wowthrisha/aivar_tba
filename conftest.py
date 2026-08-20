# Empty on purpose: its only job is to mark the repo root as a pytest
# rootdir, so pytest's default "prepend" import mode puts this directory
# on sys.path before collecting tests/ - without it, `pytest -q` (no
# `app/__init__.py`, no other path-insertion mechanism) fails with
# ModuleNotFoundError: No module named 'app'.
