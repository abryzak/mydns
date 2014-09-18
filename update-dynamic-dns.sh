#!/usr/bin/env bash

set -e

label="myhost"
zone="example.com"
internal_token="00000000-0000-4000-0000-000000000000"
external_token="11111111-1111-4111-1111-111111111111"

internal_zone="internal.$zone"
external_zone="external.$zone"

curl --fail -sS >/dev/null -F zone="$external_zone" -F token="$external_token" -F label="$label" "https://my-dns.org/api/update-record" || echo >&2 "Updating external DNS failed"

ip="$(hostname -I | head -n 1)"
if [[ -n "$ip" ]]; then
	curl --fail -sS >/dev/null -F zone="$internal_zone" -F token="$internal_token" -F label="$label" -F data="$ip" "https://my-dns.org/api/update-record" || echo >&2 "Updating internal DNS failed"
fi
