# shellcheck shell=bash
# The runbook's proxy network and the swag container address verdict.
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
if [[ $mode == repo ]]; then   # the self-tests and the checkout-only checks (ADR 0020)
# --- The runbook's proxy network (#495) -----------------------------------------------------------
ip2int() { local a b c d; IFS=. read -r a b c d <<<"$1"; echo $(( (a << 24) | (b << 16) | (c << 8) | d )); }
in_cidr() {   # in_cidr ADDRESS NET/LEN
  local len="${2#*/}" mask
  mask=$(( len == 0 ? 0 : (0xFFFFFFFF << (32 - len)) & 0xFFFFFFFF ))
  (( ($(ip2int "$1") & mask) == ($(ip2int "${2%/*}") & mask) ))
}
cidr_re='[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+'
ip_re='[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'
# swag's fixed address is in the network's subnet but outside --ip-range, the pool Docker hands out
# to other containers, so none can take it while swag is down; it equals PROXY_IP's example and this
# script's dummy.
check_runbook() {
  local f="$1" create subnet range gw addr env
  create="$(grep -E '^docker network create ' "$f")" || fail "$f: no docker network create" || return 1
  [ "$(wc -l <<<"$create")" = 1 ] || fail "$f: one docker network create" || return 1
  grep -Eq -- '--internal( |$)' <<<"$create" || fail "$f: the proxy network is --internal" || return 1
  subnet="$(grep -Eo -- "--subnet $cidr_re" <<<"$create" | cut -d' ' -f2)" || fail "$f: --subnet" || return 1
  range="$(grep -Eo -- "--ip-range $cidr_re" <<<"$create" | cut -d' ' -f2)" \
    || fail "$f: --ip-range keeps swag's address out of Docker's pool" || return 1
  gw="$(grep -Eo -- "--gateway $ip_re" <<<"$create" | cut -d' ' -f2)" || fail "$f: --gateway" || return 1
  addr="$(grep -Eo "ipv4_address: $ip_re" "$f" | cut -d' ' -f2)" || fail "$f: swag's ipv4_address" || return 1
  env="$(grep -Eo "^PROXY_IP=$ip_re" "$f" | cut -d= -f2)" || fail "$f: the PROXY_IP example" || return 1
  [ "$(wc -l <<<"$addr")" = 1 ] && [ "$addr" = "$env" ] && [ "$addr" = "$good_ip" ] \
    || fail "$f: swag's ipv4_address, PROXY_IP's example and this script's dummy agree" || return 1
  (( ${range#*/} >= ${subnet#*/} )) && in_cidr "${range%/*}" "$subnet" \
    || fail "$f: --ip-range lies inside --subnet" || return 1
  in_cidr "$addr" "$subnet" && ! in_cidr "$addr" "$range" \
    || fail "$f: swag's address is in the subnet and outside --ip-range" || return 1
  in_cidr "$gw" "$subnet" && ! in_cidr "$gw" "$range" && [ "$gw" != "$addr" ] \
    || fail "$f: the gateway is in the subnet, outside --ip-range and not swag's" || return 1
}
check_runbook "$runbook" || exit 1
expect_runbook_refused() {
  sed -E "$1" "$runbook" > "$tmp/runbook.md"
  if (check_runbook "$tmp/runbook.md" 2>/dev/null); then
    echo "check-prod-compose: self-test: accepted an unsafe runbook: $1" >&2; exit 1
  fi
}
for m in \
  's/ --ip-range [^ ]+//' \
  's|--ip-range [^ ]+|--ip-range 172.29.90.0/25|' \
  's|--ip-range [^ ]+|--ip-range 172.29.90.8/29|' \
  's|--ip-range [^ ]+|--ip-range 172.29.0.0/16|' \
  's/ipv4_address: [0-9.]+/ipv4_address: 172.29.90.200/' \
  's/^PROXY_IP=[0-9.]+/PROXY_IP=172.29.90.11/' \
  's|--gateway [^ ]+|--gateway 172.29.90.10|' \
  's/ --internal//'; do
  expect_runbook_refused "$m"
done

# swag's address against PROXY_IP (#500): a match, a typo inside Docker's range, a second network,
# and nothing to compare.
swag_address_verdict 172.29.90.10 "172.29.90.10 " 2>/dev/null || { echo "check-prod-compose: self-test: swag's own address refused" >&2; exit 1; }
swag_address_verdict 172.29.90.10 "10.1.0.5 172.29.90.10 " 2>/dev/null || { echo "check-prod-compose: self-test: swag's address on its second network refused" >&2; exit 1; }
if (swag_address_verdict 172.29.90.130 "172.29.90.10 " 2>/dev/null); then
  echo "check-prod-compose: self-test: a PROXY_IP that is not swag's address was accepted" >&2; exit 1
fi
rc=0; swag_address_verdict 172.29.90.10 "  " 2>/dev/null || rc=$?
[[ "$rc" == 2 ]] || { echo "check-prod-compose: self-test: no swag address must be 'nothing to compare', not a verdict" >&2; exit 1; }
fi
