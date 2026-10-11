#!/usr/bin/env bash
# The URL and the digest are the ones the engine itself pins, in
# r5py/util/classpath.py - keep them in step with the r5py version in
# pyproject.toml's `matrix` group.
set -euo pipefail

VERSION="v7.5.1-r5py"
JAR="r5-${VERSION}-all.jar"
URL="https://github.com/r5py/r5/releases/download/${VERSION}/${JAR}"
SHA256="d50be106cadd7b636cfc0e209052767d7df570629f79fdf98ecd5cf5d2d89be7"

cd "$(dirname "$0")/.."
mkdir -p vendor

if [ -f "vendor/${JAR}" ] && echo "${SHA256}  vendor/${JAR}" | sha256sum --check --status; then
    echo "vendor/${JAR} is already in place"
    exit 0
fi

CACHED="${XDG_CACHE_HOME:-${HOME}/.cache}/r5py/${JAR}"
if [ -f "${CACHED}" ]; then
    echo "copying ${CACHED}"
    cp "${CACHED}" "vendor/${JAR}"
else
    echo "downloading ${URL}"
    curl --fail --location --output "vendor/${JAR}" "${URL}"
fi

echo "${SHA256}  vendor/${JAR}" | sha256sum --check
