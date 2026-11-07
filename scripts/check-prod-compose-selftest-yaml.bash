# shellcheck shell=bash
# Self-test: YAML and text bypasses, the real render and the owner PROXY_IP (repo mode).
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
# The same bypasses as YAML (#517): a compose file merged after compose.prod.yaml (and after each
# overlay), rendered with every profile and run through the whole variant check, on every variant. The
# text rule is bypassed on purpose here (expect_yaml_refused looks at the render only), so the
# allow-list is what refuses; expect_text_refused then checks the keys the render drops.
mkdir "$tmp/yaml"
ln -s ../secrets "$tmp/yaml/secrets"
cp "$tmp/compose.prod.yaml" "$tmp/compose.prod.loopback.yaml" "$tmp/compose.prod.lan.yaml" "$tmp/yaml/"
ydir="$native/${tmp##*/}/yaml"
yaml_render() {   # yaml_render VARIANT: the variant's files plus mutation.yaml, every profile
  case "$1" in
    shared) render -f "$ydir/compose.prod.yaml" -f "$ydir/mutation.yaml" ;;
    loopback) render -f "$ydir/compose.prod.yaml" -f "$ydir/compose.prod.loopback.yaml" -f "$ydir/mutation.yaml" ;;
    *) render -f "$ydir/compose.prod.yaml" -f "$ydir/compose.prod.lan.yaml" -f "$ydir/mutation.yaml" ;;
  esac
}
yaml_verdict() {   # yaml_verdict WANT(accepted|refused) FRAGMENT TEXT
  local want="$1" why="$2" v out rc
  printf '%s\n' "$3" > "$tmp/yaml/mutation.yaml"
  for v in shared loopback lan; do
    out="$(yaml_render "$v")" \
      || { echo "check-prod-compose: self-test: a YAML mutation does not render ($v): $3" >&2; exit 1; }
    rc=0; (J="$out"; "check_$v" 2>"$tmp/yaml-refuse.log") || rc=$?
    if [[ "$want" == accepted && "$rc" != 0 ]]; then
      cat "$tmp/yaml-refuse.log" >&2; echo "check-prod-compose: self-test: refused a harmless YAML file ($v): $3" >&2; exit 1
    fi
    if [[ "$want" == refused ]]; then
      [[ "$rc" != 0 ]] || { echo "check-prod-compose: self-test: accepted unsafe YAML ($v): $3" >&2; exit 1; }
      grep -qF -- "$why" "$tmp/yaml-refuse.log" \
        || { cat "$tmp/yaml-refuse.log" >&2; echo "check-prod-compose: self-test: refused for another reason than '$why' ($v): $3" >&2; exit 1; }
    fi
  done
}
expect_yaml_refused() { yaml_verdict refused "$1" "$2"; }   # expect_yaml_refused FRAGMENT TEXT
expect_text_refused() {   # expect_text_refused TEXT: the files' text, for the keys the render drops
  printf '%s\n' "$1" > "$tmp/yaml/mutation.yaml"
  if (check_prod_file_text "$tmp/yaml" mutation.yaml 2>/dev/null); then
    echo "check-prod-compose: self-test: the text rule accepted: $1" >&2; exit 1
  fi
}
expect_yaml_refused configs 'configs:
  hostproc:
    file: /proc
services:
  worker:
    configs:
      - source: hostproc
        target: /hostproc'
expect_yaml_refused services.web.post_start 'services:
  web:
    post_start:
      - command: ["sh", "-c", "id"]
        user: root
        privileged: true'
expect_yaml_refused services.worker.pre_stop 'services:
  worker:
    pre_stop:
      - command: ["sh", "-c", "id"]'
expect_yaml_refused services.worker.volumes_from 'services:
  worker:
    volumes_from: [web]'
expect_yaml_refused volumes.db-data.driver_opts 'volumes:
  db-data:
    driver_opts: {type: none, o: bind, device: /}'
