"""The API Lambda has no photo layer: it must import and serve without Pillow,
imagehash, numpy or scipy.

Every test machine has those installed, so a module-level import of them
anywhere under api.app passes the whole suite and still takes every route
down with Runtime.ImportModuleError once deployed (it happened, 9 Oct 2026:
trial.process imported common.photo at the top). This runs the handler in a
fresh interpreter where those packages cannot be imported.
"""

import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

LAYER_ONLY = ("imagehash", "PIL", "numpy", "scipy", "pywt")

SCRIPT = f"""
import json, os, sys
for name in {LAYER_ONLY!r}:
    sys.modules[name] = None          # import now raises ImportError
sys.path.insert(0, {str(REPO / "backend")!r})
os.environ.update(AWS_DEFAULT_REGION="ap-south-1", AWS_REGION="ap-south-1",
                  AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing")
os.environ.pop("MOCK_AWS", None)
from api import app
import api.trials, trial.process, trial.analysis   # the whole trial route graph
health = app.lambda_handler({{"routeKey": "GET /health"}}, None)
closed = app.lambda_handler({{"routeKey": "POST /trials", "body": "{{}}"}}, None)
print(json.dumps([health["statusCode"], closed["statusCode"], json.loads(closed["body"])["code"]]))
"""


def test_the_api_imports_and_serves_without_the_photo_layer():
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT], capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    health, closed, code = json.loads(result.stdout.strip().splitlines()[-1])
    assert health == 200
    # Live mode with no invite code: trials closed, and still no photo import.
    assert (closed, code) == (503, "TRIALS_DISABLED")


def test_the_layer_requirements_are_not_in_the_api_package():
    lines = (REPO / "backend" / "requirements.txt").read_text().lower().splitlines()
    packages = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    assert not any(name in line for line in packages for name in ("pillow", "imagehash"))
