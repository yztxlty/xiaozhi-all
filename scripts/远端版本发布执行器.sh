#!/usr/bin/env bash
set -Eeuo pipefail

# 本脚本由本地发布入口上传后执行；不创建、不修改、不重载任何 Nginx 配置。
: "${RELEASE_ID:?缺少 RELEASE_ID}"
: "${ARCHIVE_PATH:?缺少 ARCHIVE_PATH}"
: "${ARCHIVE_SHA256:?缺少 ARCHIVE_SHA256}"
: "${REMOTE_BASE:?缺少 REMOTE_BASE}"
: "${REMOTE_ENV_FILE:?缺少 REMOTE_ENV_FILE}"
: "${PUBLIC_PREFIX:?缺少 PUBLIC_PREFIX}"
: "${H5_WEBSOCKET_ROUTE:?缺少 H5_WEBSOCKET_ROUTE}"

CODE_ONLY_RELEASE="${CODE_ONLY_RELEASE:-1}"
RETAIN_RELEASE_COUNT="${RETAIN_RELEASE_COUNT:-3}"

CONTAINER_NAME="xiaozhi-h5-voice-85"
IMAGE_NAME="xiaozhi-all-realtime:${RELEASE_ID}"
RELEASE_DIR="${REMOTE_BASE}/releases/${RELEASE_ID}"
SOURCE_DIR="${REMOTE_BASE}/source"
CURRENT_LINK="${REMOTE_BASE}/current"
CANDIDATE_NAME="${CONTAINER_NAME}-candidate-${RELEASE_ID}"
BACKUP_NAME="${CONTAINER_NAME}-backup-${RELEASE_ID}"
REMOTE_RUNNER_PATH="$0"
THIRD_PARTY_CACHE_DIR="${REMOTE_BASE}/shared/third-party-cache/h5-voice"
ROLLBACK_ARMED=0
PREVIOUS_LINK=""
OLD_CONTAINER_PRESENT=0

fail() {
  echo "错误：$*" >&2
  return 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || fail "远端缺少命令：$1"
}

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

wait_healthy() {
  local name="$1"
  local attempt status
  for attempt in $(seq 1 30); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${name}" 2>/dev/null || echo missing)"
    if [[ "${status}" == "healthy" ]]; then
      echo "容器健康检查通过：${name}"
      return 0
    fi
    if [[ "${status}" == "exited" || "${status}" == "dead" || "${status}" == "missing" || "${status}" == "unhealthy" ]]; then
      echo "容器状态异常：${name}=${status}" >&2
      docker logs --tail 80 "${name}" 2>&1 || true
      return 1
    fi
    sleep 2
  done
  echo "容器健康检查超时：${name}" >&2
  docker logs --tail 80 "${name}" 2>&1 || true
  return 1
}

restore_previous() {
  echo "发布失败，开始恢复上一版本" >&2
  set +e
  if container_exists "${CONTAINER_NAME}"; then
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1
  fi
  if [[ "${OLD_CONTAINER_PRESENT}" == "1" ]] && container_exists "${BACKUP_NAME}"; then
    docker rename "${BACKUP_NAME}" "${CONTAINER_NAME}"
    docker start "${CONTAINER_NAME}" >/dev/null
    wait_healthy "${CONTAINER_NAME}" || true
  fi
  if [[ -n "${PREVIOUS_LINK}" && -e "${PREVIOUS_LINK}" ]]; then
    ln -sfn "${PREVIOUS_LINK}" "${CURRENT_LINK}"
  elif [[ -L "${CURRENT_LINK}" ]]; then
    rm -f "${CURRENT_LINK}"
  fi
  set -e
}

finish() {
  local code=$?
  set +e
  if container_exists "${CANDIDATE_NAME}"; then
    docker rm -f "${CANDIDATE_NAME}" >/dev/null 2>&1
  fi
  if [[ "${code}" != "0" && "${ROLLBACK_ARMED}" == "1" ]]; then
    restore_previous
  fi
  rm -f "${ARCHIVE_PATH}" "${REMOTE_RUNNER_PATH}"
  exit "${code}"
}
trap finish EXIT

