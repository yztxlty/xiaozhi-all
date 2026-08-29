#!/usr/bin/env bash
set -Eeuo pipefail

if locale -a 2>/dev/null | grep -Eqi '^zh_CN\.UTF-8$'; then
  export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8
else
  export LANG=C LC_ALL=C
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PUBLISH_SCRIPT="${PROJECT_ROOT}/scripts/一键构建发布到85服务器.sh"
REMOTE_RUNNER="${PROJECT_ROOT}/scripts/远端版本发布执行器.sh"
TEMP_DIR="$(mktemp -d)"
TEST_ARCHIVE="${TEMP_DIR}/发布包.tgz"

cleanup() {
  rm -rf "${TEMP_DIR}"
}
trap cleanup EXIT

fail() {
  echo "自检失败：$*" >&2
  exit 1
}

[[ -x "${PUBLISH_SCRIPT}" ]] || fail "本地发布脚本不存在或不可执行"
[[ -x "${REMOTE_RUNNER}" ]] || fail "远端执行器不存在或不可执行"

bash -n "${PUBLISH_SCRIPT}"
bash -n "${REMOTE_RUNNER}"

grep -Fq 'root@118.145.230.85' "${PUBLISH_SCRIPT}" || fail "缺少默认发布目标"
grep -Fq 'https://yomitest.gwcz.online/chat/' "${PUBLISH_SCRIPT}" || fail "缺少公网验收前缀"
grep -Fq '127.0.0.1:18766:18765' "${REMOTE_RUNNER}" || fail "缺少宿主机回环端口映射"
grep -Fq 'CODE_ONLY_RELEASE' "${PUBLISH_SCRIPT}" || fail "本地入口缺少代码快速发布开关"
grep -Fq 'CODE_ONLY_RELEASE="${CODE_ONLY_RELEASE:-1}"' "${PUBLISH_SCRIPT}" || fail "默认发布未复用现有依赖镜像"
grep -Fq '配置/85发布配置.env' "${PUBLISH_SCRIPT}" || fail "发布入口未加载可切换配置文件"
grep -Fq 'H5_WEBSOCKET_ROUTE' "${PUBLISH_SCRIPT}" || fail "发布入口缺少 H5 WebSocket 路由配置"
grep -Fq 'MODE="发布"' "${PUBLISH_SCRIPT}" || fail "零参数调用未默认执行完整发布"
grep -Fq 'tar --no-xattrs' "${PUBLISH_SCRIPT}" || fail "发布包未清除 macOS 扩展属性"
grep -Fq 'Dockerfile.code-only' "${REMOTE_RUNNER}" || fail "远端执行器缺少依赖层复用构建"
grep -Fq "docker inspect --format '{{.Image}}'" "${REMOTE_RUNNER}" || fail "代码快速发布未锁定当前运行镜像"
grep -Fq 'HEALTHCHECK --interval=5s' "${REMOTE_RUNNER}" || fail "代码快速发布缺少快速健康检查"
grep -Fq 'requestPreferredMicrophone' "${REMOTE_RUNNER}" || fail "公网验收缺少物理麦克风修复检查"
grep -Fq 'mascot-controller.js' "${REMOTE_RUNNER}" || fail "公网验收缺少角色控制器检查"
grep -Fq '子路径部署出现站点根路径资源' "${REMOTE_RUNNER}" || fail "公网验收未阻止 /chat/ 资源逃逸到站点根路径"
grep -Fq 'youguang-base.png' "${REMOTE_RUNNER}" || fail "公网验收缺少高清角色底图检查"
grep -Fq '表情差分帧公网验收通过' "${REMOTE_RUNNER}" || fail "公网验收未覆盖全部表情差分帧"
grep -Fq 'vendor/voice/ort-wasm-simd-threaded.wasm' "${REMOTE_RUNNER}" || fail "公网验收缺少本地语音模型资源检查"
grep -Fq -- '--compressed' "${REMOTE_RUNNER}" || fail "WASM 验收未启用 gzip 解压"
grep -Fq 'Content-Encoding: gzip' "${REMOTE_RUNNER}" || fail "WASM 验收未检查 gzip 响应头"
grep -Fq 'CONTAINER_NAME="xiaozhi-h5-voice-85"' "${REMOTE_RUNNER}" || fail "没有接管当前测试环境容器"
grep -Fq 'RETAIN_RELEASE_COUNT="${RETAIN_RELEASE_COUNT:-3}"' "${REMOTE_RUNNER}" || fail "未配置仅保留最近三个版本"
grep -Fq 'cleanup_old_versions' "${REMOTE_RUNNER}" || fail "发布成功后未清理旧版本"
grep -Fq 'xiaozhi-all-realtime:' "${REMOTE_RUNNER}" || fail "版本清理缺少专属镜像范围"
grep -Fq 'cleanup_local_archives' "${PUBLISH_SCRIPT}" || fail "发布成功后未轮转本地版本包"
grep -Fq 'RETAIN_RELEASE_COUNT="${RETAIN_RELEASE_COUNT:-3}"' "${PUBLISH_SCRIPT}" || fail "本地版本包未配置仅保留最近三个"
grep -Fq 'nginx -t' "${REMOTE_RUNNER}" || fail "缺少 Nginx 配置检查"
grep -Fq 'H5_WEBSOCKET_ROUTE' "${REMOTE_RUNNER}" || fail "远端验收缺少可切换 WebSocket 路由"
grep -Fq 'from realtime_server.combined_server import main; main()' "${PUBLISH_SCRIPT}" || fail "Docker CMD 不符合要求"
grep -Fq 'openai>=1.74,<3' "${PUBLISH_SCRIPT}" || fail "缺少 openai 兼容约束"
grep -Fq 'protobuf<7' "${PUBLISH_SCRIPT}" || fail "缺少 protobuf 兼容约束"
grep -Fq 'soxr~=1.0.0' "${PUBLISH_SCRIPT}" || fail "缺少 soxr 兼容约束"
grep -Fq 'pillow' "${PUBLISH_SCRIPT}" || fail "缺少 Pillow 运行依赖"
if grep -Fq -- '--no-deps' "${PUBLISH_SCRIPT}"; then
  fail "不得禁用 pipecat 传递依赖解析"
fi

ALLOW_DIRTY=1 "${PUBLISH_SCRIPT}" --仅打包 --输出包 "${TEST_ARCHIVE}"
[[ -s "${TEST_ARCHIVE}" ]] || fail "未生成发布包"

ARCHIVE_LIST="${TEMP_DIR}/包清单.txt"
tar -tzf "${TEST_ARCHIVE}" > "${ARCHIVE_LIST}"

grep -Eq '(^|/)Dockerfile$' "${ARCHIVE_LIST}" || fail "发布包缺少 Dockerfile"
if grep -Eq '(^|/)(\.env($|\.)|\.git/|\.venv/|\.runtime/|certs/|__pycache__/|\.pytest_cache/)' "${ARCHIVE_LIST}"; then
  fail "发布包包含密钥或垃圾目录"
fi

EXTRACT_DIR="${TEMP_DIR}/解包"
mkdir -p "${EXTRACT_DIR}"
tar -xzf "${TEST_ARCHIVE}" -C "${EXTRACT_DIR}"
[[ -f "${EXTRACT_DIR}/发布信息.txt" ]] || fail "发布包缺少发布信息"
grep -Fq 'CMD ["python", "-c", "from realtime_server.combined_server import main; main()"]' "${EXTRACT_DIR}/Dockerfile" \
  || fail "发布包中的 Docker CMD 不符合要求"

echo "一键发布脚本离线自检通过"
