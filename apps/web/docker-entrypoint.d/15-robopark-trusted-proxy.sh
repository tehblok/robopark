#!/bin/sh
set -eu

# Published localhost ports reach a bridge container through its host gateway.
# Only that exact peer (and nginx.conf's loopback) may supply a client address;
# sibling containers must not gain trust merely by having a private address.
gateway=$(ip -4 route show default | awk '$1 == "default" && $2 == "via" { print $3; exit }')
if ! printf '%s\n' "$gateway" | awk -F. '
  NF != 4 { exit 1 }
  { for (i = 1; i <= 4; i++) if ($i !~ /^[0-9]+$/ || length($i) > 3 || $i > 255) exit 1 }
'; then
  echo 'robopark: cannot determine the trusted Docker host gateway' >&2
  exit 1
fi

mkdir -p /etc/nginx/realip.d
printf 'set_real_ip_from %s;\n' "$gateway" > /etc/nginx/realip.d/host.conf
