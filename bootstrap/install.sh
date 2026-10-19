#!/usr/bin/env bash
# Public bootstrap only: no private hub or Cloudflare credentials.
set -Eeuo pipefail
umask 077
installer="$(mktemp)"
trap 'rm -f -- "$installer"' EXIT
if ! curl -fsSL --retry 3 --connect-timeout 15 --max-time 180 \
  https://raw.githubusercontent.com/mtgggxfc-eng/ip/main/jkyj.sh -o "$installer"; then
  printf '安装脚本下载失败，请检查 GitHub 连接后重试。\n' >&2
  exit 1
fi
if [[ $(id -u) -eq 0 ]]; then
  bash "$installer" "$@"
else
  command -v sudo >/dev/null || { printf '请切换 root 用户后重新执行。\n' >&2; exit 1; }
  sudo bash "$installer" "$@"
fi
