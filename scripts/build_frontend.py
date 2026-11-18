"""Copy the static dashboard into the Vercel output directory."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent.parent
source = root / "src" / "dashboard" / "static"
output = root / "frontend_dist"
if output.exists():
    shutil.rmtree(output)
shutil.copytree(source, output)
(output / "config.js").write_text(
    "window.GTA_API_BASE = '';\n"
    "window.GTA_HOSTED = false;\n"
    "window.GTA_CONNECT_LOCAL = true;\n",
    encoding="utf-8",
)
