#!/bin/bash
# Build script for compiling the log-sync script to a standalone binary with Nuitka.
#
# Prerequisites:
#   pip install --break-system-packages nuitka argcomplete numpy astropy \
#       colorama mcap pymavlink scipy
#   apt-get install -y patchelf   # required by Nuitka standalone mode on Linux
#
# bytecode_bigmods_plugin.py must be in the same directory as this script.
#
# Usage:
#   ./build.sh                # looks for sync.py in this directory
#   ./build.sh my_script.py   # or pass the entry script explicitly
set -euo pipefail

ENTRY="${1:-sync.py}"
if [ ! -f "$ENTRY" ]; then
    echo "Entry script '$ENTRY' not found in $(pwd)." >&2
    echo "Usage: ./build.sh [path/to/script.py]" >&2
    exit 1
fi

ASTROPY_DIR="$(python3 -c 'import astropy, os; print(os.path.dirname(astropy.__file__))')"

# scipy vendors the array-api-compat shim under different package paths
# depending on version (scipy._lib.array_api_compat vs
# scipy._external.array_api_compat). It's picked dynamically at runtime via
# a computed importlib.import_module() call that Nuitka's static analysis
# can't see, so detect and include whichever one is actually present.
ARRAY_API_PKG="$(python3 -c "
import importlib
for cand in ('scipy._lib.array_api_compat', 'scipy._external.array_api_compat'):
    try:
        importlib.import_module(cand)
        print(cand)
        break
    except ImportError:
        pass
")"

ARGS=(
  --standalone --onefile
  --output-dir=build
  --output-filename=sync
  --user-plugin=bytecode_bigmods_plugin.py
  --no-deployment-flag=excluded-module-usage

  --include-module=pymavlink.dialects.v10.ardupilotmega
  --include-module=pymavlink.dialects.v10.common
  --include-module=pymavlink.dialects.v20.ardupilotmega
  --include-module=pymavlink.dialects.v20.common
  --include-module=pymavlink.DFReader
  --include-module=astropy.constants.codata2022
  --include-module=astropy.constants.iau2015
  --include-module=astropy.units.format.generic_parsetab
  --include-module=astropy.units.format.generic_lextab
  --include-module=astropy.units.format.cds_parsetab
  --include-module=astropy.units.format.cds_lextab
  --include-module=astropy.units.format.ogip_parsetab
  --include-module=astropy.units.format.ogip_lextab
  --include-module=astropy.coordinates.angles.angle_parsetab
  --include-module=astropy.coordinates.angles.angle_lextab

  --include-data-files="${ASTROPY_DIR}/units/format/generic_parsetab.py=astropy/units/format/generic_parsetab.py"
  --include-data-files="${ASTROPY_DIR}/units/format/generic_lextab.py=astropy/units/format/generic_lextab.py"
  --include-data-files="${ASTROPY_DIR}/units/format/cds_parsetab.py=astropy/units/format/cds_parsetab.py"
  --include-data-files="${ASTROPY_DIR}/units/format/cds_lextab.py=astropy/units/format/cds_lextab.py"
  --include-data-files="${ASTROPY_DIR}/units/format/ogip_parsetab.py=astropy/units/format/ogip_parsetab.py"
  --include-data-files="${ASTROPY_DIR}/units/format/ogip_lextab.py=astropy/units/format/ogip_lextab.py"
  --include-data-files="${ASTROPY_DIR}/coordinates/angles/angle_parsetab.py=astropy/coordinates/angles/angle_parsetab.py"
  --include-data-files="${ASTROPY_DIR}/coordinates/angles/angle_lextab.py=astropy/coordinates/angles/angle_lextab.py"

  --nofollow-import-to=matplotlib
  --nofollow-import-to=wx
  --nofollow-import-to=PyQt5
  --nofollow-import-to=PyQt6
  --nofollow-import-to=PySide2
  --nofollow-import-to=PySide6
  --nofollow-import-to=pandas
  --nofollow-import-to=PIL
  --nofollow-import-to=mpmath
  --nofollow-import-to=chardet
  --nofollow-import-to=requests
  --nofollow-import-to=urllib3
  --nofollow-import-to=IPython
  --nofollow-import-to=bs4
  --nofollow-import-to=asdf
  --nofollow-import-to=h5py
  --nofollow-import-to=sympy
  --nofollow-import-to=astropy.visualization
  --nofollow-import-to=astropy.stats
  --nofollow-import-to=astropy.convolution
  --nofollow-import-to=astropy.timeseries
  --nofollow-import-to=astropy.cosmology
  --nofollow-import-to=astropy.modeling
  --nofollow-import-to=astropy.nddata
  --nofollow-import-to=astropy.io.votable
  --nofollow-import-to=astropy.io.misc.pandas
  --nofollow-import-to=astropy.io.misc.asdf

  --assume-yes-for-downloads
  --jobs="$(nproc)"
)

if [ -n "$ARRAY_API_PKG" ]; then
    echo "Including scipy array-api-compat shim: $ARRAY_API_PKG"
    ARGS+=(--include-package="$ARRAY_API_PKG")
else
    echo "WARNING: could not locate scipy's array_api_compat shim (checked scipy._lib and scipy._external)." >&2
    echo "If the built binary fails with a ModuleNotFoundError under scipy._lib/._external, add:" >&2
    echo "  --include-package=<the missing top-level package shown in the traceback>" >&2
fi

python3 -m nuitka "${ARGS[@]}" "$ENTRY"

echo "Binary produced at: build/sync"
