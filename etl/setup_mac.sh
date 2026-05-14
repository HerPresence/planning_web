#!/usr/bin/env bash
# setup_mac.sh — one-time setup: download ADOMD.NET NuGet DLL for Mac pwsh.
# Run once before first ETL execution on Mac.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

PACKAGE="Microsoft.AnalysisServices.AdomdClient"
VERSION="19.113.7"
# NuGet v3 flat-container URL (lowercase package name) — more reliable than v2
NUPKG_URL="https://api.nuget.org/v3-flatcontainer/microsoft.analysisservices.adomdclient/${VERSION}/microsoft.analysisservices.adomdclient.${VERSION}.nupkg"
# Fallback v2 URL (used if v3 fails)
NUPKG_URL_V2="https://www.nuget.org/api/v2/package/${PACKAGE}/${VERSION}"

echo "=== ETL SSAS setup for Mac ==="
echo "ETL dir : $SCRIPT_DIR"
echo "Lib dir : $LIB_DIR"
echo ""

# Check pwsh
if ! command -v pwsh &>/dev/null; then
    echo "[ERROR] pwsh not found."
    echo "Install: brew install powershell"
    exit 1
fi
echo "[OK] $(pwsh --version)"

mkdir -p "$LIB_DIR"

TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

echo ""
echo "Downloading ${PACKAGE} v${VERSION} from NuGet..."
echo "URL: $NUPKG_URL"

NUPKG="$TMP_DIR/adomd.nupkg"

# Try v3 first, then v2 fallback
if ! curl -L --fail --progress-bar -o "$NUPKG" "$NUPKG_URL"; then
    echo "[WARN] v3 URL failed, trying v2 fallback..."
    echo "URL: $NUPKG_URL_V2"
    curl -L --fail --progress-bar -o "$NUPKG" "$NUPKG_URL_V2" || {
        echo "[ERROR] Download failed from both URLs."
        exit 1
    }
fi

echo ""
echo "Download complete. File info:"
file "$NUPKG"
echo "Size: $(du -sh "$NUPKG" | cut -f1)"

# Validate it's a real ZIP/nupkg before extracting
if ! unzip -t "$NUPKG" &>/dev/null; then
    echo ""
    echo "[ERROR] Downloaded file is not a valid ZIP/nupkg."
    echo "First 20 lines of the file:"
    head -20 "$NUPKG" 2>/dev/null || xxd "$NUPKG" | head -20
    exit 1
fi
echo "[OK] Package is a valid ZIP."

echo ""
echo "Extracting..."
cd "$TMP_DIR" && unzip -q "$NUPKG"

# Priority: net8.0 (pwsh 7.4+), net6.0 (pwsh 7.2+), net472 (fallback)
DLL_SRC=""
for target in "lib/net8.0" "lib/net6.0" "lib/net472" "lib/netstandard2.0" "lib/net45"; do
    candidate="$TMP_DIR/$target/Microsoft.AnalysisServices.AdomdClient.dll"
    if [ -f "$candidate" ]; then
        DLL_SRC="$(dirname "$candidate")"
        echo "Using target: $target"
        break
    fi
done

if [ -z "$DLL_SRC" ]; then
    echo "[ERROR] Microsoft.AnalysisServices.AdomdClient.dll not found in package."
    echo "All DLLs in package (non-locale):"
    find "$TMP_DIR/lib" -name "*.dll" ! -path "*/*-*/*" | sort
    exit 1
fi

echo "Copying DLLs to $LIB_DIR ..."
# Copy all non-locale DLLs from the selected target (AdomdClient + Runtime companions)
find "$DLL_SRC" -maxdepth 1 -name "*.dll" -exec cp {} "$LIB_DIR/" \;

echo ""
echo "DLLs installed:"
ls -la "$LIB_DIR"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy .env.example -> .env and fill in credentials"
echo "  2. Run ETL:"
echo "       cd $SCRIPT_DIR"
echo "       pip install -r requirements.txt"
echo "       python etl_pnl_olap.py"