expect_yaml_refused services.worker.pid 'services:
  worker:
    pid: "service:web"'
expect_yaml_refused services.worker.volumes 'services:
  worker:
    volumes:
      - type: bind
        source: /proc/1/root
        target: /h'
expect_yaml_refused services.worker.stop_signal 'services:
  worker:
    stop_signal: SIGKILL'
expect_yaml_refused services.worker.cgroup_parent 'services:
  worker:
    cgroup_parent: elsewhere'
expect_yaml_refused 'security_opt is exactly' 'services:
  worker:
    security_opt: [seccomp=unconfined]'
# B5, both ways: the render holds a profiled service (--profile "*"), as a new service, and the text
# rule refuses the key itself.
expect_yaml_refused services.spy 'services:
  spy:
    image: busybox
    profiles: [ops]
    pid: host
    privileged: true
    group_add: ["10010"]
    logging: {driver: json-file, options: {max-size: 10m}}
    volumes:
      - /:/host'
printf 'services:\n  spy:\n    image: busybox\n    profiles: [ops]\n' > "$tmp/yaml/mutation.yaml"
jq -e '.services | has("spy")' <<<"$(yaml_render shared)" >/dev/null \
  || { echo "check-prod-compose: self-test: the render left out a profiled service" >&2; exit 1; }
expect_yaml_refused services.brand_new_service 'services:
  brand_new_service:
    image: busybox
    logging: {driver: json-file, options: {max-size: 10m}}'
# The text rule's bypasses (#598 review, finding 1; #610): env_file, label_file, profiles and include
# spelled so a line match misses them (a quoted key, YAML's explicit `? key`, a flow mapping). The render
# is the check: the env_file's variables land in environment (check_keys refuses them by name, without
# printing one), the other keys stay in the render or bring a new service. The secret file's canary (an
# OAuth dummy, one line without `=`) becomes the variable "" and must never be printed.
printf 'SPY=1\n' > "$tmp/yaml/spy.env"
printf 'services:\n  spy:\n    image: busybox\n    logging: {driver: json-file, options: {max-size: 10m}}\n' > "$tmp/yaml/other.yaml"
expect_yaml_refused 'services.worker.environment: 1 variable' 'services:
  worker:
    "env_file": ./secrets/oauth_google_client_secret'
grep -qF -- "$oauth_canary" "$tmp/yaml-refuse.log" \
  && { echo "check-prod-compose: self-test: a refused env_file's value was printed" >&2; exit 1; }
expect_yaml_refused 'services.worker.environment: 1 variable' "services:
  worker:
    'env_file': [./spy.env]"
expect_yaml_refused 'services.worker.environment: 1 variable' 'services:
  worker:
    ? env_file
    : ./spy.env'
expect_yaml_refused 'services.db.environment: 1 variable' 'services:
  db:
    ? env_file
    : ./secrets/connection_kek'
grep -qF -- 'dummy-secret-connection_kek' "$tmp/yaml-refuse.log" \
  && { echo "check-prod-compose: self-test: a refused env_file's value was printed" >&2; exit 1; }
expect_yaml_refused 'services.web.environment: 1 variable' 'services: {web: {"env_file": [{path: ./spy.env}]}}'
# (Compose versions differ in what they keep: label_file itself, or only the labels it reads.)
expect_yaml_refused 'services.worker.label' 'services:
  worker:
    ? label_file
    : ./spy.env'
expect_yaml_refused 'services.spy' 'services:
  spy:
    image: busybox
    logging: {driver: json-file, options: {max-size: 10m}}
    "profiles": [ops]'
