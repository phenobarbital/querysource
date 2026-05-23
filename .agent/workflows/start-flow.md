---
description: A fresh startup for work
---

---
description: Startup flow — sync repo then install + start dev environment
---

// turbo
1. Confirm we are in the project root (where the Makefile exists). If not, `cd` to the repo root.

2. Check for local changes before pulling:
   - Run `git status --porcelain`.
   - If output is non-empty, STOP and ask whether to commit/stash/discard before continuing.

// turbo
3. Run `git fetch --all`.

// turbo
4. Run `git pull`.

5. Verify if `uv` is installed.
   - Run `uv --version`.
   - If `uv` is NOT installed (command not found):
     - Run `curl -LsSf https://astral.sh/uv/install.sh | sh`
     - OR Run `pip install uv` (if curl fails).
     - Ensure `uv` is in your PATH.

6. Verify if virtualenv is enabled.
   - Run `source .venv/bin/activate` if exists.
   - If `.venv` does not exist:
     - Run `uv venv .venv` OR `make venv`.
     - Then run `source .venv/bin/activate`.

//turbo
7. Run `make develop` (or `make install`).
   - This command uses `uv sync` internally.
   - If it fails due to missing `uv`, ensure step 5 was successful.
   - Leave the dev server running if applicable.

8. If any step fails:
   - Paste the error output.
   - Diagnose the most likely cause.
   - **CRITICAL**: If the error mentions missing `uv` or `lock file`, ensure you installed `uv` as per step 5.
   - Propose the smallest fix.
   - Re-run only the failed step.