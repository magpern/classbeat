# shellcheck shell=bash
# Self-test of the swag site rules and of the redacted nginx -t summary (repo mode).
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
if [[ $mode == repo ]]; then   # the sample and its self-tests (ADR 0020)
check_site "$site" || exit 1
# expect_site_refused SED [FRAGMENT]: the mutated sample must be refused, and with FRAGMENT in the
# message when given (so a mutation that merely fails to parse cannot pass for the rule it is for).
expect_site_refused() {
  sed -E "$1" "$site" > "$tmp/site-mutated.conf"
  if (check_site "$tmp/site-mutated.conf" 2>"$tmp/site-refuse.log"); then
    echo "check-prod-compose: self-test: accepted an unsafe site config: $1" >&2; exit 1
  fi
  if [[ -n "${2:-}" ]] && ! grep -qF -- "$2" "$tmp/site-refuse.log"; then
    cat "$tmp/site-refuse.log" >&2
    echo "check-prod-compose: self-test: refused for another reason than '$2': $1" >&2; exit 1
  fi
}
# The harmless rewrites: the same directives in another layout must still pass (the tokenizer must
# not see a second directive, or lose one, where nginx sees none).
expect_site_accepted() {
  sed -E "$1" "$site" > "$tmp/site-mutated.conf"
  if ! (check_site "$tmp/site-mutated.conf" 2>"$tmp/site-accept.log"); then
    cat "$tmp/site-accept.log" >&2
    echo "check-prod-compose: self-test: refused a valid site config: $1" >&2; exit 1
  fi
}
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
for m in \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/a\    proxy_set_header X-Forwarded-For $http_x_forwarded_for;' \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/a\    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/a\    proxy_set_header x-forwarded-for "6.6.6.6";' \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/i\    proxy_set_header "X-Forwarded-For" $http_x_forwarded_for;' \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/d' \
  's/proxy_set_header X-Forwarded-For \$remote_addr;/proxy_set_header X-Forwarded-For $http_x_forwarded_for;/' \
  's/^( *)location @classbeat_down \{$/\1server { server_name classbeat.example.test; }\n&/' \
  '$a\server { listen 8443; server_name other.example.test; }' \
  '/include \/config\/nginx\/ssl.conf;/a\    include /config/nginx/proxy.conf;' \
  '/include \/config\/nginx\/ssl.conf;/a\    include /config/nginx/extra-headers.conf;' \
  '/server_tokens off;/d' \
  's/server_tokens off;/server_tokens on;/' \
  '/error_page 502/a\    proxy_intercept_errors on;' \
  '/client_max_body_size 1m;/d' \
  '/client_max_body_size 1m;/a\    client_max_body_size 100m;' \
  '/add_header Strict-Transport-Security/a\    add_header Content-Security-Policy "default-src *" always;' \
  's/proxy_buffering off;/proxy_buffering on;/' \
  '/return 502/a\        add_header Cache-Control "no-store" always;' \
  's/real_ip_header CF-Connecting-IP;/real_ip_header X-Forwarded-For;/' \
  '/include \/config\/nginx\/cf_real-ip.conf;/d' \
  's/^( *)location \/ \{$/&\n        if ($host) { return 200; }/' \
  's/proxy_set_header X-Forwarded-For \$remote_addr;/proxy_set_header X-Forwarded-For $remote_addr/'; do
  expect_site_refused "$m"
done
# The four spellings of a second X-Forwarded-For that the line-based check missed (#500), each with
# the visitor-chosen value and with $remote_addr, and a location that carries the header itself.
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
for v in '$http_x_forwarded_for' '$remote_addr'; do
  one='exactly one proxy_set_header X-Forwarded-For (found 2)'
  # (a) a second directive on the line of X-Forwarded-Proto, after the overwrite
  expect_site_refused 's/^( *proxy_set_header X-Forwarded-Proto https;)$/\1 proxy_set_header X-Forwarded-For '"$v"';/' "$one"
  # (b) the directive split over two lines (after its name, and before its value)
  expect_site_refused 's/^( *proxy_set_header X-Forwarded-Proto https;)$/\1\n    proxy_set_header\n        X-Forwarded-For '"$v"';/' "$one"
  expect_site_refused 's/^( *proxy_set_header X-Forwarded-Proto https;)$/\1\n    proxy_set_header X-Forwarded-For\n        '"$v"';/' "$one"
  # (c) after a quoted "#" (and after a "#" inside a token) that a comment stripper would cut at
  expect_site_refused 's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP "#"; proxy_set_header X-Forwarded-For '"$v"';/' "$one"
  expect_site_refused 's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP a#b; proxy_set_header X-Forwarded-For '"$v"';/' "$one"
  # (d) a one-line location block, and a multi-line one: a location's own proxy_set_header replaces
  # every inherited one, so it is refused whatever it sets
  expect_site_refused 's/^( *)location @classbeat_down \{$/\1location \/api\/ { proxy_set_header X-Forwarded-For '"$v"'; proxy_pass http:\/\/$upstream_classbeat:8000; }\n&/' \
    'a location sets proxy_set_header'
  expect_site_refused 's/^( *)location \/ \{$/&\n        proxy_set_header\n            X-Forwarded-For '"$v"';/' \
    'a location sets proxy_set_header'
