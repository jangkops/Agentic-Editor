#!/bin/bash
#
# AI Editor — macOS 서명 + 공증(notarization) 빌드 (무경고 더블클릭 배포용)
#
# 전제(모두 갖춰져야 무경고 배포가 성립한다 — 하나라도 없으면 중단):
#   1) Developer ID Application 인증서가 이 Mac 키체인에 설치되어 있을 것
#      (Apple Developer Program 가입 후 발급)
#   2) 공증용 Apple 자격증명 (환경변수):
#        APPLE_ID                    — Apple Developer 계정 이메일
#        APPLE_APP_SPECIFIC_PASSWORD — appleid.apple.com에서 발급한 앱 암호
#        APPLE_TEAM_ID               — 10자리 팀 ID
#      (선택) CSC_LINK / CSC_KEY_PASSWORD — .p12를 파일로 주입할 때만.
#             키체인에 인증서가 이미 있으면 생략 가능.
#
# 사용법:
#   export APPLE_ID="you@example.com"
#   export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"
#   export APPLE_TEAM_ID="ABCDE12345"
#   bash scripts/sign-and-notarize-mac.sh
#
# 결과: dist_electron/AI Editor-<버전>-<arch>.dmg 가 서명+공증+스테이플되어
#       어떤 Mac에서도 경고 없이 더블클릭 설치된다.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "==============================================="
echo "  AI Editor — 서명 + 공증 빌드"
echo "==============================================="

# ── 1) 전제조건 검사 (없으면 즉시 중단하고 사유 안내) ──────────────────
fail=0

echo "[검사] Developer ID Application 인증서..."
if security find-identity -v -p codesigning 2>/dev/null | grep -q "Developer ID Application"; then
  IDENTITY_LINE="$(security find-identity -v -p codesigning | grep "Developer ID Application" | head -n1)"
  echo "      ✓ 발견: ${IDENTITY_LINE}"
else
  echo "      ✗ 없음 — Apple Developer Program 가입 후 'Developer ID Application'"
  echo "         인증서를 발급하여 이 Mac 키체인에 설치해야 합니다."
  fail=1
fi

echo "[검사] 공증용 Apple 자격증명(APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID)..."
for v in APPLE_ID APPLE_APP_SPECIFIC_PASSWORD APPLE_TEAM_ID; do
  if [ -z "${!v:-}" ]; then
    echo "      ✗ 환경변수 ${v} 미설정"
    fail=1
  else
    echo "      ✓ ${v} 설정됨"
  fi
done

if [ "${fail}" -ne 0 ]; then
  echo ""
  echo "중단: 무경고(서명+공증) 배포에 필요한 자산이 없습니다."
  echo "      위 ✗ 항목을 갖춘 뒤 다시 실행하세요. (자산 없이는 macOS 보안상 불가)"
  exit 1
fi

# ── 2) 백엔드 동결 (PyInstaller) ─────────────────────────────────────
echo "[1/3] Python 백엔드 동결 빌드..."
npm run build:python

# ── 3) electron-builder 서명 + 공증 빌드 ─────────────────────────────
# electron-builder는 키체인의 Developer ID Application 인증서로 자동 서명하고,
# APPLE_ID/APPLE_APP_SPECIFIC_PASSWORD/APPLE_TEAM_ID로 notarytool 공증을 수행한다.
# electron-builder.yml의 mac.notarize를 런타임에 true로 오버라이드한다.
echo "[2/3] 서명 + 공증 빌드 (electron-builder, notarize=true)..."
npx electron-builder --mac \
  --config.mac.notarize=true

# ── 4) 스테이플 검증 (공증 티켓 임베드 확인) ─────────────────────────
echo "[3/3] 공증 스테이플 검증..."
shopt -s nullglob
ok=1
for dmg in dist_electron/*.dmg; do
  echo "  • ${dmg}"
  if xcrun stapler validate "${dmg}" 2>/dev/null; then
    echo "    ✓ 스테이플 확인 — 무경고 설치 가능"
  else
    echo "    ✗ 스테이플 실패 — 공증이 완료되지 않았습니다"
    ok=0
  fi
done

if [ "${ok}" -ne 1 ]; then
  echo "경고: 일부 산출물의 공증 스테이플이 확인되지 않았습니다. 로그를 확인하세요."
  exit 1
fi

echo ""
echo "==============================================="
echo "  ✓ 서명 + 공증 완료 — dist_electron/*.dmg"
echo "  이 DMG는 어떤 Mac에서도 경고 없이 더블클릭 설치됩니다."
echo "==============================================="
