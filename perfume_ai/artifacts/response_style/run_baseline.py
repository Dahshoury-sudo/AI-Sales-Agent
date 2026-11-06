"""Evaluate the committed baseline in a disposable source snapshot and test DB."""
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile

from dotenv import load_dotenv

root = Path(__file__).resolve().parents[2]
repo = root.parent
load_dotenv(root / ".env")
paths = subprocess.check_output(
    ["git", "ls-tree", "-r", "--name-only", "HEAD", "--", "perfume_ai/products",
     "perfume_ai/perfume_ai", "perfume_ai/dashboard", "perfume_ai/marketing",
     "perfume_ai/eval_harness", "perfume_ai/scripts/evaluate_agent_budgeted.py"],
    cwd=repo, text=True,
).splitlines()
paths = [name for name in paths if name.endswith(".py")]
archive = subprocess.check_output(["git", "archive", "HEAD", "--", *paths], cwd=repo)
with tempfile.TemporaryDirectory(prefix="perfume-response-baseline-") as directory:
    snapshot = Path(directory).resolve()
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source.getmembers():
            if not member.isfile():
                continue
            target = (snapshot / member.name).resolve()
            target.relative_to(snapshot)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.extractfile(member).read())
    app = snapshot / "perfume_ai"
    sys.path.insert(0, str(app))
    os.chdir(app)
    sys.argv = [str(app / "scripts/evaluate_agent_budgeted.py"),
                "--budget", "0.30", "--suite", "core", "--workers", "3",
                "--scenario-ids", "D2,R2,P1,O5,X1,CS1,P3,C1,F1",
                "--output", str(root / "artifacts/response_style/live_before")]
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
    finally:
        os.chdir(root)
