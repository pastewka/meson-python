# SPDX-FileCopyrightText: 2021 The meson-python developers
#
# SPDX-License-Identifier: MIT

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import textwrap

import packaging.tags
import pytest
import wheel.wheelfile

import mesonpy

from .conftest import adjust_packaging_platform_tag


EXT_SUFFIX = sysconfig.get_config_var('EXT_SUFFIX')
if sys.version_info <= (3, 8, 7):
    meson_ver_str = subprocess.run(['meson', '--version'], check=True, stdout=subprocess.PIPE, text=True).stdout
    meson_version = tuple(map(int, meson_ver_str.split('.')[:2]))
    if meson_version >= (0, 99):
        # Fixed in Meson 1.0, see https://github.com/mesonbuild/meson/pull/10961.
        from distutils.sysconfig import get_config_var
        EXT_SUFFIX = get_config_var('EXT_SUFFIX')

EXT_IMP_SUFFIX = re.sub(r'.pyd$', '.dll', EXT_SUFFIX) + '.a'
INTERPRETER_VERSION = f'{sys.version_info[0]}{sys.version_info[1]}'

# Test against the wheel tag generated by packaging module.
tag = next(packaging.tags.sys_tags())
ABI = tag.abi
INTERPRETER = tag.interpreter
PLATFORM = adjust_packaging_platform_tag(tag.platform)


def wheel_contents(artifact):
    # Sometimes directories have entries, sometimes not, so we filter them out.
    return {
        entry for entry in artifact.namelist()
        if not entry.endswith('/')
    }


def wheel_filename(artifact):
    return artifact.filename.split(os.sep)[-1]


def test_scipy_like(wheel_scipy_like):
    # This test is meant to exercise features commonly needed by a regular
    # Python package for scientific computing or data science:
    #   - C and Cython extensions,
    #   - including generated code,
    #   - using `install_subdir`,
    #   - packaging data files with extensions not known to Meson
    artifact = wheel.wheelfile.WheelFile(wheel_scipy_like)

    expecting = {
        'mypkg-2.3.4.dist-info/METADATA',
        'mypkg-2.3.4.dist-info/RECORD',
        'mypkg-2.3.4.dist-info/WHEEL',
        'mypkg/__init__.py',
        'mypkg/__config__.py',
        f'mypkg/extmod{EXT_SUFFIX}',
        f'mypkg/cy_extmod{EXT_SUFFIX}',
        'mypkg/submod/__init__.py',
        'mypkg/submod/unknown_filetype.npq',
    }
    if sys.platform in {'win32', 'cygwin'}:
        # Currently Meson is installing .dll.a (import libraries) next
        # to .pyd extension modules. Those are very small, so it's not
        # a major issue - just sloppy. Ensure we don't fail on those.
        expecting.update({
            f'mypkg/extmod{EXT_IMP_SUFFIX}',
            f'mypkg/cy_extmod{EXT_IMP_SUFFIX}',
        })
    assert wheel_contents(artifact) == expecting

    name = artifact.parsed_filename
    assert name.group('pyver') == INTERPRETER
    assert name.group('abi') == ABI
    assert name.group('plat') == PLATFORM


@pytest.mark.skipif(platform.system() != 'Linux', reason='Needs library vendoring, only implemented in POSIX')
def test_contents(package_library, wheel_library):
    artifact = wheel.wheelfile.WheelFile(wheel_library)

    for name, regex in zip(sorted(wheel_contents(artifact)), [  # noqa: B905
        re.escape('.library.mesonpy.libs/libexample.so'),
        re.escape('library-1.0.0.data/headers/examplelib.h'),
        re.escape('library-1.0.0.data/scripts/example'),
        re.escape('library-1.0.0.dist-info/METADATA'),
        re.escape('library-1.0.0.dist-info/RECORD'),
        re.escape('library-1.0.0.dist-info/WHEEL'),
        re.escape('library.libs/libexample.so'),
    ]):
        assert re.match(regex, name), f'{name!r} does not match {regex!r}'


