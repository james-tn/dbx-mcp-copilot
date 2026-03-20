#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
APP_PACKAGE_DIR="$ROOT_DIR/appPackage"
BUILD_DIR="$APP_PACKAGE_DIR/build"
TEMPLATE_PATH="$APP_PACKAGE_DIR/manifest.template.json"
MANIFEST_PATH="$BUILD_DIR/manifest.json"
COLOR_ICON_PATH="$BUILD_DIR/color.png"
OUTLINE_ICON_PATH="$BUILD_DIR/outline.png"
ZIP_PATH="$BUILD_DIR/daily-account-planner-m365.zip"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

mkdir -p "$BUILD_DIR"

WRAPPER_BASE_URL="${WRAPPER_BASE_URL:-https://daily-planner-m365.mangobeach-b4158ed2.eastus2.azurecontainerapps.io}"
BOT_APP_ID="${BOT_APP_ID:-}"
if [[ -z "$BOT_APP_ID" ]]; then
  echo "BOT_APP_ID is required in $ENV_FILE or the environment." >&2
  exit 1
fi
BOT_SSO_APP_ID="${BOT_SSO_APP_ID:-$BOT_APP_ID}"
BOT_SSO_RESOURCE="${BOT_SSO_RESOURCE:-api://botid-$BOT_APP_ID}"

M365_APP_PACKAGE_ID="${M365_APP_PACKAGE_ID:-4ce15231-5892-465f-82ea-c1764d5de891}"
APP_VERSION="${APP_VERSION:-1.0.0}"
M365_APP_SHORT_NAME="${M365_APP_SHORT_NAME:-Daily Planner}"
M365_APP_FULL_NAME="${M365_APP_FULL_NAME:-Daily Account Planner}"
M365_APP_SHORT_DESCRIPTION="${M365_APP_SHORT_DESCRIPTION:-Databricks-backed seller planning agent for Microsoft 365 Copilot.}"
M365_APP_FULL_DESCRIPTION="${M365_APP_FULL_DESCRIPTION:-Daily Account Planner helps sellers get an account pulse, prioritize next moves, and draft outreach using Databricks-backed account intelligence surfaced through a custom engine agent.}"
M365_APP_ACCENT_COLOR="${M365_APP_ACCENT_COLOR:-#00B336}"
M365_DEVELOPER_NAME="${M365_DEVELOPER_NAME:-Veeam}"
M365_DEVELOPER_WEBSITE_URL="${M365_DEVELOPER_WEBSITE_URL:-https://www.veeam.com}"
M365_PRIVACY_URL="${M365_PRIVACY_URL:-https://www.veeam.com/privacy-notice.html}"
M365_TERMS_URL="${M365_TERMS_URL:-https://www.veeam.com/terms-of-use.html}"
M365_AGENT_DISCLAIMER="${M365_AGENT_DISCLAIMER:-Responses use synthetic MVP data and may require seller verification before external use.}"
WRAPPER_VALID_DOMAIN="$(python - <<'PY' "$WRAPPER_BASE_URL"
import sys
from urllib.parse import urlparse
print(urlparse(sys.argv[1]).netloc)
PY
)"

python - <<'PY' "$TEMPLATE_PATH" "$MANIFEST_PATH" \
  "$APP_VERSION" \
  "$M365_APP_PACKAGE_ID" \
  "$M365_DEVELOPER_NAME" \
  "$M365_DEVELOPER_WEBSITE_URL" \
  "$M365_PRIVACY_URL" \
  "$M365_TERMS_URL" \
  "$M365_APP_SHORT_NAME" \
  "$M365_APP_FULL_NAME" \
  "$M365_APP_SHORT_DESCRIPTION" \
  "$M365_APP_FULL_DESCRIPTION" \
  "$M365_APP_ACCENT_COLOR" \
  "$BOT_APP_ID" \
  "$BOT_SSO_APP_ID" \
  "$BOT_SSO_RESOURCE" \
  "$WRAPPER_VALID_DOMAIN" \
  "$M365_AGENT_DISCLAIMER"
import json
import re
import sys
from pathlib import Path

template_path = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
keys = [
    "APP_VERSION",
    "M365_APP_PACKAGE_ID",
    "M365_DEVELOPER_NAME",
    "M365_DEVELOPER_WEBSITE_URL",
    "M365_PRIVACY_URL",
    "M365_TERMS_URL",
    "M365_APP_SHORT_NAME",
    "M365_APP_FULL_NAME",
    "M365_APP_SHORT_DESCRIPTION",
    "M365_APP_FULL_DESCRIPTION",
    "M365_APP_ACCENT_COLOR",
    "BOT_APP_ID",
    "BOT_SSO_APP_ID",
    "BOT_SSO_RESOURCE",
    "WRAPPER_VALID_DOMAIN",
    "M365_AGENT_DISCLAIMER",
]
values = dict(zip(keys, sys.argv[3:], strict=True))
template = template_path.read_text(encoding="utf-8")

pattern = re.compile(r"\$\{([A-Z0-9_]+)\}")

def replace(match):
    key = match.group(1)
    return values.get(key, "")

rendered = pattern.sub(replace, template)
data = json.loads(rendered)
manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

python - <<'PY' "$COLOR_ICON_PATH" "$OUTLINE_ICON_PATH"
import struct
import sys
import zlib
from pathlib import Path

def chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack("!I", len(data))
        + tag
        + data
        + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )

def write_png(path: Path, width: int, height: int, pixel_fn):
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(pixel_fn(x, y))
        rows.append(bytes(row))
    raw = b"".join(rows)
    ihdr = struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)

def color_pixels(x: int, y: int):
    border = 12
    if x < border or y < border or x >= 192 - border or y >= 192 - border:
        return (0, 179, 54, 255)
    if 32 <= x <= 160 and 40 <= y <= 72:
        return (255, 255, 255, 255)
    if 32 <= x <= 64 and 40 <= y <= 152:
        return (255, 255, 255, 255)
    if 32 <= x <= 128 and 120 <= y <= 152:
        return (255, 255, 255, 255)
    if 96 <= x <= 160 and 80 <= y <= 112:
        return (255, 255, 255, 255)
    return (0, 140, 42, 255)

def outline_pixels(x: int, y: int):
    if 4 <= x <= 7 and 4 <= y <= 27:
        return (0, 0, 0, 255)
    if 4 <= x <= 18 and 4 <= y <= 7:
        return (0, 0, 0, 255)
    if 4 <= x <= 18 and 24 <= y <= 27:
        return (0, 0, 0, 255)
    if 15 <= x <= 18 and 8 <= y <= 23:
        return (0, 0, 0, 255)
    if 20 <= x <= 27 and 4 <= y <= 27:
        return (0, 0, 0, 255)
    if 24 <= x <= 27 and 4 <= y <= 16:
        return (0, 0, 0, 255)
    return (0, 0, 0, 0)

write_png(Path(sys.argv[1]), 192, 192, color_pixels)
write_png(Path(sys.argv[2]), 32, 32, outline_pixels)
PY

python - <<'PY' "$ZIP_PATH" "$MANIFEST_PATH" "$COLOR_ICON_PATH" "$OUTLINE_ICON_PATH"
import sys
import zipfile
from pathlib import Path

zip_path = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
color_icon_path = Path(sys.argv[3])
outline_icon_path = Path(sys.argv[4])

with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    archive.write(manifest_path, arcname="manifest.json")
    archive.write(color_icon_path, arcname="color.png")
    archive.write(outline_icon_path, arcname="outline.png")
PY

echo "Manifest written to: $MANIFEST_PATH"
echo "App package written to: $ZIP_PATH"
