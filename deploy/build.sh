#!/bin/sh
# Assemble the Lambda package the template's CodeUri points at: the installed `disclosed` wheel
# with its `ask` extra, plus the committed data and corpus the evidence store is built from.
# Prepared, not applied (deploy/README.md). Nothing here talks to AWS.
set -eu

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/build/package"
rm -rf "$out"
mkdir -p "$out"

# Linux arm64 wheels for the Lambda runtime, not this machine's.
python -m pip install --quiet --target "$out" \
  --platform manylinux2014_aarch64 --python-version 3.12 --only-binary=:all: \
  "$root[ask]"

# The evidence store's inputs and the corpus, at the paths the code reads under DISCLOSED_ROOT.
mkdir -p "$out/data/census" "$out/data/snapshots" "$out/corpus"
cp "$root"/data/sample.json "$root"/data/report.json "$root"/data/HD*.zip "$root"/data/IC*.zip "$out/data/"
cp "$root"/data/census/scorecard.json "$out/data/census/"
cp -R "$root"/data/snapshots/. "$out/data/snapshots/"
cp -R "$root"/corpus/. "$out/corpus/"

du -sh "$out"
echo "package assembled at $out; nothing has been deployed"