expect_yaml_refused 'services.spy' '? include
: [./other.yaml]'
# The real render (check_real, #610): the documented compose.bridge.yaml of proton-bridge-smtp.md passes;
# any other shape of it, and anything else the dummy files would refuse, does not.
mkdir -p "$tmp/yaml/smtp-ca"; : > "$tmp/yaml/smtp-ca/bridge.pem"
real_verdict() {   # real_verdict WANT(accepted|refused) FRAGMENT TEXT: shared and loopback, plus mutation.yaml
  local want="$1" why="$2" v out rc
  printf '%s\n' "$3" > "$tmp/yaml/mutation.yaml"
  for v in shared loopback lan; do
    out="$(yaml_render "$v")" \
      || { echo "check-prod-compose: self-test: a real-render mutation does not render ($v): $3" >&2; exit 1; }
    rc=0; (J="$out"; check_real 2>"$tmp/real-refuse.log") || rc=$?
    if [[ "$want" == accepted && "$rc" != 0 ]]; then
      cat "$tmp/real-refuse.log" >&2; echo "check-prod-compose: self-test: the real-render check refused ($v): $3" >&2; exit 1
    fi
    if [[ "$want" == refused ]]; then
      [[ "$rc" != 0 ]] || { echo "check-prod-compose: self-test: the real-render check accepted ($v): $3" >&2; exit 1; }
      grep -qF -- "$why" "$tmp/real-refuse.log" \
        || { cat "$tmp/real-refuse.log" >&2; echo "check-prod-compose: self-test: real render refused for another reason than '$why' ($v): $3" >&2; exit 1; }
    fi
  done
}
bridge='services:
  web:
    volumes:
      - ./smtp-ca/bridge.pem:/run/smtp-ca/bridge.pem:ro
    networks:
      - bridge-mail
networks:
  bridge-mail:
    external: true
    name: proton-bridge-net'
real_verdict accepted '' "$bridge"
real_verdict refused services.web.volumes "${bridge/:ro/}"
real_verdict refused services.web.volumes "${bridge/.\/smtp-ca\/bridge.pem:/.\/secrets:}"
real_verdict refused services.web.volumes "${bridge/\/run\/smtp-ca\/bridge.pem/\/run\/secrets\/x}"
real_verdict refused services.web.volumes "${bridge/external: true/external: false}"
real_verdict refused services.web.volumes "${bridge/services:/services:
  worker:
    networks: [bridge-mail]}"
real_verdict refused services.web.volumes "${bridge//bridge-mail/mail-net}"
real_verdict refused 'networks.spynet' 'services:
  web:
    networks: [spynet]
networks:
  spynet:
    external: true
    name: somebody-elses-network'
real_verdict refused 'services.web.environment: 1 variable' "${bridge/  web:/  web:
    \"env_file\": ./secrets/oauth_github_client_secret}"
real_verdict refused 'services.worker.environment: 1 variable' 'services:
  worker:
    ? env_file
    : ./spy.env'
expect_refused real "$shared" '.services.db.volumes += [{"type":"bind","source":"/proc","target":"/h"}]' 'no bind mounts'
expect_refused real "$shared" '.services.db.user = "0"' 'runs as root'
real_verdict refused 'services.spy' 'services:
  spy:
    image: busybox
    profiles: [ops]'
# Harmless: an extension key, and a comment that names a forbidden key, pass both rules.
yaml_verdict accepted "" 'x-note: fine'
printf '# profiles: [x] and env_file: y in a comment\nx-note: fine\n' > "$tmp/yaml/mutation.yaml"
check_prod_file_text "$tmp/yaml" mutation.yaml \
  || { echo "check-prod-compose: self-test: the text rule refused a comment" >&2; exit 1; }
# The keys the render drops (and the two that pull in text this script never sees), in the files' text.
expect_text_refused 'services:
  worker:
    env_file: ./secrets/app_secret_key'
expect_text_refused 'services:
  worker:
    env_file:
      - path: ./secrets/connection_kek
        required: false'
expect_text_refused 'services:
  worker:
    label_file: ./secrets/app_secret_key'
expect_text_refused 'services:
  spy:
    image: busybox
    profiles: [ops]'
expect_text_refused 'services:
  spy: {image: busybox, profiles: [ops]}'
expect_text_refused 'services:
  worker:
    "env_file": [./secrets/app_secret_key]'