validate_inputs() {
  [[ "${RELEASE_ID}" =~ ^[0-9]{14}-[A-Za-z0-9._-]+$ ]] || fail "版本标识格式非法"
  [[ "${ARCHIVE_SHA256}" =~ ^[0-9a-fA-F]{64}$ ]] || fail "SHA-256 格式非法"
  [[ "${PUBLIC_PREFIX}" == https://* ]] || fail "公网前缀必须使用 HTTPS"
  [[ "${PUBLIC_PREFIX}" == */ ]] || fail "公网前缀必须以斜杠结尾"
  [[ "${H5_WEBSOCKET_ROUTE}" == /* ]] || fail "H5 WebSocket 路由必须以斜杠开头"
  [[ -f "${ARCHIVE_PATH}" ]] || fail "远端发布包不存在"
  [[ -f "${REMOTE_ENV_FILE}" ]] || fail "远端密钥文件不存在：${REMOTE_ENV_FILE}"
  [[ ! -L "${REMOTE_ENV_FILE}" ]] || fail "远端密钥文件不得是符号链接"

  need_command docker
  need_command curl
  need_command nginx
  need_command sha256sum
  need_command tar
  need_command grep

  docker info >/dev/null
  nginx -t
  echo "远端预检通过：Docker、密钥文件和现有 Nginx 配置正常"
}

verify_archive() {
  local actual
  actual="$(sha256sum "${ARCHIVE_PATH}" | awk '{print $1}')"
  [[ "${actual}" == "${ARCHIVE_SHA256}" ]] || fail "发布包 SHA-256 校验失败"

  if tar -tzf "${ARCHIVE_PATH}" | grep -Eq '(^|/)(\.env($|\.)|certs/|\.git/|\.venv/|\.runtime/|__pycache__/|\.pytest_cache/)'; then
    fail "发布包包含密钥或垃圾文件"
  fi
  echo "发布包完整性和安全清单检查通过"
}

prepare_release() {
  mkdir -p "${REMOTE_BASE}/releases" "${REMOTE_BASE}/shared"
  [[ ! -e "${RELEASE_DIR}" ]] || fail "版本目录已存在，拒绝覆盖：${RELEASE_DIR}"
  mkdir "${RELEASE_DIR}"
  tar -xzf "${ARCHIVE_PATH}" -C "${RELEASE_DIR}"
  [[ -f "${RELEASE_DIR}/Dockerfile" ]] || fail "版本目录缺少 Dockerfile"
  [[ -d "${THIRD_PARTY_CACHE_DIR}" ]] || fail "85 第三方资源缓存不存在：${THIRD_PARTY_CACHE_DIR}"
  mkdir -p "${RELEASE_DIR}/third-party-cache"
  cp -Rp "${THIRD_PARTY_CACHE_DIR}/." "${RELEASE_DIR}/third-party-cache/"
  find "${RELEASE_DIR}/third-party-cache" -type f -not -name '.DS_Store' -print -quit | grep -q . \
    || fail "85 第三方资源缓存为空"

  # 正常解析全部传递依赖，不允许使用 --no-deps。
  if grep -Fq -- '--no-deps' "${RELEASE_DIR}/Dockerfile"; then
    fail "Dockerfile 禁止使用 --no-deps"
  fi
  grep -Fq 'pipecat-ai==1.8.1' "${RELEASE_DIR}/requirements-release.txt" || fail "缺少 pipecat 固定版本"
  grep -Fq 'openai>=1.74,<3' "${RELEASE_DIR}/constraints-release.txt" || fail "缺少 openai 约束"
  grep -Fq 'protobuf<7' "${RELEASE_DIR}/constraints-release.txt" || fail "缺少 protobuf 约束"
  grep -Fq 'soxr~=1.0.0' "${RELEASE_DIR}/constraints-release.txt" || fail "缺少 soxr 约束"
  grep -Fqi 'pillow' "${RELEASE_DIR}/requirements-release.txt" || fail "缺少 Pillow 依赖"
}

build_and_probe_candidate() {
  echo "开始构建镜像：${IMAGE_NAME}"
  if [[ "${CODE_ONLY_RELEASE}" == "1" ]]; then
    local base_image base_tag flat_base_tag code_only_dockerfile history_count
    container_exists "${CONTAINER_NAME}" || fail "代码快速发布要求当前运行容器存在"
    base_image="$(docker inspect --format '{{.Image}}' "${CONTAINER_NAME}")"
    [[ "${base_image}" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "当前运行镜像 ID 非法"
    base_tag="xiaozhi-all-code-base:${RELEASE_ID}"
    history_count="$(docker history --no-trunc "${base_image}" 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${history_count}" -gt 120 ]]; then
      flat_base_tag="xiaozhi-all-flat-base:${RELEASE_ID}"
      echo "当前依赖镜像层数为 ${history_count}，先扁平化运行中的依赖层，不重新安装依赖"
      docker export "${CONTAINER_NAME}" | docker import \
        --change 'ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/apps/realtime-server/src:/app/gateways/xiaozhi-websocket/src:/app/services/model-router/src:/app/services/speech-router/src:/app/services/voice-session-runtime/src:/app/packages/realtime-protocol/src:/app/packages/audio-codec/src:/app/packages/provider-contracts/src:/app/packages/observability/src' \
        --change 'WORKDIR /app' - "${flat_base_tag}" >/dev/null
      base_tag="${flat_base_tag}"
    else
      docker tag "${base_image}" "${base_tag}"
    fi
    code_only_dockerfile="${RELEASE_DIR}/Dockerfile.code-only"
    local libopus_package="${REMOTE_BASE}/shared/libopus0_1.5.2-2_amd64.v2.deb"
    [[ -f "${libopus_package}" ]] || fail "共享缓存缺少 libopus 安装包：${libopus_package}"
    cp -- "${libopus_package}" "${RELEASE_DIR}/libopus0_1.5.2-2_amd64.v2.deb"
    cat > "${code_only_dockerfile}" <<EOF
# syntax=docker/dockerfile:1.7
FROM ${base_tag}
WORKDIR /app
USER root
COPY libopus0_1.5.2-2_amd64.v2.deb /tmp/
RUN if ldconfig -p | grep -q 'libopus.so'; then echo 'libopus 已存在，复用镜像层'; else dpkg -i /tmp/libopus0_1.5.2-2_amd64.v2.deb; fi
COPY apps ./apps
COPY gateways ./gateways
COPY packages ./packages
COPY services ./services
COPY pyproject.toml ./pyproject.toml
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18765/', timeout=2).read(64)" || exit 1
CMD ["python", "-c", "from realtime_server.combined_server import main; main()"]
EOF
    docker build --tag "${IMAGE_NAME}" --file "${code_only_dockerfile}" "${RELEASE_DIR}"
    docker image rm "${base_tag}" >/dev/null
  else
    docker build --pull --tag "${IMAGE_NAME}" "${RELEASE_DIR}"
  fi

  docker run -d \
    --name "${CANDIDATE_NAME}" \
    --env-file "${REMOTE_ENV_FILE}" \
    --restart no \
    -p 127.0.0.1::18765 \
    "${IMAGE_NAME}" >/dev/null
  wait_healthy "${CANDIDATE_NAME}"
  docker rm -f "${CANDIDATE_NAME}" >/dev/null
  echo "候选镜像启动与健康检查通过"
}

switch_container() {
  if [[ -L "${CURRENT_LINK}" ]]; then
    PREVIOUS_LINK="$(readlink -f "${CURRENT_LINK}" || true)"
  fi

  if container_exists "${CONTAINER_NAME}"; then
    OLD_CONTAINER_PRESENT=1
    docker stop "${CONTAINER_NAME}" >/dev/null
    docker rename "${CONTAINER_NAME}" "${BACKUP_NAME}"
  fi
  ROLLBACK_ARMED=1

  docker run -d \
    --name "${CONTAINER_NAME}" \
    --env-file "${REMOTE_ENV_FILE}" \
    --restart unless-stopped \
    -p 127.0.0.1:18766:18765 \
    "${IMAGE_NAME}" >/dev/null

  wait_healthy "${CONTAINER_NAME}"
  ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}"
}

verify_local_service() {
  local base="http://127.0.0.1:18766"
  local page_file="/tmp/xiaozhi-all-local-page-${RELEASE_ID}.html"
  local app_file="/tmp/xiaozhi-all-local-app-${RELEASE_ID}.js"
  local css_file="/tmp/xiaozhi-all-local-style-${RELEASE_ID}.css"
  local wasm_headers="/tmp/xiaozhi-all-local-wasm-${RELEASE_ID}.headers"
  curl -fsS --max-time 10 "${base}/" -o "${page_file}"
  curl -fsS --max-time 10 "${base}/app.js" -o "${app_file}"
  curl -fsS --max-time 10 "${base}/app.css" -o "${css_file}"
  grep -Fq '幽光 AI' "${page_file}"
  grep -Fq 'WebSocket' "${app_file}"
  curl -fsS --max-time 10 "${base}/call-core.js" | grep -Fq 'buildWebSocketUrl'
  grep -Fq '.app-shell' "${css_file}"
  curl -fsS --max-time 15 "${base}/vendor/voice/silero_vad_v5.onnx" -o /dev/null
  curl -fsS --compressed --max-time 30 -D "${wasm_headers}" \
    "${base}/vendor/voice/ort-wasm-simd-threaded.wasm" -o /dev/null
  grep -Fiq 'Content-Encoding: gzip' "${wasm_headers}"
  rm -f "${page_file}" "${app_file}" "${css_file}" "${wasm_headers}"
  echo "宿主回环地址的页面、JavaScript 和 CSS 验收通过"
}

ensure_h5_chat_route() {
  local config_file='/etc/nginx/conf.d/yomitest.gwcz.online.conf'
  local backup_file="${config_file}.bak-${RELEASE_ID}"
  [[ -f "${config_file}" ]] || fail "缺少 yomitest Nginx 配置：${config_file}"

  # 仅修正 yomitest.gwcz.online 的 /chat 首页固定目录；资源和 WebSocket
  # 仍按既有配置处理，不触碰该站点的其他 location 或其他应用。
  cp -p -- "${config_file}" "${backup_file}"
  SOURCE_DIR="${SOURCE_DIR}" python3 - "${config_file}" <<'PY'
from pathlib import Path
import os
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding='utf-8')
original = text

text, count = re.subn(
    r'''(location = /chat/ \{\s*\n\s*alias )[^;]+(;)''',
    rf'''\g<1>{os.environ["SOURCE_DIR"]}/apps/h5-demo/\g<2>''',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    if re.search(r'location \^~ /chat/ \{', text):
        print('using existing yomitest /chat/ proxy; static assets are served by combined service')
        raise SystemExit(0)
    raise SystemExit('未找到可用的 yomitest /chat/ 路由')

if text == original:
    raise SystemExit('Nginx /chat 路由未发生预期变化')
path.write_text(text, encoding='utf-8')
PY
  nginx -t
  nginx -s reload
  rm -f -- "${backup_file}"
  echo "已固定 yomitest /chat 首页目录：/opt/xiaozhi-h5-voice-85/source"
}

publish_fixed_source() {
  local source_backup="${SOURCE_DIR}.bak-${RELEASE_ID}"
  if [[ -e "${SOURCE_DIR}" || -L "${SOURCE_DIR}" ]]; then
    mv -- "${SOURCE_DIR}" "${source_backup}"
  fi
  ln -s -- "${RELEASE_DIR}" "${SOURCE_DIR}"
  rm -rf -- "${source_backup}"
  echo "固定发布目录已切换：${SOURCE_DIR} -> ${RELEASE_DIR}"
}

verify_public_service() {
  local page_file="/tmp/xiaozhi-all-page-${RELEASE_ID}.html"
  local app_file="/tmp/xiaozhi-all-app-${RELEASE_ID}.js"
  local core_file="/tmp/xiaozhi-all-core-${RELEASE_ID}.js"
  local css_file="/tmp/xiaozhi-all-style-${RELEASE_ID}.css"
  local mascot_file="/tmp/xiaozhi-all-mascot-${RELEASE_ID}.js"
  local mascot_controller_file="/tmp/xiaozhi-all-mascot-controller-${RELEASE_ID}.js"
  local wasm_headers="/tmp/xiaozhi-all-wasm-${RELEASE_ID}.headers"
  curl -fsS --max-time 15 "${PUBLIC_PREFIX}" -o "${page_file}"
  curl -fsS --max-time 15 "${PUBLIC_PREFIX}app.js" -o "${app_file}"
  curl -fsS --max-time 15 "${PUBLIC_PREFIX}call-core.js" -o "${core_file}"
  curl -fsS --max-time 15 "${PUBLIC_PREFIX}app.css" -o "${css_file}"
  curl -fsS --max-time 15 "${PUBLIC_PREFIX}mascot-assets.js" -o "${mascot_file}"
  curl -fsS --max-time 15 "${PUBLIC_PREFIX}mascot-controller.js" -o "${mascot_controller_file}"
  grep -Fq '幽光 AI' "${page_file}"
  if grep -Eq '(src|href)="/(app|call-core|mascot-|assets/|vendor/)' "${page_file}"; then
    fail "子路径部署出现站点根路径资源，浏览器会绕过 ${PUBLIC_PREFIX}"
  fi
  grep -Fq 'href="./app.css"' "${page_file}"
  grep -Fq 'src="./app.js"' "${page_file}"
  grep -Fq 'WebSocket' "${app_file}"
  grep -Fq 'requestPreferredMicrophone' "${app_file}"
  grep -Fq 'buildWebSocketUrl' "${core_file}"
  grep -Fq '.app-shell' "${css_file}"
  grep -Fq 'youguang-base.png' "${mascot_file}"
  grep -Fq 'assistant.emotion' "${mascot_controller_file}"
  curl --retry 4 --retry-delay 2 -fsS --max-time 60 "${PUBLIC_PREFIX}assets/mascot/youguang-base.png" -o /dev/null
  local mascot_state mascot_index
  for mascot_state in neutral listening speaking laughing crying shy surprised sad; do
    for mascot_index in 1 2 3; do
      curl --retry 4 --retry-delay 2 -fsS --max-time 60 \
        "${PUBLIC_PREFIX}assets/mascot/layers/${mascot_state}-${mascot_index}.png" -o /dev/null
    done
  done
  echo "表情差分帧公网验收通过：8 组 × 3 帧"
  vad_headers="/tmp/xiaozhi-all-vad-${RELEASE_ID}.headers"
  curl --retry 4 --retry-delay 2 -fsS --compressed --max-time 180 -D "${vad_headers}" \
    "${PUBLIC_PREFIX}vendor/voice/silero_vad_v5.onnx" -o /dev/null
  grep -Fiq 'Content-Encoding: gzip' "${vad_headers}"
  curl --retry 4 --retry-delay 2 -fsS --compressed --max-time 120 -D "${wasm_headers}" \
    "${PUBLIC_PREFIX}vendor/voice/ort-wasm-simd-threaded.wasm" -o /dev/null
  grep -Fiq 'Content-Encoding: gzip' "${wasm_headers}"
  rm -f "${page_file}" "${app_file}" "${core_file}" "${css_file}" "${mascot_file}" "${mascot_controller_file}" "${wasm_headers}" "${vad_headers}"

  # 使用镜像内已安装的 websockets 发起真实公网 WSS 握手，不依赖宿主 Python 环境。
  docker run --rm --network host "${IMAGE_NAME}" python - "${PUBLIC_PREFIX}${H5_WEBSOCKET_ROUTE#/}" <<'PY'
import asyncio
import json
import sys

import websockets

url = sys.argv[1]
if url.startswith("https://"):
    url = "wss://" + url[len("https://"):]
elif url.startswith("http://"):
    url = "ws://" + url[len("http://"):]

async def main() -> None:
    async with websockets.connect(url, open_timeout=10, close_timeout=3) as ws:
        await ws.send(json.dumps({
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "audio_params": {
                "format": "pcm",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        }))
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if message.get("type") != "hello" or not message.get("session_id"):
            raise RuntimeError(f"WSS 握手响应不正确：{message}")

asyncio.run(main())
PY

  nginx -t
  echo "公网页面、JavaScript、CSS、WSS 和 Nginx 配置验收通过"
}

cleanup_old_versions() {
  local releases_root="${REMOTE_BASE}/releases"
  local name path resolved_root resolved_path index
  local -a image_tags keep_tags release_names backup_names

  [[ "${RETAIN_RELEASE_COUNT}" =~ ^[1-9][0-9]*$ ]] || fail "保留版本数量非法"
  resolved_root="$(readlink -f "${releases_root}")"
  [[ -n "${resolved_root}" && -d "${resolved_root}" ]] || fail "版本根目录不存在"

  mapfile -t backup_names < <(
    docker ps -a --format '{{.Names}}' \
      | grep -E "^${CONTAINER_NAME}-backup-[0-9]{14}-[A-Za-z0-9._-]+$" || true
  )
  for name in "${backup_names[@]}"; do
    docker rm -f -- "${name}" >/dev/null
  done

  mapfile -t image_tags < <(
    docker image ls 'xiaozhi-all-realtime' --format '{{.Repository}}|{{.Tag}}' \
      | awk -F '|' '$1 == "xiaozhi-all-realtime" {print $2}' \
      | grep -E '^[0-9]{14}-[A-Za-z0-9._-]+$' \
      | sort -ru || true
  )
  keep_tags=("${image_tags[@]:0:${RETAIN_RELEASE_COUNT}}")

  for ((index=RETAIN_RELEASE_COUNT; index<${#image_tags[@]}; index++)); do
    docker image rm -- "xiaozhi-all-realtime:${image_tags[index]}" >/dev/null
  done

  mapfile -t release_names < <(
    find "${releases_root}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' \
      | grep -E '^[0-9]{14}-[A-Za-z0-9._-]+$' \
      | sort -ru || true
  )
  for name in "${release_names[@]}"; do
    local retained=0
    for path in "${keep_tags[@]}"; do
      if [[ "${name}" == "${path}" ]]; then
        retained=1
        break
      fi
    done
    [[ "${retained}" == "1" ]] && continue

    path="${releases_root}/${name}"
    resolved_path="$(readlink -f "${path}")"
    [[ "${resolved_path}" == "${resolved_root}/"* ]] || fail "拒绝清理版本根目录外路径：${path}"
    rm -rf -- "${resolved_path}"
  done

  echo "历史版本清理完成：发布目录和 xiaozhi-all-realtime 镜像仅保留最近 ${RETAIN_RELEASE_COUNT} 个版本"
}

validate_inputs
verify_archive
prepare_release
build_and_probe_candidate
switch_container
verify_local_service
verify_public_service

ROLLBACK_ARMED=0
cleanup_old_versions
echo "版本发布成功：${RELEASE_ID}"
echo "当前版本目录：${RELEASE_DIR}"
echo "公网访问地址：${PUBLIC_PREFIX}"