done
# A backslash inside a quoted string (#470, review of #592): `\\"` ends the string after one backslash
# (sed turns the four backslashes below into two, nginx reads two as one), so the second
# X-Forwarded-For after it is a real directive.
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
expect_site_refused 's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP "a\\\\"; proxy_set_header X-Forwarded-For $http_x_forwarded_for;/' \
  'exactly one proxy_set_header X-Forwarded-For (found 2)'
# A location that sets some other header drops the server-level overwrite from what it forwards.
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
expect_site_refused 's/^( *)location @classbeat_down \{$/\1location \/api\/ { proxy_set_header Host $host; proxy_pass http:\/\/$upstream_classbeat:8000; }\n&/' \
  'a location sets proxy_set_header'
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
for m in \
  's/proxy_set_header X-Forwarded-For \$remote_addr;/proxy_set_header\n        "x-forwarded-for"\n        ${remote_addr}; # proxy_set_header X-Forwarded-For $http_x_forwarded_for;/' \
  's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP "a # b ; proxy_set_header X-Forwarded-For x"; proxy_set_header X-Real-Host a#b;/' \
  's/^( *)location @classbeat_down \{$/\1location = \/health { return 200 "ok"; }\n&/' \
  's/^( *proxy_set_header X-Forwarded-Proto https;)$/\1 # proxy_set_header X-Forwarded-For $http_x_forwarded_for;/' \
  's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP "a \\" ; proxy_set_header X-Forwarded-For x";/' \
  's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP a\\;proxy_set_header X-Forwarded-For x;/'; do
  expect_site_accepted "$m"
done
# nginx_log_summary prints no word of the log (#470): a planted canary in every place nginx quotes
# file content must not appear; the level and the site file's line number must.
canary="CANARY-$RANDOM$RANDOM-not-to-be-printed"
{
  printf 'nginx: [emerg] unknown directive "%s" in /config/nginx/proxy-confs/classbeat.subdomain.conf:12\n' "$canary"
  printf 'nginx: [warn] "%s" in /config/nginx/ssl.conf:3\n' "$canary"
  printf '%s\n' "$canary"
  printf 'nginx: configuration file /check/nginx.conf test failed (%s)\n' "$canary"
} > "$tmp/nginx-canary.log"
nginx_log_summary "$tmp/nginx-canary.log" 2> "$tmp/nginx-summary.log"
if grep -qF -- "$canary" "$tmp/nginx-summary.log" \
  || ! grep -qF 'nginx -t: emerg at line 12 of the site config' "$tmp/nginx-summary.log" \
  || ! grep -qF 'nginx -t: warn at no site config line' "$tmp/nginx-summary.log" \
  || [[ "$(wc -l < "$tmp/nginx-summary.log")" -ne 2 ]]; then
  echo "check-prod-compose: self-test: the nginx -t summary is not the bounded, redacted one" >&2; exit 1
fi
for _ in 1 2 3 4 5 6 7; do printf 'nginx: [emerg] "%s" in /x/classbeat.subdomain.conf:1\n' "$canary"; done > "$tmp/nginx-canary.log"
nginx_log_summary "$tmp/nginx-canary.log" 2> "$tmp/nginx-summary.log"
if grep -qF -- "$canary" "$tmp/nginx-summary.log" || [[ "$(wc -l < "$tmp/nginx-summary.log")" -ne 6 ]] \
  || ! grep -qF 'nginx -t: 2 more error lines' "$tmp/nginx-summary.log"; then
  echo "check-prod-compose: self-test: the nginx -t summary is not bounded to 5 lines" >&2; exit 1
fi
nginx_site_t "$site" || exit 1
fi
