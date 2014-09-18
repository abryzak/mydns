#!/usr/bin/env bash

set -e
set -o pipefail

admin_token="00000000-0000-4000-0000-000000000000"

basename=$(basename "$0")
tmpfile=$(mktemp "${TMPDIR:-/tmp}/${basename}.XXXXXXXX")
chmod 644 "$tmpfile"
chgrp bind "$tmpfile"

master_ip="$(dig ns1.my-dns.org +short)"
[[ -n "$master_ip" ]] || { echo >&2 "no master ip"; exit 1; }
while IFS= read -r zone; do
  cat <<EOF
zone "$zone" {
	type slave;
	file "db.$zone";
	masters {
		$master_ip;
	};
};
EOF
done >> "$tmpfile" < <(curl --fail -sS -F token="$admin_token" "https://my-dns.org/api/zones" | jq '.zones[]' | sed -e 's/^"//'  -e 's/"$//')

named-checkconf "$tmpfile"

mv "$tmpfile" "/etc/bind/named.conf.mydns"
service bind9 reload >/dev/null
