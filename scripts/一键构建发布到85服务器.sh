#!/usr/bin/env bash
set -Eeuo pipefail

# 选择本机可用的 UTF-8 区域，避免 macOS tar 对中文文件名转义并输出告警。
if locale -a 2>/dev/null | grep -Eqi '^zh_CN\.UTF-8$'; then
  export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8
else
  export LANG=C LC_ALL=C
fi

# 本脚本只负责安全打包和调度远端版本化发布；不会生成、上传或打印任何密钥。
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${CONFIG_FILE:-${PROJECT_ROOT}/配置/85发布配置.env}"
if [[ -f "${CONFIG_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${CONFIG_FILE}"
  set +a
fi
REMOTE_TARGET="${REMOTE_TARGET:-root@118.145.230.85}"
PUBLIC_PREFIX="${PUBLIC_PREFIX:-https://yomitest.gwcz.online/chat/}"
REMOTE_BASE="${REMOTE_BASE:-/opt/xiaozhi-all}"
REMOTE_ENV_FILE="${REMOTE_ENV_FILE:-/opt/xiaozhi-all/shared/.env}"
HOST_BINDING="127.0.0.1:18766:18765"
REMOTE_RUNNER_LOCAL="${PROJECT_ROOT}/scripts/远端版本发布执行器.sh"
MODE=""
OUTPUT_ARCHIVE=""
TEMP_DIR=""
ALLOW_DIRTY="${ALLOW_DIRTY:-1}"
CODE_ONLY_RELEASE="${CODE_ONLY_RELEASE:-1}"
RETAIN_RELEASE_COUNT="${RETAIN_RELEASE_COUNT:-3}"
H5_CHAT_PREFIX="${H5_CHAT_PREFIX:-/chat/}"
H5_WEBSOCKET_ROUTE="${H5_WEBSOCKET_ROUTE:-/xiaozhi/v1/ws}"
LOCAL_THIRD_PARTY_CACHE="${XIAOZHI_ALL_THIRD_PARTY_CACHE:-${PROJECT_ROOT}/.third-party-cache/h5-voice}"
REMOTE_THIRD_PARTY_CACHE="${REMOTE_BASE}/shared/third-party-cache/h5-voice"
LIBOPUS_VERSION="1.5.2-2"
LIBOPUS_SHA256="794056db33d71b2ac4bd8b5a4eb23b627bcb8a49d123c33b6e841a996253a067"

usage() {
  cat <<'EOF'
用法：
  scripts/一键构建发布到85服务器.sh
  scripts/一键构建发布到85服务器.sh --仅检查
  scripts/一键构建发布到85服务器.sh --仅清理版本包
  scripts/一键构建发布到85服务器.sh --仅打包 [--输出包 /tmp/xiaozhi-all.tgz]
  scripts/一键构建发布到85服务器.sh --发布

可选环境变量：
  ALLOW_DIRTY=0          禁止从有未提交改动的工作区发布；默认允许发布当前工作区
  CODE_ONLY_RELEASE=1    复用当前运行镜像的依赖层，仅发布应用代码（默认）
  CODE_ONLY_RELEASE=0    仅在依赖清单变化时显式执行完整依赖镜像构建
  REMOTE_TARGET=...      覆盖远端目标，默认 root@118.145.230.85
  REMOTE_BASE=...        覆盖远端版本根目录，默认 /opt/xiaozhi-all
  REMOTE_ENV_FILE=...    覆盖远端密钥文件，默认 /opt/xiaozhi-all/shared/.env
  PUBLIC_PREFIX=...      覆盖公网验收前缀
  CONFIG_FILE=...        覆盖发布配置文件，默认 配置/85发布配置.env
  H5_WEBSOCKET_ROUTE=... H5 后端 WebSocket 路由，默认 /xiaozhi/v1/ws

安全说明：
  不带参数会直接一键发布到 85 环境，并自动完成候选启动、切换与验收。
  --仅检查 和 --仅打包 绝不会连接远端；只有 --发布 会执行 ssh/scp。
EOF
}

fail() {
  echo "错误：$*" >&2
  exit 1
}

cleanup() {
  if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
    rm -rf "${TEMP_DIR}"
  fi
}
trap cleanup EXIT

need_command() {
  command -v "$1" >/dev/null 2>&1 || fail "本机缺少命令：$1"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

preflight() {
  need_command bash
  need_command git
  need_command tar
  need_command python3
  need_command grep
  need_command shasum

  [[ -f "${PROJECT_ROOT}/pyproject.toml" ]] || fail "项目根目录缺少 pyproject.toml"
  [[ -f "${PROJECT_ROOT}/apps/realtime-server/src/realtime_server/demo.py" ]] || fail "缺少实时服务入口"
  [[ -f "${PROJECT_ROOT}/apps/h5-demo/index.html" ]] || fail "缺少 H5 页面"
  [[ -f "${PROJECT_ROOT}/apps/h5-demo/运行配置.js" ]] || fail "缺少 H5 运行配置文件"
  [[ -x "${REMOTE_RUNNER_LOCAL}" ]] || fail "远端发布执行器不存在或不可执行"
  [[ "${H5_CHAT_PREFIX}" == /*/ ]] || fail "H5_CHAT_PREFIX 必须以 / 开头和结尾"
  [[ "${H5_WEBSOCKET_ROUTE}" == /* ]] || fail "H5_WEBSOCKET_ROUTE 必须以 / 开头"

  if [[ "${MODE}" == "发布" ]]; then
    need_command ssh
    need_command scp
    if [[ -n "$(git -C "${PROJECT_ROOT}" status --porcelain)" && "${ALLOW_DIRTY}" != "1" ]]; then
      fail "工作区存在未提交改动；确认确需发布后使用 ALLOW_DIRTY=1"
    fi
  fi

  # 阻止构建上下文中的符号链接逃逸到项目目录之外。
  python3 - "${PROJECT_ROOT}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
for top in ("apps", "gateways", "packages", "services"):
    base = root / top
    if not base.exists():
        continue
    for path in base.rglob("*"):
        if path.is_symlink():
            target = path.resolve()
            try:
                target.relative_to(root)
            except ValueError:
                raise SystemExit(f"错误：符号链接越过项目边界：{path}")
PY

  echo "本地预检通过：项目结构、命令和符号链接均符合要求"
}

prepare_local_third_party_cache() {
  local source_dir="${PROJECT_ROOT}/apps/h5-demo/vendor/voice"
  [[ -d "${source_dir}" ]] || fail "缺少 H5 第三方语音资源目录：${source_dir}"
  mkdir -p "${LOCAL_THIRD_PARTY_CACHE}"
  rsync -a --delete --exclude='.DS_Store' "${source_dir}/" "${LOCAL_THIRD_PARTY_CACHE}/" 2>/dev/null \
    || cp -Rp "${source_dir}/." "${LOCAL_THIRD_PARTY_CACHE}/"
  find "${LOCAL_THIRD_PARTY_CACHE}" -type f -not -name '.DS_Store' -print -quit | grep -q . \
    || fail "第三方语音资源缓存为空"
  echo "本地第三方语音资源缓存就绪：${LOCAL_THIRD_PARTY_CACHE}"
}

write_docker_files() {
  local stage="$1"

  # 从项目声明读取直接依赖；pip 会正常解析 pipecat-ai 的完整传递依赖。
  python3 - "${PROJECT_ROOT}/pyproject.toml" "${stage}/requirements-release.txt" <<'PY'
from pathlib import Path
import sys
import tomllib

source = Path(sys.argv[1])
target = Path(sys.argv[2])
dependencies = tomllib.loads(source.read_text(encoding="utf-8"))["project"]["dependencies"]
extra = ["python-dotenv>=1,<2", "pillow>=10,<13"]
target.write_text("\n".join([*dependencies, *extra]) + "\n", encoding="utf-8")
PY

  cat > "${stage}/constraints-release.txt" <<'EOF'
openai>=1.74,<3
protobuf<7
soxr~=1.0.0
pillow>=10,<13
EOF

  cat > "${stage}/Dockerfile" <<'EOF'
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/apps/realtime-server/src:/app/gateways/xiaozhi-websocket/src:/app/services/model-router/src:/app/services/speech-router/src:/app/services/voice-session-runtime/src:/app/packages/realtime-protocol/src:/app/packages/audio-codec/src:/app/packages/provider-contracts/src:/app/packages/observability/src

WORKDIR /app

COPY requirements-release.txt constraints-release.txt /tmp/
RUN --mount=type=cache,target=/var/cache/xiaozhi,sharing=locked \
    if ldconfig -p | grep -q 'libopus.so'; then echo 'libopus 已存在，复用镜像缓存'; else \
    if [ -f /var/cache/xiaozhi/libopus0_1.5.2-2_amd64.v2.deb ] && [ "$(stat -c %s /var/cache/xiaozhi/libopus0_1.5.2-2_amd64.v2.deb)" = 2851932 ]; then echo 'libopus 安装包命中缓存'; else python -c "import os,urllib.request; p='/var/cache/xiaozhi/libopus0_1.5.2-2_amd64.v2.deb'; u=urllib.request.build_opener(urllib.request.ProxyHandler({})); r=u.open('https://deb.debian.org/debian/pool/main/o/opus/libopus0_1.5.2-2_amd64.deb', timeout=60); d=r.read(); assert len(d)==2851932, len(d); open(p+'.tmp','wb').write(d); os.replace(p+'.tmp',p)"; fi \
    && dpkg -i /var/cache/xiaozhi/libopus0_1.5.2-2_amd64.v2.deb; fi
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --upgrade "pip>=24,<27" \
    && python -m pip install --no-cache-dir \
       -r /tmp/requirements-release.txt \
       -c /tmp/constraints-release.txt

COPY apps ./apps
COPY gateways ./gateways
COPY packages ./packages
COPY services ./services
COPY third-party-cache ./apps/h5-demo/vendor/voice
COPY pyproject.toml ./pyproject.toml

EXPOSE 18765
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18765/', timeout=2).read(64)" || exit 1

CMD ["python", "-c", "from realtime_server.combined_server import main; main()"]
EOF
}

create_archive() {
  local release_id="$1"
  local archive="$2"
  local stage="${TEMP_DIR}/构建上下文"
  mkdir -p "${stage}"
  prepare_local_third_party_cache

  # 只收集运行所需目录，主动排除密钥、证书、缓存、测试和本地产物。
  tar --no-xattrs -C "${PROJECT_ROOT}" -cf - \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='certs' \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='.runtime' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='.DS_Store' \
    --exclude='*/tests' \
    --exclude='*/tests/*' \
    --exclude='apps/h5-demo/vendor/voice' \
    apps gateways packages services pyproject.toml \
    | tar -C "${stage}" -xf -

  write_docker_files "${stage}"

  local commit dirty
  commit="$(git -C "${PROJECT_ROOT}" rev-parse --short=12 HEAD 2>/dev/null || echo '无提交')"
  dirty="否"
  [[ -n "$(git -C "${PROJECT_ROOT}" status --porcelain 2>/dev/null || true)" ]] && dirty="是"
  cat > "${stage}/发布信息.txt" <<EOF
版本标识：${release_id}
Git 提交：${commit}
包含未提交改动：${dirty}
构建时间：$(date '+%Y-%m-%d %H:%M:%S %z')
容器启动命令：python -c "from realtime_server.combined_server import main; main()"
宿主监听：${HOST_BINDING}
公网前缀：${PUBLIC_PREFIX}
H5 WebSocket 路由：${H5_WEBSOCKET_ROUTE}
EOF

  # 在压缩前扫描常见私钥和云模型密钥形态；只输出文件名，不输出命中内容。
  local secret_files
  secret_files="$(grep -RIlE \
    '(-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}\b|Bearer[[:space:]]+[A-Za-z0-9._-]{24,})' \
    "${stage}" 2>/dev/null || true)"
  if [[ -n "${secret_files}" ]]; then
    echo "检测到疑似密钥，已阻止打包，涉及文件：" >&2
    printf '%s\n' "${secret_files}" >&2
    exit 1
  fi

  mkdir -p "$(dirname "${archive}")"
  tar --no-xattrs -C "${stage}" -czf "${archive}" .

  local archive_list="${TEMP_DIR}/发布包清单.txt"
  tar -tzf "${archive}" > "${archive_list}"
  if grep -Eq '(^|/)(\.env($|\.)|certs/|\.git/|\.venv/|\.runtime/|__pycache__/|\.pytest_cache/)' "${archive_list}"; then
    fail "发布包复检发现密钥或垃圾文件"
  fi

  echo "发布包已生成：${archive}"
  echo "发布包 SHA-256：$(sha256_file "${archive}")"
}

publish_archive() {
  local release_id="$1"
  local archive="$2"
  local checksum="$3"
  local remote_archive="/tmp/xiaozhi-all-${release_id}.tgz"
  local remote_runner="/tmp/xiaozhi-all-远端发布-${release_id}.sh"

  echo "开始上传发布包和远端执行器；不会上传本地 .env 或证书"
  scp -- "${archive}" "${REMOTE_TARGET}:${remote_archive}"
  scp -- "${REMOTE_RUNNER_LOCAL}" "${REMOTE_TARGET}:${remote_runner}"
  ensure_remote_libopus_cache
  sync_remote_third_party_cache

  echo "开始远端版本化发布：${release_id}"
  ssh -- "${REMOTE_TARGET}" env \
    RELEASE_ID="${release_id}" \
    ARCHIVE_PATH="${remote_archive}" \
    ARCHIVE_SHA256="${checksum}" \
    REMOTE_BASE="${REMOTE_BASE}" \
    REMOTE_ENV_FILE="${REMOTE_ENV_FILE}" \
    PUBLIC_PREFIX="${PUBLIC_PREFIX}" \
    H5_WEBSOCKET_ROUTE="${H5_WEBSOCKET_ROUTE}" \
    CODE_ONLY_RELEASE="${CODE_ONLY_RELEASE:-1}" \
    bash "${remote_runner}"
}

sync_remote_third_party_cache() {
  local relative remote_hash local_hash
  ssh -- "${REMOTE_TARGET}" "mkdir -p '${REMOTE_THIRD_PARTY_CACHE}'"
  while IFS= read -r -d '' file; do
    relative="${file#${LOCAL_THIRD_PARTY_CACHE}/}"
    remote_hash="$(ssh -- "${REMOTE_TARGET}" "if [ -f '${REMOTE_THIRD_PARTY_CACHE}/${relative}' ]; then sha256sum '${REMOTE_THIRD_PARTY_CACHE}/${relative}' | cut -d' ' -f1; fi")"
    local_hash="$(sha256_file "${file}")"
    if [[ "${remote_hash}" != "${local_hash}" ]]; then
      ssh -- "${REMOTE_TARGET}" "mkdir -p '${REMOTE_THIRD_PARTY_CACHE}/$(dirname "${relative}")'"
      scp -- "${file}" "${REMOTE_TARGET}:${REMOTE_THIRD_PARTY_CACHE}/${relative}"
      echo "第三方缓存已补传：${relative}"
    else
      echo "第三方缓存已命中：${relative}"
    fi
  done < <(find "${LOCAL_THIRD_PARTY_CACHE}" -type f -not -name '.DS_Store' -print0)
}

ensure_remote_libopus_cache() {
  local cache_root="${TMPDIR:-/tmp}/xiaozhi-all-third-party-cache"
  local package="${cache_root}/libopus0_${LIBOPUS_VERSION}_amd64.deb"
  local remote_package="${REMOTE_BASE}/shared/libopus0_${LIBOPUS_VERSION}_amd64.v2.deb"
  mkdir -p "${cache_root}"
  if [[ ! -f "${package}" ]] || [[ "$(shasum -a 256 "${package}" | awk '{print $1}')" != "${LIBOPUS_SHA256}" ]]; then
    rm -f -- "${package}.tmp"
    local mirror url
    for mirror in \
      "https://mirrors.aliyun.com/debian" \
      "https://mirrors.cloud.tencent.com/debian" \
      "https://mirrors.ustc.edu.cn/debian" \
      "https://mirrors.tuna.tsinghua.edu.cn/debian" \
      "https://deb.debian.org/debian"; do
      url="${mirror}/pool/main/o/opus/libopus0_${LIBOPUS_VERSION}_amd64.deb"
      if curl --noproxy '*' --fail --location --retry 2 --connect-timeout 10 --max-time 60 "${url}" -o "${package}.tmp" \
        && [[ "$(shasum -a 256 "${package}.tmp" | awk '{print $1}')" == "${LIBOPUS_SHA256}" ]]; then
        mv -- "${package}.tmp" "${package}"
        break
      fi
      rm -f -- "${package}.tmp"
    done
  fi
  [[ -f "${package}" ]] || fail "无法获得经过校验的 libopus0 安装包"
  [[ "$(shasum -a 256 "${package}" | awk '{print $1}')" == "${LIBOPUS_SHA256}" ]] || fail "libopus0 SHA-256 校验失败"
  local remote_sha
  remote_sha="$(ssh -- "${REMOTE_TARGET}" "if [ -f '${remote_package}' ]; then sha256sum '${remote_package}' | cut -d' ' -f1; fi")"
  if [[ "${remote_sha}" != "${LIBOPUS_SHA256}" ]]; then
    scp -- "${package}" "${REMOTE_TARGET}:${remote_package}"
    echo "libopus0 已通过国内镜像校验并写入 85 持久化缓存"
  else
    echo "libopus0 已命中 85 持久化缓存，跳过下载和上传"
  fi
}

cleanup_local_archives() {
  local archive_root candidate basename resolved
  archive_root="$(cd /tmp && pwd -P)"
  [[ -n "${archive_root}" && -d "${archive_root}" ]] || fail "本地临时目录不存在"
  [[ "${RETAIN_RELEASE_COUNT}" =~ ^[1-9][0-9]*$ ]] || fail "本地保留版本数量非法"

  find "${archive_root}" -mindepth 1 -maxdepth 1 -type f -name 'xiaozhi-all-*.tgz' -print \
    | sort -r \
    | awk -v keep="${RETAIN_RELEASE_COUNT}" '
        /\/xiaozhi-all-[0-9]{14}-[A-Za-z0-9._-]+\.tgz$/ {valid += 1; if (valid > keep) print; next}
        {print}
      ' \
    | while IFS= read -r candidate; do
        basename="${candidate##*/}"
        [[ "${basename}" == xiaozhi-all-*.tgz ]] || fail "拒绝清理非本项目发布包：${candidate}"
        resolved="$(cd "$(dirname "${candidate}")" && pwd -P)/${basename}"
        [[ "${resolved}" == "${archive_root}/"* ]] || fail "拒绝清理临时目录外发布包：${candidate}"
        rm -f -- "${resolved}"
      done

  echo "本地发布包清理完成：仅保留最近 ${RETAIN_RELEASE_COUNT} 个版本"
}

if [[ $# -eq 0 ]]; then
  MODE="发布"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --仅检查) MODE="仅检查"; shift ;;
    --仅清理版本包) MODE="仅清理版本包"; shift ;;
    --仅打包) MODE="仅打包"; shift ;;
    --发布) MODE="发布"; shift ;;
    --输出包)
      [[ $# -ge 2 ]] || fail "--输出包 缺少路径"
      OUTPUT_ARCHIVE="$2"
      shift 2
      ;;
    --帮助|-h|--help) usage; exit 0 ;;
    *) fail "未知参数：$1" ;;
  esac
done

[[ -n "${MODE}" ]] || { usage; exit 1; }

TEMP_DIR="$(mktemp -d)"
preflight
[[ "${MODE}" == "仅检查" ]] && exit 0
if [[ "${MODE}" == "仅清理版本包" ]]; then
  cleanup_local_archives
  exit 0
fi

COMMIT_ID="$(git -C "${PROJECT_ROOT}" rev-parse --short=12 HEAD 2>/dev/null || echo 'no-commit')"
RELEASE_ID="$(date '+%Y%m%d%H%M%S')-${COMMIT_ID}"
[[ -n "${OUTPUT_ARCHIVE}" ]] || OUTPUT_ARCHIVE="/tmp/xiaozhi-all-${RELEASE_ID}.tgz"
create_archive "${RELEASE_ID}" "${OUTPUT_ARCHIVE}"

if [[ "${MODE}" == "发布" ]]; then
  CHECKSUM="$(sha256_file "${OUTPUT_ARCHIVE}")"
  publish_archive "${RELEASE_ID}" "${OUTPUT_ARCHIVE}" "${CHECKSUM}"
  cleanup_local_archives
  echo "发布及公网验收完成：${PUBLIC_PREFIX}"
else
  echo "仅打包模式完成，未连接任何远端服务器"
fi