def test_purelib_and_platlib(wheel_purelib_and_platlib):
    artifact = wheel.wheelfile.WheelFile(wheel_purelib_and_platlib)

    expecting = {
        f'plat{EXT_SUFFIX}',
        'purelib_and_platlib-1.0.0.data/purelib/pure.py',
        'purelib_and_platlib-1.0.0.dist-info/METADATA',
        'purelib_and_platlib-1.0.0.dist-info/RECORD',
        'purelib_and_platlib-1.0.0.dist-info/WHEEL',
    }
    if sys.platform in {'win32', 'cygwin'}:
        # Currently Meson is installing .dll.a (import libraries) next
        # to .pyd extension modules. Those are very small, so it's not
        # a major issue - just sloppy. Ensure we don't fail on those.
        expecting.update({
            f'plat{EXT_IMP_SUFFIX}'
        })

    assert wheel_contents(artifact) == expecting


def test_pure(wheel_pure):
    artifact = wheel.wheelfile.WheelFile(wheel_pure)

    assert wheel_contents(artifact) == {
        'pure-1.0.0.dist-info/METADATA',
        'pure-1.0.0.dist-info/RECORD',
        'pure-1.0.0.dist-info/WHEEL',
        'pure.py',
    }


def test_configure_data(wheel_configure_data):
    artifact = wheel.wheelfile.WheelFile(wheel_configure_data)

    assert wheel_contents(artifact) == {
        'configure_data.py',
        'configure_data-1.0.0.dist-info/METADATA',
        'configure_data-1.0.0.dist-info/RECORD',
        'configure_data-1.0.0.dist-info/WHEEL',
    }


@pytest.mark.skipif(platform.system() not in ['Linux', 'Darwin'], reason='Unsupported on this platform for now')
def test_local_lib(venv, wheel_link_against_local_lib):
    venv.pip('install', wheel_link_against_local_lib)
    output = venv.python('-c', 'import example; print(example.example_sum(1, 2))')
    assert int(output) == 3


def test_contents_license_file(wheel_license_file):
    artifact = wheel.wheelfile.WheelFile(wheel_license_file)
    assert artifact.read('license_file-1.0.0.dist-info/LICENSE.custom').rstrip() == b'Hello!'


@pytest.mark.skipif(sys.platform in {'win32', 'cygwin'}, reason='Platform does not support executable bit')
def test_executable_bit(wheel_executable_bit):
    artifact = wheel.wheelfile.WheelFile(wheel_executable_bit)

    executable_files = {
        'executable_bit-1.0.0.data/purelib/executable_module.py',
        'executable_bit-1.0.0.data/scripts/example',
        'executable_bit-1.0.0.data/scripts/example-script',
    }
    for info in artifact.infolist():
        mode = (info.external_attr >> 16) & 0o777
        assert bool(mode & stat.S_IXUSR) == (info.filename in executable_files)


def test_detect_wheel_tag_module(wheel_purelib_and_platlib):
    name = wheel.wheelfile.WheelFile(wheel_purelib_and_platlib).parsed_filename
    assert name.group('pyver') == INTERPRETER
    assert name.group('abi') == ABI
    assert name.group('plat') == PLATFORM


def test_detect_wheel_tag_script(wheel_executable):
    name = wheel.wheelfile.WheelFile(wheel_executable).parsed_filename
    assert name.group('pyver') == 'py3'
    assert name.group('abi') == 'none'
    assert name.group('plat') == PLATFORM


