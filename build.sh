#!/bin/bash
# Build script for compiling sync.py to a standalone binary with Nuitka.
#
# Prerequisites:
#   pip install --break-system-packages nuitka argcomplete numpy astropy \
#       colorama mcap pymavlink scipy
#   apt-get install -y patchelf   # required by Nuitka standalone mode on Linux
#
# bytecode_bigmods_plugin.py must be in the same directory as this script.
# Run from the directory containing sync.py and bytecode_bigmods_plugin.py.

python3 -m nuitka --standalone --onefile \
  --output-dir=build \
  --output-filename=sync \
  --user-plugin=bytecode_bigmods_plugin.py \
  --no-deployment-flag=excluded-module-usage \
  --include-module=pymavlink.dialects.v10.ardupilotmega \
  --include-module=pymavlink.DFReader \
  --include-module=astropy.constants.codata2022 \
  --include-module=astropy.constants.iau2015 \
  --include-module=astropy.units.format.generic_parsetab \
  --include-module=astropy.units.format.generic_lextab \
  --include-module=astropy.units.format.cds_parsetab \
  --include-module=astropy.units.format.cds_lextab \
  --include-module=astropy.units.format.ogip_parsetab \
  --include-module=astropy.units.format.ogip_lextab \
  --include-module=astropy.coordinates.angles.angle_parsetab \
  --include-module=astropy.coordinates.angles.angle_lextab \
  --include-data-files="$(python3 -c 'import astropy,os;print(os.path.dirname(astropy.__file__))')/units/format/generic_parsetab.py=astropy/units/format/generic_parsetab.py" \
  --include-data-files="$(python3 -c 'import astropy,os;print(os.path.dirname(astropy.__file__))')/units/format/generic_lextab.py=astropy/units/format/generic_lextab.py" \
  --include-data-files="$(python3 -c 'import astropy,os;print(os.path.dirname(astropy.__file__))')/units/format/cds_parsetab.py=astropy/units/format/cds_parsetab.py" \
  --include-data-files="$(python3 -c 'import astropy,os;print(os.path.dirname(astropy.__file__))')/units/format/cds_lextab.py=astropy/units/format/cds_lextab.py" \
  --include-data-files="$(python3 -c 'import astropy,os;print(os.path.dirname(astropy.__file__))')/units/format/ogip_parsetab.py=astropy/units/format/ogip_parsetab.py" \
  --include-data-files="$(python3 -c 'import astropy,os;print(os.path.dirname(astropy.__file__))')/units/format/ogip_lextab.py=astropy/units/format/ogip_lextab.py" \
  --include-data-files="$(python3 -c 'import astropy,os;print(os.path.dirname(astropy.__file__))')/coordinates/angles/angle_parsetab.py=astropy/coordinates/angles/angle_parsetab.py" \
  --include-data-files="$(python3 -c 'import astropy,os;print(os.path.dirname(astropy.__file__))')/coordinates/angles/angle_lextab.py=astropy/coordinates/angles/angle_lextab.py" \
  --nofollow-import-to=matplotlib \
  --nofollow-import-to=wx \
  --nofollow-import-to=PyQt5 \
  --nofollow-import-to=PyQt6 \
  --nofollow-import-to=PySide2 \
  --nofollow-import-to=PySide6 \
  --nofollow-import-to=pandas \
  --nofollow-import-to=PIL \
  --nofollow-import-to=mpmath \
  --nofollow-import-to=chardet \
  --nofollow-import-to=requests \
  --nofollow-import-to=urllib3 \
  --nofollow-import-to=IPython \
  --nofollow-import-to=bs4 \
  --nofollow-import-to=asdf \
  --nofollow-import-to=h5py \
  --nofollow-import-to=sympy \
  --nofollow-import-to=astropy.visualization \
  --nofollow-import-to=astropy.stats \
  --nofollow-import-to=astropy.convolution \
  --nofollow-import-to=astropy.timeseries \
  --nofollow-import-to=astropy.cosmology \
  --nofollow-import-to=astropy.modeling \
  --nofollow-import-to=astropy.nddata \
  --nofollow-import-to=astropy.io.votable \
  --nofollow-import-to=astropy.io.misc.pandas \
  --nofollow-import-to=astropy.io.misc.asdf \
  --nofollow-import-to=astropy.io.fits \
  --assume-yes-for-downloads \
  --jobs="$(nproc)" \
  sync.py

echo "Binary produced at: build/sync_logs"
