#!/bin/bash
#
# resign-mac-dmg.sh — electron-builder 산출 arm64 앱의 ad-hoc 서명을 유효하게
# 재적용하고 DMG를 재패키징한다.
#
# 배경(실측): electron-builder가 만든 미서명(ad-hoc) 앱은 상위 번들의 CodeResources
# seal이 불일치해 `codesign --verify --deep --strict` 가 실패한다:
#   "code has no resources but signature indicates they must be present"
# Apple Silicon에서는 서명이 무효면 quarantine 여부와 무관하게 실행 시
# "‘...’이(가) 손상되었기 때문에 열 수 없습니다(damaged)" 오류가 뜬다.
#
# `codesign --force --deep --sign -` 로 깊은 재서명을 하면 서명이 유효해지고
# (valid on disk), 이후에는 quarantine이 있어도 "미확인 개발자"(우클릭→열기로 통과)로
# 완화되며 "damaged" 는 사라진다. install-mac.command 도 수신자 측에서 동일 재서명을
# 수행하지만, 사용자가 DMG에서 앱을 바로 더블클릭하는 경우까지 커버하려면 배포 DMG
# 자체가 유효 서명 상태여야 한다.
#
# 사용법:  bash scripts/resign-mac-dmg.sh
# 전제:    npx electron-builder --mac --arm64 로 dist_electron/mac-arm64/*.app 가 존재.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist_electron/mac-arm64/Mogam Works.app"
DMG="$ROOT/dist_electron/Mogam Works-0.5.4-arm64.dmg"
VOL="Mogam Works 0.5.4-arm64"
STAGE="$(mktemp -d)"

[ -d "$APP" ] || { echo "오류: 앱을 찾지 못함: $APP (먼저 electron-builder 실행)"; exit 1; }

echo "[1/4] 깊은 ad-hoc 재서명..."
codesign --force --deep --sign - "$APP"

echo "[2/4] 서명 유효성 검증(엄격)..."
codesign --verify --deep --strict "$APP"

echo "[3/4] DMG 재패키징..."
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Mogam Works.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "$VOL" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
rm -rf "$STAGE"

echo "[4/4] DMG 내부 앱 서명 재확인..."
MP="$(hdiutil attach "$DMG" -nobrowse -noverify | grep -Eo '/Volumes/[^"]+' | tail -n1)"
codesign --verify --deep --strict "$MP/Mogam Works.app" && echo "  OK: DMG 내부 앱 서명 유효"
hdiutil detach "$MP" -quiet || true

echo "완료: $DMG (유효 ad-hoc 서명). 배포 키트로 복사해 사용하세요."