@pytest.mark.skipif(platform.system() not in ['Linux', 'Darwin'], reason='Unsupported on this platform for now')
def test_rpath(wheel_link_against_local_lib, tmp_path):
    artifact = wheel.wheelfile.WheelFile(wheel_link_against_local_lib)
    artifact.extractall(tmp_path)

    if platform.system() == 'Linux':
        elf = mesonpy._elf.ELF(tmp_path / f'example{EXT_SUFFIX}')
        assert '$ORIGIN/.link_against_local_lib.mesonpy.libs' in elf.rpath
    else:  # 'Darwin'
        dylib = mesonpy._dylib.Dylib(tmp_path / f'example{EXT_SUFFIX}')
        assert '@loader_path/.link_against_local_lib.mesonpy.libs' in dylib.rpath


@pytest.mark.skipif(platform.system() not in ['Linux', 'Darwin'], reason='Unsupported on this platform for now')
def test_uneeded_rpath(wheel_purelib_and_platlib, tmp_path):
    artifact = wheel.wheelfile.WheelFile(wheel_purelib_and_platlib)
    artifact.extractall(tmp_path)

    if platform.system() == 'Linux':
        shared_lib = mesonpy._elf.ELF(tmp_path / f'plat{EXT_SUFFIX}')
    else:  # 'Darwin'
        shared_lib = mesonpy._dylib.Dylib(tmp_path / f'plat{EXT_SUFFIX}')
    if shared_lib.rpath:
        # shared_lib.rpath is a frozenset, so iterate over it. An rpath may be
        # present, e.g. when conda is used (rpath will be <conda-prefix>/lib/)
        for rpath in shared_lib.rpath:
            assert 'mesonpy.libs' not in rpath


def test_entrypoints(wheel_full_metadata):
    artifact = wheel.wheelfile.WheelFile(wheel_full_metadata)

    with artifact.open('full_metadata-1.2.3.dist-info/entry_points.txt') as f:
        assert f.read().decode().strip() == textwrap.dedent('''
            [something.custom]
            example = example:custom

            [console_scripts]
            example-cli = example:cli

            [gui_scripts]
            example-gui = example:gui
        ''').strip()


def test_top_level_modules(package_module_types):
    with mesonpy.Project.with_temp_working_dir() as project:
        assert set(project._wheel_builder.top_level_modules) == {
            'file',
            'package',
            'namespace',
            'native',
        }


def test_editable(
    package_imports_itself_during_build,
    editable_imports_itself_during_build,
    venv,
):
    venv.pip('install', os.fspath(editable_imports_itself_during_build))

    assert venv.python('-c', 'import plat; print(plat.foo())').strip() == 'bar'

    plat = package_imports_itself_during_build / 'plat.c'
    plat_text = plat.read_text()
    try:
        plat.write_text(plat_text.replace('bar', 'something else'))

        assert venv.python('-c', 'import plat; print(plat.foo())').strip() == 'something else'
    finally:
        plat.write_text(plat_text)


def test_editable_broken_non_existent_build_dir(
    package_imports_itself_during_build,
    editable_imports_itself_during_build,
    venv,
    tmp_path,
):
    # Start from a clean slate, copy the package to temp path
    # Ensure there is no .mesonpy directory (could be generated by test_editable)
    shutil.rmtree(tmp_path)  # copytree requires dest not to exist
    shutil.copytree(package_imports_itself_during_build, tmp_path)
    mesonpy_dir = os.path.join(package_imports_itself_during_build, '.mesonpy')
    if os.path.isdir(mesonpy_dir):
        venv.pip('uninstall', '-y', 'imports-itself-during-build')
        shutil.rmtree(mesonpy_dir)

    venv.pip('install', os.path.join(tmp_path, mesonpy.build_editable(tmp_path)))

    # Try corrupting the build dir,
    # removing the build dir after an install will trigger it
    if os.path.isdir(mesonpy_dir):
        venv.pip('uninstall', '-y', 'imports-itself-during-build')
        shutil.rmtree(mesonpy_dir)

    venv.pip('install', os.path.join(tmp_path, mesonpy.build_editable(tmp_path)))

    assert venv.python('-c', 'import plat; print(plat.foo())').strip() == 'bar'
