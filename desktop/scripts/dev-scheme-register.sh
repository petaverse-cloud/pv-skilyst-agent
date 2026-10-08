#!/bin/bash
# #25: register skilyst:// for a `tauri dev` run (macOS).
# A bare debug binary has no Info.plist, so LaunchServices refuses it as a
# URL handler even with LSSetDefaultHandlerForURLScheme (it needs a
# registered app). We wrap the running binary in a throwaway .app stub and
# register that once; the stub's executable symlink resolves to the binary,
# so a cold skilyst:// link launches the actual dev process.
set -euo pipefail
BIN="$1"                 # target/debug/skilyst-agent
BUNDLE_ID="${2:-com.petaverse.skilyst-agent}"
STUB_DIR="$BIN.dev.app"  # /path/to/skilyst-agent.dev.app
CONTENTS="$STUB_DIR/Contents"
mkdir -p "$CONTENTS/MacOS"
ln -sf "$(cd "$(dirname "$BIN")" && pwd)/$(basename "$BIN")" "$CONTENTS/MacOS/skilyst-agent"
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>skilyst-agent</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleName</key><string>Skilyst Agent Dev</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleURLTypes</key>
  <array><dict>
    <key>CFBundleURLName</key><string>ai.skilyst.agent</string>
    <key>CFBundleURLSchemes</key><array><string>skilyst</string></array>
  </dict></array>
</dict>
</plist>
PLIST
touch "$STUB_DIR"
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
"$LSREG" -f "$STUB_DIR"
echo "[deep-link] dev stub registered: $STUB_DIR (handler $BUNDLE_ID)"
