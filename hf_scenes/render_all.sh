#!/bin/sh
# Render each scene by staging it as the project's index.html (module imports resolve from root).
cd "$(dirname "$0")"
for s in "$@"; do
  cp "scenes/$s.html" index.html
  npx --yes hyperframes@0.8.223 render -o "renders/$s.mp4" --quiet --video-frame-format png 2>&1 | grep -iE "error|fail" | grep -v '\.env'
  echo "rendered $s"
done
cp index.bak index.html
