#!/bin/bash
#
# Install the optional phomymo label designer into vendor/phomymo.
# faxomemo serves it at /labels once this directory exists, and the web UI
# shows a "Labels" tab.
#
# phomymo loads Tailwind, JsBarcode, jsPDF, pdf.js and QRCode from public CDNs,
# so the designer needs internet access to open.

set -euo pipefail

REPO_URL="https://github.com/transcriptionstream/phomymo.git"
REF="${PHOMYMO_REF:-master}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${PROJECT_ROOT}/vendor/phomymo"

if [ -d "${TARGET}/.git" ]; then
    echo "Updating ${TARGET}..."
    git -C "${TARGET}" fetch --depth 1 origin "${REF}"
    git -C "${TARGET}" checkout --force FETCH_HEAD
else
    echo "Cloning phomymo into ${TARGET}..."
    mkdir -p "$(dirname "${TARGET}")"
    rm -rf "${TARGET}"
    git clone --depth 1 --branch "${REF}" "${REPO_URL}" "${TARGET}"
fi

if [ ! -f "${TARGET}/src/web/index.html" ]; then
    echo "Error: ${TARGET}/src/web/index.html is missing. The upstream layout may have changed." >&2
    exit 1
fi

echo "Done. Restart faxomemo, then open /labels."
