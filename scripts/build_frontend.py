"""Copy the static dashboard into the Vercel output directory."""
from pathlib import Path
import json
import os
import shutil

root = Path(__file__).resolve().parent.parent
is_hosted = os.environ.get("VERCEL") == "1" or os.environ.get("GTA_HOSTED", "").lower() in {"1", "true", "yes"}
source = root / "src" / "dashboard" / "static"
output = root / "frontend_dist"
if output.exists():
    shutil.rmtree(output)
shutil.copytree(source, output)
(output / "config.js").write_text(
    "window.GTA_API_BASE = " + json.dumps(os.environ.get("GTA_API_BASE", "").rstrip("/")) + ";\n"
    f"window.GTA_HOSTED = {str(is_hosted).lower()};\n"
    f"window.GTA_CONNECT_LOCAL = {str(not is_hosted).lower()};\n",
    encoding="utf-8",
)
