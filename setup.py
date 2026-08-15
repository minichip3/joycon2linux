"""Setuptools build script with C extension module."""
from setuptools import Extension, setup

ble_ext = Extension(
    "j2l._ble",
    sources=["src/j2l/_ble.c"],
    include_dirs=["src/j2l/cinclude"],
    libraries=["bluetooth"],
)

setup(
    ext_modules=[ble_ext],
)