expect_text_refused 'services: {worker: {env_file: [./secrets/app_secret_key]}}'
expect_text_refused 'include:
  - ./other.yaml'
expect_text_refused 'services:
  worker:
    extends:
      file: other.yaml
      service: web'
# The same, written into each real file (copies): none of the three may carry one.
mkdir "$tmp/textfiles"
for f in compose.prod.yaml compose.prod.loopback.yaml compose.prod.lan.yaml; do
  for key in 'env_file: ./secrets/app_secret_key' 'label_file: ./secrets/app_secret_key' 'profiles: [ops]'; do
    cp "$tmp/compose.prod.yaml" "$tmp/compose.prod.loopback.yaml" "$tmp/compose.prod.lan.yaml" "$tmp/textfiles/"
    sed -i -E "0,/^services:/s||services:\n  web:\n    $key|" "$tmp/textfiles/$f"
    if (check_prod_file_text "$tmp/textfiles" 2>/dev/null); then
      echo "check-prod-compose: self-test: accepted $f with '$key'" >&2; exit 1
    fi
  done
done
# The owner's PROXY_IP itself (it reaches CLASSBEAT_TRUSTED_PROXIES unchanged): a range, a list, a
# public or an empty address must be refused even when the variable and the setting agree.
good_ip="$swag_ip"
for bad in 172.29.90.0/24 172.29.90.10/32 0.0.0.0/0 8.8.8.8 173.245.48.5 "172.29.90.10,10.0.0.1" \
  172.32.0.1 172.29.90.256 172.29.090.10 fd00::1 swag ""; do
  swag_ip="$bad"
  expect_refused shared "$shared" ".services.web.environment.CLASSBEAT_TRUSTED_PROXIES = \"$bad\""
done
swag_ip="$good_ip"
for m in \
  '.services.web.ports[0].host_ip = "0.0.0.0"' \
  'del(.services.web.ports[0].host_ip)' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.10"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.80.0/24"' \
  '.services.web.networks.proxy = {"aliases":["classbeat-web"]}' \
  '.services.worker.ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8081"}]' \
  '.services.worker.secrets += [{"source":"oauth_microsoft_client_secret"}]'; do
  expect_refused loopback "$loopback" "$m"
done

# The LAN overlay (#567): one port on the one private address, web off the proxy network.
for m in \
  '.services.web.ports[0].host_ip = "0.0.0.0"' \
  '.services.web.ports[0].host_ip = "::"' \
  '.services.web.ports[0].host_ip = "127.0.0.1"' \
  '.services.web.ports[0].host_ip = "192.168.1.21"' \
  'del(.services.web.ports[0].host_ip)' \
  '.services.web.ports[0].target = 5432' \
  '.services.web.ports[0].published = "80"' \
  '.services.web.ports[0].protocol = "udp"' \
  '.services.web.ports += [{"host_ip":"192.168.1.20","target":8000,"published":"8081"}]' \
  '.services.web.networks.proxy = {"aliases":["classbeat-web"]}' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.80.1"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "192.168.1.20"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "192.168.1.0/24"' \
  '.services.worker.ports = [{"host_ip":"192.168.1.20","target":8000,"published":"8082"}]' \
  '.services.db.ports = [{"host_ip":"192.168.1.20","target":5432,"published":"5432"}]'; do
  expect_refused lan "$lan" "$m"
done
# swag is this server (PROXY_IP equal to WEB_LAN_ADDRESS): the web setting matches PROXY_IP, so only the
# clause `$ip != $lan` of check_lan can refuse it (the mutation list above is refused by the first clause
# alone: a mutation removing `$ip != $lan` survived, review of #576).
lan_same="$(PROXY_IP="$lan_ip" render -f "$dir/compose.prod.yaml" -f "$dir/compose.prod.lan.yaml")"
swag_keep="$swag_ip"; swag_ip="$lan_ip"
expect_refused lan "$lan_same" '.' "and not this server"
swag_ip="$swag_keep"
