# mypy: ignore-errors
# pylint: skip-file

"""Nuitka user plugin: force specific huge generated modules to be included
as bytecode instead of being compiled to C, to avoid excessive compile
time/memory usage on the giant auto-generated pymavlink dialect files.
"""

from nuitka.plugins.PluginBase import NuitkaPluginBase

BYTECODE_MODULES = {
    'pymavlink.dialects.v10.ardupilotmega',
    'pymavlink.dialects.v10.common',
    'pymavlink.dialects.v20.ardupilotmega',
    'pymavlink.dialects.v20.common',
    'pymavlink.dialects.v09.ardupilotmega',
    'pymavlink.dialects.v09.common',
}

# astropy.units'/coordinates' PLY-based lexer/parser machinery relies on
# introspecting the calling frame's locals/globals (sys._getframe()) to
# discover grammar rules. Nuitka-compiled functions don't reproduce CPython
# frame semantics closely enough for this to work, so keep everything in
# that call chain interpreted (bytecode) instead of C-compiled.
BYTECODE_PREFIXES = (
    'astropy.extern.ply.',
    'astropy.utils.parsing',
    'astropy.units.format.',
    'astropy.coordinates.angles.',
)


class NuitkaPluginBytecodeBigMods(NuitkaPluginBase):
    plugin_name = 'bytecode-bigmods'
    plugin_desc = 'Force huge generated modules to stay as bytecode.'

    @staticmethod
    def isAlwaysEnabled():
        return True

    def decideCompilation(self, module_name):
        name = str(module_name)
        if name in BYTECODE_MODULES:
            return 'bytecode'
        if name.startswith(BYTECODE_PREFIXES) or name in (
            'astropy.extern.ply',
            'astropy.utils.parsing',
        ):
            return 'bytecode'
        return None
