#!/usr/bin/env bash
# Fetches the Piper voice the probe speaks with into the directory named by the first argument, at the revision and
# with the checksums below. The Dockerfile and the end-to-end job run this script, so the image and CI hold the same
# bytes. A file that fails its checksum is removed and the script fails.
set -euo pipefail

repository=rhasspy/piper-voices
revision=b4e358ffcb232591bf73a540e70f7de528e4174a
directory=de/de_DE/thorsten/medium
voice=de_DE-thorsten-medium
model_sha256=7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819
metadata_sha256=974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85

target=${1:?usage: fetch_probe_voice.sh DIRECTORY}
mkdir -p "$target"

fetch() {
  local file=$1 sha256=$2 partial
  if [ -f "$target/$file" ] && echo "$sha256  $target/$file" | sha256sum --check --status; then
    return
  fi
  partial=$(mktemp "$target/$file.XXXXXX")
  curl --fail --silent --show-error --location --retry 3 \
    "https://huggingface.co/$repository/resolve/$revision/$directory/$file" --output "$partial"
  if ! echo "$sha256  $partial" | sha256sum --check --status; then
    rm -f "$partial"
    echo "$file does not have the pinned checksum" >&2
    exit 1
  fi
  chmod 0644 "$partial"
  mv "$partial" "$target/$file"
}

fetch "$voice.onnx" "$model_sha256"
fetch "$voice.onnx.json" "$metadata_sha256"
