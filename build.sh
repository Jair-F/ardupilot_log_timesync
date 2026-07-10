#!/bin/bash

# Ensure ccache is installed
if ! command -v ccache &> /dev/null; then
    echo "Installing ccache..."
    sudo apt update && sudo apt install -y ccache
fi

# Clean up any previous builds to avoid conflicts
rm -rf sync.dist

echo "🚀 Running fast Nuitka compilation..."
python3 -m nuitka \
    --standalone \
    --jobs=$(nproc) \
    --include-package=packaging \
    --include-package=numpy.lib.recfunctions \
    --include-package=erfa \
    --include-package=scipy._external \
    --nofollow-import-to=astropy \
    --no-deployment-flag=excluded-module-usage \
    sync.py

echo "📦 Injecting raw Astropy into the standalone folder..."
# Find exactly where astropy is installed in your container environment
ASTROPY_PATH=$(python3 -c "import astropy; print(astropy.__path__[0])")

# Copy the entire package cleanly into the sync.dist folder
cp -r "$ASTROPY_PATH" sync.dist/

# Lean cleanup: Only delete heavy nested sub-tests, keeping top-level initialization happy
rm -rf sync.dist/astropy/*/tests

echo "✅ Done! Try running: ./sync.dist/sync.bin"
