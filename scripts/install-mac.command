#!/bin/bash
#
# Mogam Works — macOS 설치 도우미 (미서명 빌드용 Gatekeeper 우회 자동화)
#
# 사용법: 이 파일과 같은 폴더에 DMG(Mogam Works-<버전>-<arch>.dmg)가 있어야 한다.
#   - Finder에서 이 파일을 더블클릭 (또는 우클릭 → 열기)
#   - 혹은 터미널에서:  bash "install-mac.command"
#
# 하는 일:
#   1) CPU 아키텍처(arm64/x64) 자동 감지 → 알맞은 DMG 선택
#   2) DMG 마운트 → 내부 "Mogam Works.app"을 /Applications로 복사
#   3) 복사된 앱의 com.apple.quarantine 속성 제거 (Gatekeeper 경고 우회)
#   4) ad-hoc 서명 재적용(Apple Silicon "손상됨" 오류 예방) → DMG 언마운트
#
# 주의: 이 스크립트는 미서명 앱을 신뢰하도록 강제하는 것이다. 신뢰할 수 있는
#       배포처(사내 담당자)로부터 받은 파일에만 사용하라. 근본 해결은 서명·공증이다.

set -euo pipefail

APP_NAME="Mogam Works.app"
DEST="/Applications/${APP_NAME}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==============================================="
echo "  Mogam Works 설치 도우미"
echo "==============================================="

# 1) 아키텍처 감지 → DMG 선택 (버전 무관하게 폴더 내 최신 dmg 매칭)
ARCH="$(uname -m)"
if [ "${ARCH}" = "arm64" ]; then
  PATTERN="arm64"
  echo "[1/4] CPU: Apple Silicon(arm64) 감지"
else
  PATTERN="x64"
  echo "[1/4] CPU: Intel(x64) 감지"
fi

# 같은 폴더에서 아키텍처에 맞는 DMG를 찾는다(가장 최근 수정본 우선).
DMG="$(ls -t "${DIR}"/*"${PATTERN}"*.dmg 2>/dev/null | head -n1 || true)"
if [ -z "${DMG}" ]; then
  # arch 매칭 실패 시 폴더 내 아무 Mogam Works DMG라도 시도
  DMG="$(ls -t "${DIR}"/*.dmg 2>/dev/null | head -n1 || true)"
fi
if [ -z "${DMG}" ] || [ ! -f "${DMG}" ]; then
  echo "오류: 같은 폴더에서 설치용 .dmg 파일을 찾지 못했습니다."
  echo "      이 스크립트를 DMG와 같은 폴더에 두고 다시 실행하세요."
  echo ""
  read -r -p "엔터를 눌러 종료합니다..." _ || true
  exit 1
fi
echo "      설치 파일: $(basename "${DMG}")"

# 2) DMG 마운트 (마운트 지점을 파싱해서 정확히 잡는다)
echo "[2/4] DMG 마운트 중..."
MOUNT_OUTPUT="$(hdiutil attach "${DMG}" -nobrowse -noverify 2>/dev/null)"
MOUNT_POINT="$(echo "${MOUNT_OUTPUT}" | grep -Eo '/Volumes/[^"]+' | tail -n1)"
if [ -z "${MOUNT_POINT}" ] || [ ! -d "${MOUNT_POINT}" ]; then
  echo "오류: DMG 마운트에 실패했습니다."
  read -r -p "엔터를 눌러 종료합니다..." _ || true
  exit 1
fi

cleanup() {
  # 언마운트 (실패해도 무시)
  hdiutil detach "${MOUNT_POINT}" -quiet 2>/dev/null || true
}
trap cleanup EXIT

SRC_APP="${MOUNT_POINT}/${APP_NAME}"
if [ ! -d "${SRC_APP}" ]; then
  # 앱 이름이 다를 수 있으니 .app을 탐색
  SRC_APP="$(find "${MOUNT_POINT}" -maxdepth 1 -name '*.app' -print -quit 2>/dev/null || true)"
fi
if [ -z "${SRC_APP}" ] || [ ! -d "${SRC_APP}" ]; then
  echo "오류: DMG 안에서 .app을 찾지 못했습니다."
  read -r -p "엔터를 눌러 종료합니다..." _ || true
  exit 1
fi

# 3) 기존 설치본 제거 후 /Applications로 복사
echo "[3/4] /Applications 로 복사 중... (기존 버전이 있으면 교체)"
if [ -d "${DEST}" ]; then
  rm -rf "${DEST}"
fi
cp -R "${SRC_APP}" "/Applications/"

# 4) quarantine 제거 + ad-hoc 서명 (Gatekeeper 우회 + "손상됨" 예방)
echo "[4/4] Gatekeeper quarantine 제거 + 서명 정리 중..."
xattr -dr com.apple.quarantine "${DEST}" 2>/dev/null || true
# ad-hoc 재서명 — Apple Silicon에서 미서명 실행 시 "손상되었습니다" 오류를 예방.
# (Developer ID 서명/공증과는 다르며, 로컬 신뢰만 부여한다.)
codesign --force --deep --sign - "${DEST}" 2>/dev/null || true

echo ""
echo "==============================================="
echo "  ✓ 설치 완료"
echo "  실행: Launchpad 또는 응용 프로그램 폴더에서 'Mogam Works' 실행"
echo "==============================================="
echo ""
read -r -p "엔터를 눌러 종료합니다..." _ || true
