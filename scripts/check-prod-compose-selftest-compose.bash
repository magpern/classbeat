# shellcheck shell=bash
# Self-test: every unsafe mutation of the rendered files is refused (repo mode).
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
# Self-test: every mutation is unsafe and must be refused.
expect_refused() {   # expect_refused VARIANT BASE MUTATION [FRAGMENT of the message it must give]
  local variant="$1" base="$2" mutation="$3" why="${4:-}"
  J="$(jq "$mutation" <<<"$base")" \
    || { echo "check-prod-compose: self-test: a mutation does not apply: $mutation" >&2; exit 1; }
  if ("check_$variant" 2>"$tmp/refuse.log"); then
    echo "check-prod-compose: self-test: accepted an unsafe $variant file: $mutation" >&2
    exit 1
  fi
  if [[ -n "$why" ]] && ! grep -qF -- "$why" "$tmp/refuse.log"; then
    cat "$tmp/refuse.log" >&2
    echo "check-prod-compose: self-test: refused for another reason than '$why' ($variant): $mutation" >&2
    exit 1
  fi
}
expect_accepted() {   # expect_accepted VARIANT BASE MUTATION: a harmless change must still pass
  local variant="$1" base="$2" mutation="$3"
  J="$(jq "$mutation" <<<"$base")" \
    || { echo "check-prod-compose: self-test: a mutation does not apply: $mutation" >&2; exit 1; }
  ("check_$variant" 2>"$tmp/accept.log") \
    || { cat "$tmp/accept.log" >&2; echo "check-prod-compose: self-test: refused a harmless $variant change: $mutation" >&2; exit 1; }
}
for m in \
  '.services.caddy = {"image":"caddy:2.10.2","ports":[{"target":443,"published":"443"}],"logging":.services.web.logging}' \
  '.services.web.ports = [{"host_ip":"0.0.0.0","target":8000,"published":"8000"}]' \
  '.services.web.ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8000"}]' \
  '.services.db.ports = [{"host_ip":"127.0.0.1","target":5432,"published":"5432"}]' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.0/24"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "0.0.0.0/0"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "173.245.48.5"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.10,172.29.90.130"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.130"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = ""' \
  '.services.worker.networks.proxy = null' \
  '.services.db.networks["web-out"] = null' \
  '.networks.proxy.external = false' \
  '.networks.backend.internal = false' \
  '.services.web.read_only = false' \
  '.services.web.cap_drop = []' \
  'del(.services.web.healthcheck)' \
  '.services.web.secrets += [{"source":"anthropic_api_key","target":"anthropic_api_key"}]' \
  '.services.web.environment.CLASSBEAT_PUBLIC_BASE_URL = "http://classbeat.example.test"' \
  '.services.web.environment.CLASSBEAT_ENVIRONMENT = "dev"' \
  '.services.web.environment.CLASSBEAT_RUNNING_ENABLED = "true"' \
  '.services.worker.environment.CLASSBEAT_SOUNDLESS_ENABLED = "true"' \
  '.services.worker.environment.CLASSBEAT_SWEDEBEAT_ENABLED = "true"' \
  '.services.web.environment.CLASSBEAT_SWEDEBEAT_STUDIO = "design"' \
  'del(.services.worker.environment.CLASSBEAT_SWEDEBEAT_STUDIO)' \
  '.services.migrate.environment.CLASSBEAT_SWEDEBEAT_STUDIO = ""' \
  '.services.mailpit = {"image":"axllent/mailpit","logging":.services.web.logging}' \
  '.volumes["caddy-data"] = {}' \
  '.services.extra = {"image":"busybox","networks":{"proxy":null},"logging":.services.web.logging}' \
  '.services.extra = {"image":"busybox","networks":{"web-out":null},"logging":.services.web.logging}' \
  '.services.worker.secrets += [{"source":"oauth_google_client_secret","target":"/run/secrets/oauth_google_client_secret"}]' \
  '.services.migrate.secrets += [{"source":"oauth_github_client_secret"}]' \
  '.services.maint = {"image":.services.web.image,"networks":{"backend":null},"logging":.services.web.logging,"secrets":[{"source":"oauth_microsoft_client_secret"}]}' \
  '.services.web.secrets |= map(select(.source != "oauth_github_client_secret"))' \
  '.services.web.secrets |= map(if .source == "oauth_google_client_secret" then .target = "/tmp/google" else . end)' \
  '.secrets.oauth_google_client_secret = {"name":"x","environment":"OAUTH_GOOGLE_CLIENT_SECRET"}' \
  '.secrets.oauth_github_client_secret = {"name":"x","content":"a-literal-secret"}' \
  '.secrets.oauth_microsoft_client_secret.file = "/etc/hostname"' \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET = "x"' \
  '.services.worker.environment.OAUTH_GITHUB_CLIENT_SECRET = "x"' \
  '.services.worker.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "check-google-client-id"' \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "a-literal-in-compose"' \
  '.services.web.environment.CLASSBEAT_OAUTH_VISIBLE_PROVIDERS = "google"' \
  '.services.web.environment.CLASSBEAT_OAUTH_EXTRA = "x"' \
  '.services.migrate.secrets += [{"source":"swedebeat_gateway_key"}]' \
  '.services["db-functions"].secrets += [{"source":"swedebeat_gateway_key"}]' \
  '.services.web.secrets |= map(select(.source != "swedebeat_gateway_key"))' \
  '.services.worker.secrets |= map(select(.source != "swedebeat_gateway_key"))' \
  '.services.migrate.environment.CLASSBEAT_SWEDEBEAT_GATEWAY_URL = ""' \
  'del(.services.worker.environment.CLASSBEAT_SWEDEBEAT_GATEWAY_URL)' \
  '.services.web.environment.CLASSBEAT_SWEDEBEAT_GATEWAY_URL = "http://192.168.1.222:7355"' \
  '.services.worker.environment.CLASSBEAT_SWEDEBEAT_GATEWAY_KEY = "x"' \
  '.services.worker.secrets += [{"source":"turnstile_secret_key"}]' \
  '.services.web.secrets |= map(select(.source != "turnstile_secret_key"))' \
  '.services.web.environment.CLASSBEAT_TURNSTILE_ENABLED = "false"' \
  'del(.services.web.environment.CLASSBEAT_TURNSTILE_ENABLED)' \
  '.services.web.environment.CLASSBEAT_TURNSTILE_FAKE = "true"' \
  '.services.worker.environment.CLASSBEAT_TURNSTILE_FAKE = "false"' \
  '.services.worker.environment.CLASSBEAT_TURNSTILE_ENABLED = "false"' \
  '.services.worker.environment.CLASSBEAT_TURNSTILE_SITE_KEY = ""' \
  '.services.web.environment.CLASSBEAT_PASSKEYS = "true"' \
  '.services.worker.environment.CLASSBEAT_PASSKEYS = "false"' \
  '.services.worker.environment.CLASSBEAT_WEBAUTHN_RP_ID = ""' \
  '.services.migrate.environment.CLASSBEAT_PASSKEYS = "false"' \
  '.services.web.environment.CLASSBEAT_PASSKEYS_EXTRA = "x"' \
  '.services.web.environment.CLASSBEAT_WEBAUTHN_ORIGIN = "https://example.test"' \
  'del(.services.web.environment.CLASSBEAT_WEBAUTHN_RP_ID)'; do
  expect_refused shared "$shared" "$m"
done
# ADR 0035 section 8: the retired SwedeBeat app key, declared as a well-formed secret file (so no
# other rule refuses it first), mounted on web or only declared.
sb_app='.secrets.swedebeat_app = {"file": (.secrets.app_secret_key.file | rtrimstr("app_secret_key") + "swedebeat_app")}'
expect_refused shared "$shared" "$sb_app | .services.web.secrets += [{\"source\":\"swedebeat_app\"}]" "swedebeat_app"
expect_refused shared "$shared" "$sb_app" "swedebeat_app"
# ADR 0038 section 2 (#1185): the composer flow is not set in production at all, on any service,
# whatever its value (refused by its own rule, before the variable allow-list).
for m in \
  '.services.web.environment.CLASSBEAT_COMPOSER_FLOW = "true"' \
  '.services.worker.environment.CLASSBEAT_COMPOSER_FLOW = "false"' \
  '.services.migrate.environment.CLASSBEAT_COMPOSER_FLOW = ""'; do
  expect_refused shared "$shared" "$m" "CLASSBEAT_COMPOSER_FLOW is not set in production"
done
# Secret files (#511): the group, the users, one name per file, no other way into secrets/.
for m in \
  'del(.services.web.group_add)' \
  'del(.services.migrate.group_add)' \
  '.services.worker.group_add = ["10011"]' \
  '.services.db.group_add = []' \
  'del(.services["db-functions"].group_add)' \
  'del(.services.db.user)' \
  '.services.db.user = "root"' \
  '.services["db-functions"].user = "0"' \
  '.services.migrate.user = "0:0"' \
  '.services.web.user = "root"' \
  '.services.worker.user = "root:10010"' \
  '.secrets.gsec = {"name":"x","file":.secrets.oauth_google_client_secret.file} | .services.worker.secrets += [{"source":"gsec"}]' \
  '.secrets.kek2 = {"name":"x","file":.secrets.connection_kek.file} | .services.migrate.secrets += [{"source":"kek2","target":"/run/secrets/kek2"}]' \
  '.secrets.app_secret_key = {"name":"x","environment":"APP_SECRET_KEY"}' \
  '.secrets.smtp_password.file = "/etc/hostname"' \
  '.services.worker.secrets += [{"source":"oauth_google_client_secret","target":"/run/secrets/app_secret_key"}]' \
  '.services.migrate.secrets |= map(.target = "/run/secrets/database_password")' \
  '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key")),"target":"/s","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key") + "/"),"target":"/s"}]' \
  '.services.web.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key")),"target":"/x"}]' \
  '.services.web.volumes = [{"type":"bind","source":"/","target":"/host","read_only":true}]' \
  '.services.migrate.volumes = [{"type":"bind","source":.secrets.oauth_github_client_secret.file,"target":"/tmp/g"}]' \
  '.configs = {"c":{"file":.secrets.connection_kek.file}} | .services.web.configs = [{"source":"c","target":"/tmp/c"}]' \
  '.services.web.command = ["sh","-c","cat /run/secrets/oauth_google_*; exec python -m classbeat"]' \
  '.services.worker.entrypoint = ["sh","-c","cat /run/secrets/* >&2"]' \
  '.services.worker.command = "sh -c \"tar c secrets/ | nc x 1\""' \
  '.services.web.healthcheck.test = ["CMD","sh","-c","cat /run/secrets/app_secret_key"]' \
  '.services.db.image = "docker.io/library/postgres:17.12" | del(.services.db.user)' \
  '.services.db.image = "mirror.gcr.io/library/postgres@sha256:00" | .services.db.user = "1000"' \
  '.services.db.image = "example.test/pg:17" | del(.services.db.user)' \
  '.services.worker.volumes_from = ["web"]' \
  '.services.migrate.volumes_from = ["web:ro"]' \
  '.services.extra = {"image":.services.web.image,"networks":{"backend":null},"logging":.services.web.logging,"volumes_from":["container:classbeat-prod-web-1"]}' \
  '.services.worker.pid = "service:web"' \
  '.services.worker.pid = "host"' \
  '.services.migrate.pid = "container:classbeat-prod-web-1"' \
  '.services.worker.ipc = "service:web"' \
  '.services.db.ipc = "host"' \
  '.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key"))}' \
  '.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":"/"}' \
  '.volumes["db-data"].driver = "local-persist"' \
  '.volumes["db-data"].external = true' \
  '.services.worker.volumes = [{"type":"bind","source":"/proc","target":"/hostproc","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":"/proc/1/root","target":"/h","read_only":true}]' \
  '.services.web.volumes = [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]' \
  '.services.migrate.volumes = [{"type":"bind","source":"/sys","target":"/s","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":"/dev/sda1","target":"/d"}]' \
  '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/link-to-secrets"),"target":"/s","read_only":true}]' \
  '.services.db.volumes += [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/docker"),"target":"/d","read_only":true}]' \
  '.services["db-functions"].volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/docker/postgres/queue"),"target":"/queue","read_only":true}]' \
  '.services.db.volumes += [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/docker/postgres/init"),"target":"/docker-entrypoint-initdb.d","read_only":true}]' \
  '.services.db.image = "postgres:17.12" | .services["db-functions"].image = "postgres:17.12"' \
  '.services["db-functions"].image = "ghcr.io/magpern/classbeat-db:9.9.9"' \
  '.services["db-functions"].user = "1000"' \
  '.services.worker.volumes = [{"type":"npipe","source":"//./pipe/docker_engine","target":"/p"}]' \
  '.services.worker.volumes = [{"type":"cluster","source":"x","target":"/c"}]' \
  '.services.worker.privileged = true' \
  '.services.db.devices = [{"source":"/dev/sda","target":"/dev/sda","permissions":"rwm"}]' \
  '.services.db.cap_add = ["DAC_READ_SEARCH"]' \
  '.services.web.cap_add = ["SYS_PTRACE"]' \
  '.services.migrate.device_cgroup_rules = ["b 8:* rmw"]' \
  '.services.worker.use_api_socket = true' \
  '.volumes["db-data"].labels = {"x":"y"}'; do
  expect_refused shared "$shared" "$m"
done
# Default deny, on EVERY variant (#517; the #512 review's B1 to B5, the numeric-root rule, and keys and
# services nobody has heard of). Each entry is FRAGMENT|MUTATION: the mutated render must be refused
# and the message must contain FRAGMENT, so a mutation that trips some other rule, or does not apply,
# cannot pass for the rule it is for.
sd='(.secrets.app_secret_key.file | rtrimstr("/app_secret_key"))'
deny=(
  # B1: another service's mounts, a volume that is a bind of any host path, shared namespaces
  'services.worker.volumes_from|.services.worker.volumes_from = ["web"]'
  'services.migrate.volumes_from|.services.migrate.volumes_from = ["web:ro"]'
  'volumes.db-data.driver_opts|.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":'"$sd"'}'
  'volumes.db-data.driver_opts|.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":"/"}'
  'volumes.db-data.driver|.volumes["db-data"].driver = "local-persist"'
  'volumes.db-data.external|.volumes["db-data"].external = true'
  'services.worker.pid|.services.worker.pid = "service:web"'
  'services.worker.pid|.services.worker.pid = "host"'
  'services.migrate.pid|.services.migrate.pid = "container:classbeat-prod-web-1"'
  'services.worker.ipc|.services.worker.ipc = "service:web"'
  'services.db.ipc|.services.db.ipc = "host"'
  # B2: binds of /proc, /proc/1/root, the Docker socket and the secrets folder itself
  'services.worker.volumes|.services.worker.volumes = [{"type":"bind","source":"/proc","target":"/hostproc","read_only":true}]'
  'services.worker.volumes|.services.worker.volumes = [{"type":"bind","source":"/proc/1/root","target":"/h","read_only":true}]'
  'services.web.volumes|.services.web.volumes = [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]'
  'services.migrate.volumes|.services.migrate.volumes = [{"type":"bind","source":'"$sd"',"target":"/s"}]'
  'no bind mounts|.services.db.volumes += [{"type":"bind","source":"/proc","target":"/hostproc"}]'
  'no bind mounts|.services.db.volumes += [{"type":"bind","source":"/proc/1/root","target":"/h"}]'
  'no bind mounts|.services.db.volumes += [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]'
  'no bind mounts|.services.db.volumes += [{"type":"npipe","source":"//./pipe/docker_engine","target":"/p"}]'
  'bind|.services.db.volumes += [{"type":"bind","source":'"$sd"',"target":"/s"}]'
  # B3: configs (a file config is a bind of any host path)
  'configs|.configs = {"c":{"name":"x","file":"/proc"}} | .services.worker.configs = [{"source":"c","target":"/hostproc"}]'
  'configs|.configs = {"c":{"name":"x","file":"/"}} | .services.worker.configs = [{"source":"c","target":"/host"}]'
  'configs|.configs = {"c":{"name":"x","file":"/var/run/docker.sock"}} | .services.web.configs = [{"source":"c","target":"/s"}]'
  'configs|.configs = {"c":{"name":"x","file":'"$sd"'}} | .services.worker.configs = [{"source":"c","target":"/s"}]'
  'configs|.configs = {"c":{"name":"x","file":"/proc"}}'
  'services.worker.configs|.services.worker.configs = [{"source":"c","target":"/s"}]'
  # B4: lifecycle hooks with their own user and privileges
  'services.web.post_start|.services.web.post_start = [{"command":["sh","-c","id"],"user":"root","privileged":true}]'
  'services.worker.pre_stop|.services.worker.pre_stop = [{"command":["sh","-c","id"]}]'
  # B5: a service behind profiles (the render holds it because of --profile "*"): a new service
  'services.spy|.services.spy = {"image":"busybox","profiles":["ops"],"pid":"host","privileged":true,"group_add":["10010"],"volumes":[{"type":"bind","source":"/","target":"/host"}],"logging":.services.web.logging}'
  'services.web.profiles|.services.web.profiles = ["ops"]'
  # root, however written (a numeric zero, a sign, a group, the name)
  'services.worker.user|.services.worker.user = "00"'
  'services.web.user|.services.web.user = "0000:0"'
  'services.migrate.user|.services.migrate.user = "10001:0"'
  'services.worker.user|.services.worker.user = "10001:root"'
  'services.web.user|.services.web.user = "10001"'
  'runs as root|.services.db.user = "+0"'
  'runs as root|.services.db.user = "0"'
  'runs as root|.services.db.user = "0:0"'
  'runs as root|.services["db-functions"].user = "0000:0"'
  'runs as root|.services.db.user = "postgres:0"'
  'runs as root|.services["db-functions"].user = "999:root"'
  'runs as root|.services.db.user = "root"'
  'runs as root|.services.db.user = "00"'
  'runs as postgres|.services.db.user = "1000"'
  'runs as postgres|del(.services["db-functions"].user)'
  'runs as root|.services.db.user = 0'
  # a brand-new key, a brand-new top-level key and a brand-new service
  'services.web.brand_new_compose_key|.services.web.brand_new_compose_key = true'
  'services.db.brand_new_compose_key|.services.db.brand_new_compose_key = {"a":1}'
  'brand_new_top_level|.brand_new_top_level = {}'
  'services.brand_new_service|.services.brand_new_service = {"image":.services.web.image,"logging":.services.web.logging}'
  # host-reaching keys the issue names
  'services.worker.userns_mode|.services.worker.userns_mode = "host"'
  'services.worker.cgroup|.services.worker.cgroup = "host"'
  'services.worker.cgroup_parent|.services.worker.cgroup_parent = "/"'
  'services.worker.uts|.services.worker.uts = "host"'
  'services.worker.runtime|.services.worker.runtime = "runc"'
  'services.worker.gpus|.services.worker.gpus = "all"'
  'services.worker.provider|.services.worker.provider = {"type":"x"}'
  'services.worker.env_file|.services.worker.env_file = [{"path":"./secrets/app_secret_key"}]'
  'services.worker.label_file|.services.worker.label_file = ["./secrets/app_secret_key"]'
  'services.worker.privileged|.services.worker.privileged = true'
  'services.db.devices|.services.db.devices = [{"source":"/dev/sda","target":"/dev/sda","permissions":"rwm"}]'
  'services.db.cap_add|.services.db.cap_add = ["DAC_READ_SEARCH"]'
  'services.migrate.device_cgroup_rules|.services.migrate.device_cgroup_rules = ["b 8:* rmw"]'
  'services.worker.use_api_socket|.services.worker.use_api_socket = true'
  'services.web.network_mode|.services.web.network_mode = "host"'
  # security_opt exactly no-new-privileges, cap_drop exactly ALL, group_add exactly 10010
  'security_opt is exactly|.services.worker.security_opt += ["seccomp=unconfined"]'
  'security_opt is exactly|.services.web.security_opt = ["no-new-privileges:true","apparmor=unconfined"]'
  'security_opt is exactly|.services.migrate.security_opt = ["no-new-privileges:true","systempaths=unconfined"]'
  'security_opt is exactly|.services.worker.security_opt = ["no-new-privileges:true","label=disable"]'
  'cap_drop is exactly|.services.migrate.cap_drop = ["NET_RAW"]'
  'cap_drop is exactly|.services.migrate.cap_drop = ["ALL","CHOWN"]'
  'group_add is exactly|.services.worker.group_add = ["10010","0"]'
  'group_add is exactly|.services.db.group_add = ["0"]'
  # nested blocks
  'services.worker.secrets[].mode|.services.worker.secrets[0].mode = 292'
  'services.worker.secrets[].uid|.services.worker.secrets[0].uid = "0"'
  'services.db.volumes[].bind|.services.db.volumes[0].bind = {"create_host_path":true}'
  'services.db.volumes[].volume.subpath|.services.db.volumes[0].volume.subpath = "x"'
  'services.web.healthcheck.start_interval|.services.web.healthcheck.start_interval = "1s"'
  'services.web.logging.options.tag|.services.web.logging.options.tag = "x"'
  'every service logs with json-file|.services.web.logging.driver = "syslog"'
  'services.web.ports[].x_extra|.services.web.ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8080","x_extra":1}]'
  'services.worker.depends_on.*.restart|.services.worker.depends_on["db-functions"].restart = true'
  'services.worker.networks.*.ipv4_address|.services.worker.networks.egress = {"ipv4_address":"10.0.0.9"}'
  'networks.egress.driver_opts|.networks.egress.driver_opts = {"x":"y"}'
  'networks.backend.ipam.driver|.networks.backend.ipam.driver = "x"'
  'networks.egress.attachable|.networks.egress.attachable = true'
  'secrets.app_secret_key.x_extra|.secrets.app_secret_key.x_extra = 1'
  # a DNS alias anywhere but web's classbeat-web on proxy (#1481 review)
  'no network alias|.services.worker.networks.egress = {"aliases":["api.anthropic.com"]}'
  'no network alias|.services.migrate.networks.backend = {"aliases":["db"]}'
  'no network alias|.services.web.networks.backend = {"aliases":["db"]}'
  # a changed command, entrypoint or tmpfs
  'command, entrypoint and tmpfs|.services.web.entrypoint = ["sh","-c","id"]'
  'command, entrypoint and tmpfs|.services.db.command = ["postgres","-c","x=y"]'
  'command, entrypoint and tmpfs|.services["db-functions"].entrypoint = ["sh","-c","id"]'
  'command, entrypoint and tmpfs|.services.web.tmpfs = ["/tmp:exec"]'
  'services.worker.tmpfs|.services.worker.tmpfs = ["/x"]'
  # variables by name (#610): what an env_file, however spelled, leaves in the render
  'services.worker.environment: 1 variable|.services.worker.environment[""] = "a-secret-file-line"'
  'services.web.environment: 1 variable|.services.web.environment.SPY = "1"'
  'services.db.environment: 1 variable|.services.db.environment.CLASSBEAT_DATABASE_URL = "x"'
  'services.worker.environment: 2 variable|.services.worker.environment += {"CLASSBEAT_MAINT_DATABASE_URL":"x","A":"b"}'
  'services.migrate.environment lacks CLASSBEAT_MIGRATIONS_DATABASE_URL|del(.services.migrate.environment.CLASSBEAT_MIGRATIONS_DATABASE_URL)'
  # top-level networks by key, and the project's own by name (#598 review, finding 5)
  'networks.spy|.networks.spy = {"name":"classbeat-prod-check_spy","ipam":{}}'
  'networks.backendx|.networks.backendx = {"name":"classbeat-prod-check_backendx","ipam":{}}'
  'networks.egress (external or renamed)|.networks.egress.name = "some-other-network"'
  'networks.egress (external or renamed)|.networks.egress.external = true'
  'networks.backend (external or renamed)|.networks.backend.name = "bridge"'
  # the CPU and memory ceilings of ADR 0026 stage 1 (#735), and the keys still default-deny
  'keep a cpus and a mem_limit ceiling|del(.services.worker.mem_limit)'
  'keep a cpus and a mem_limit ceiling|del(.services.web.cpus)'
  'keep a cpus and a mem_limit ceiling|.services.worker.cpus = 0'
  'services.migrate.cpus|.services.migrate.cpus = 1'
  'services.worker.memswap_limit|.services.worker.memswap_limit = "1"'
)
for v in shared loopback lan withspotdown; do
  case "$v" in shared) base="$shared" ;; loopback) base="$loopback" ;; lan) base="$lan" ;; *) base="$spotdown" ;; esac
  for e in "${deny[@]}"; do
    expect_refused "$v" "$base" "${e#*|}" "${e%%|*}"
  done
  # the allow-list is not a refuse-everything: an extension key and the files as they are pass
  expect_accepted "$v" "$base" '.["x-note"] = {"a":1}'
  expect_accepted "$v" "$base" '.'
  # The allow-list itself passes the shipped files through the real-render check (no bridge overlay).
  expect_accepted real "$base" '.'
done
# spotdown's overlay (#1481, ADR 0040 section 10). Each entry is FRAGMENT|MUTATION of the spotdown render.
spot=(
  # a published port, a route out, web or another service on spotdown's networks
  'only web may publish a port|.services["spotdown-api"].ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8000"}]'
  'only web may publish a port|.services["spotdown-redis"].ports = [{"target":6379,"published":"6379"}]'
  'spotdown and spotdown-internal networks are internal|.networks.spotdown.internal = false'
  'spotdown and spotdown-internal networks are internal|del(.networks["spotdown-internal"].internal)'
  'spotdown-api joins spotdown and spotdown-internal|.services["spotdown-api"].networks.egress = null'
  'only classbeat'"'"'s worker and spotdown-api join the spotdown network|.services["spotdown-worker"].networks.spotdown = null'
  'only the three spotdown services join spotdown-internal|.services["spotdown-api"].networks |= del(.["spotdown-internal"])'
  'only classbeat'"'"'s worker and spotdown-api join the spotdown network|.services.web.networks.spotdown = null'
  'worker joins backend and egress only|.services.worker.networks["spotdown-internal"] = null'
  'only classbeat'"'"'s worker and spotdown-api join the spotdown network|.services.worker.networks |= del(.spotdown)'
  # the key: never on web, nowhere but the worker and spotdown-api; no other secret on spotdown
  'spotdown_service_key belongs to classbeat'"'"'s worker and spotdown-api only|.services.web.secrets += [{"source":"spotdown_service_key","target":"/run/secrets/spotdown_service_key"}]'
  'services.spotdown-worker.secrets|.services["spotdown-worker"].secrets = [{"source":"spotdown_service_key"}] | .services["spotdown-worker"].group_add = ["10010"]'
  'spotdown_service_key belongs to classbeat'"'"'s worker and spotdown-api only|.services.migrate.secrets += [{"source":"spotdown_service_key"}]'
  'spotdown_service_key belongs to classbeat'"'"'s worker and spotdown-api only|.services.worker.secrets |= map(select(.source != "spotdown_service_key"))'
  'spotdown-api mounts the service key and nothing else|.services["spotdown-api"].secrets += [{"source":"connection_kek"}]'
  'reads the key from /run/secrets/spotdown_service_key|.services["spotdown-api"].environment.SERVICE_KEY_FILE = "/data/key"'
  'services.web.environment: 1 variable|.services.web.environment.CLASSBEAT_SPOTDOWN_URL = "http://spotdown-api:8000"'
  'CLASSBEAT_SPOTDOWN_URL is http://spotdown-api:8000|.services.worker.environment.CLASSBEAT_SPOTDOWN_URL = "https://spotdown.example.test"'
  'services.worker.environment lacks CLASSBEAT_SPOTDOWN_URL|del(.services.worker.environment.CLASSBEAT_SPOTDOWN_URL)'
  # the image: a release tag, never latest
  'a release tag (X.Y.Z, never latest)|.services["spotdown-api"].image = "ghcr.io/magpern/spotdown:latest"'
  'a release tag (X.Y.Z, never latest)|.services["spotdown-worker"].image = "ghcr.io/magpern/spotdown:1"'
  'a release tag (X.Y.Z, never latest)|.services["spotdown-worker"].image = "ghcr.io/magpern/spotdown:9.9.9"'
  'a release tag (X.Y.Z, never latest)|.services["spotdown-api"].image = "example.test/spotdown:0.0.0"'
  'pinned redis:X.Y.Z-alpine|.services["spotdown-redis"].image = "redis:7-alpine"'
  'pinned redis:X.Y.Z-alpine|.services["spotdown-redis"].image = "redis:latest"'
  'pinned redis:X.Y.Z-alpine|.services["spotdown-redis"].image = "redis:7.4.12-alpine"'
  'pinned redis:X.Y.Z-alpine|.services["spotdown-redis"].image = "redis:7.4.12-alpine@sha256:00"'
  # root, capabilities, ceilings, healthchecks
  'names a non-root uid:gid|del(.services["spotdown-worker"].user)'
  'runs as root|.services["spotdown-redis"].user = "0"'
  'runs as root|.services["spotdown-api"].user = "10001:0"'
  'names its non-root user|del(.services["spotdown-api"].user)'
  'every service that mounts a secret has group_add|del(.services["spotdown-api"].group_add)'
  'drops every capability|del(.services["spotdown-worker"].cap_drop)'
  'drops every capability|del(.services["spotdown-redis"].security_opt)'
  'services.spotdown-api.cap_add|.services["spotdown-api"].cap_add = ["SYS_ADMIN"]'
  'services.spotdown-worker.privileged|.services["spotdown-worker"].privileged = true'
  'keeps a cpus and a mem_limit ceiling|del(.services["spotdown-worker"].mem_limit)'
  'keeps a cpus and a mem_limit ceiling|del(.services["spotdown-api"].cpus)'
  'keeps a cpus and a mem_limit ceiling|.services["spotdown-redis"].cpus = 0'
  'keep their own healthchecks|del(.services["spotdown-api"].healthcheck)'
  'keep their own healthchecks|del(.services["spotdown-worker"].healthcheck)'
  'keep their own healthchecks|.services["spotdown-worker"].healthcheck.test = ["CMD","python","-m","app.healthcheck","api"]'
  'keep their own healthchecks|.services["spotdown-api"].healthcheck.test = ["CMD","python","-c","import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8000/healthz\")"]'
  'keep their own healthchecks|.services["spotdown-redis"].healthcheck.test = ["CMD","true"]'
  # the image's own data path (/data) and users, read-only with /tmp the only tmpfs, bounded memory
  'service mode without UI|.services["spotdown-api"].environment.DATA_DIR = "/app/data"'
  'service mode without UI|.services["spotdown-worker"].environment.DATA_DIR = "/app/data"'
  'spotdown-data (/data) and spotdown-redis (/data), nothing else|.services["spotdown-worker"].volumes[0].target = "/app/data"'
  'spotdown-data (/data) and spotdown-redis (/data), nothing else|.services["spotdown-api"].volumes[0].target = "/app/data"'
  '999:1000, exactly|.services["spotdown-redis"].user = "999:10010"'
  '999:1000, exactly|.services["spotdown-api"].user = "10002:10002"'
  '999:1000, exactly|.services["spotdown-worker"].user = "10001:10010"'
  'is read-only|.services["spotdown-worker"].read_only = false'
  'is read-only|del(.services["spotdown-redis"].read_only)'
  'is read-only|del(.services["spotdown-api"].read_only)'
  'command, entrypoint and tmpfs|.services["spotdown-worker"].tmpfs = ["/tmp:exec"]'
  'command, entrypoint and tmpfs|.services["spotdown-api"].tmpfs += ["/data"]'
  'services.spotdown-redis.tmpfs|.services["spotdown-redis"].tmpfs = ["/tmp"]'
  'at most 64g each, and above 3g on spotdown-worker|.services["spotdown-api"].mem_limit = "99999999999999"'
  'at most 64g each, and above 3g on spotdown-worker|.services["spotdown-worker"].mem_limit = "3221225472"'
  'at most 64g each, and above 3g on spotdown-worker|.services["spotdown-worker"].mem_limit = "2147483648"'
  # a DNS alias on a network classbeat'"'"'s worker shares could take a name it resolves
  'no network alias|.services["spotdown-api"].networks.spotdown = {"aliases":["db"]}'
  'no network alias|.services["spotdown-api"].networks.spotdown = {"aliases":["api.anthropic.com"]}'
  'no network alias|.services["spotdown-redis"].networks["spotdown-internal"] = {"aliases":["spotdown-api"]}'
  # service mode, no UI, YouTube and the url source off
  'service mode without UI|.services["spotdown-api"].environment.SERVICE_MODE = "false"'
  'service mode without UI|.services["spotdown-api"].environment.SERVE_UI = "true"'
  'service mode without UI|.services["spotdown-worker"].environment.ALLOW_YTDLP_SOURCING = "true"'
  'service mode without UI|.services["spotdown-api"].environment.URL_ALLOWED_HOSTS = "example.test"'
  'services.spotdown-api.environment: 1 variable|.services["spotdown-api"].environment.SPOTIFY_CLIENT_SECRET = "x"'
  'services.spotdown-api.environment: 1 variable|.services["spotdown-api"].environment.SPOTDOWN_UI = "on"'
  'services.spotdown-worker.environment: 1 variable|.services["spotdown-worker"].environment.SERVICE_KEY_FILE = "/run/secrets/spotdown_service_key"'
  # volumes, the command, the service set
  'only the spotdown services mount|.services.db.volumes += [{"type":"volume","source":"spotdown-data","target":"/d","volume":{}}]'
  'spotdown-data (/data) and spotdown-redis (/data), nothing else|.services["spotdown-api"].volumes += [{"type":"volume","source":"db-data","target":"/db","volume":{}}]'
  'the only named volume is db-data|.volumes["spotdown-extra"] = {"name":"x"}'
  'command, entrypoint and tmpfs|.services["spotdown-worker"].command = ["sh","-c","id"]'
  'command, entrypoint and tmpfs|.services["spotdown-redis"].command = ["redis-server","--protected-mode","no"]'
  'the three services spotdown-api, spotdown-worker and spotdown-redis|del(.services["spotdown-redis"])'
  'services.spotdown-extra|.services["spotdown-extra"] = {"image":.services["spotdown-api"].image,"logging":.services.web.logging}'
)
for e in "${spot[@]}"; do
  expect_refused withspotdown "$spotdown" "${e#*|}" "${e%%|*}"
done
expect_accepted withspotdown "$spotdown" '.services["spotdown-api"].image = "ghcr.io/magpern/spotdown:1.2.3-rc.1" | .services["spotdown-worker"].image = "ghcr.io/magpern/spotdown:1.2.3-rc.1"'
# Without the overlay, any one spotdown piece is refused: all of spotdown or none of it.
for v in shared loopback lan; do
  case "$v" in shared) base="$shared" ;; loopback) base="$loopback" ;; *) base="$lan" ;; esac
  expect_refused "$v" "$base" '.services.worker.environment.CLASSBEAT_SPOTDOWN_URL = "http://spotdown-api:8000"' 'services.worker.environment: 1 variable'
done
for m in \
  '.secrets.spotdown_service_key = {"file": (.secrets.app_secret_key.file | rtrimstr("app_secret_key") + "spotdown_service_key")} | .services.worker.secrets += [{"source":"spotdown_service_key"}]' \
  '.volumes["spotdown-data"] = {"name":"classbeat-prod-check_spotdown-data"}' \
  '.networks.spotdown = {"name":"classbeat-prod-check_spotdown","internal":true} | .services.worker.networks.spotdown = null'; do
  for v in shared loopback lan; do
    case "$v" in shared) base="$shared" ;; loopback) base="$loopback" ;; *) base="$lan" ;; esac
    expect_refused "$v" "$base" "$m" "spotdown"
  done
done
# A refused variable's name and value are never printed (an env_file of a secret file puts the secret
# in either, #610).
expect_refused shared "$shared" '.services.worker.environment["name-canary-610"] = "value-canary-610"' 'services.worker.environment: 1 variable'
if grep -qE 'name-canary-610|value-canary-610' "$tmp/refuse.log"; then
  echo "check-prod-compose: self-test: a refused variable's name or value was printed" >&2; exit 1
fi

# The client secret's value (the canary in the dummy file) anywhere in the rendered file.
expect_refused shared "$shared" ".services.web.labels = {\"note\": \"$oauth_canary-google\"}"
for m in \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "a-literal-in-compose"' \
  '.services.web.environment.CLASSBEAT_OAUTH_VISIBLE_PROVIDERS = "google,microsoft"' \
  'del(.services.web.environment.CLASSBEAT_OAUTH_MICROSOFT_CLIENT_ID)'; do
  expect_refused oauth_defaults "$defaults" "$m"
done
# The dev compose.yaml as it is (#521): the same rule as the production files.
check_oauth_files "$root" compose.yaml || exit 1
# The compose files as written (copies; the checkout's files are never changed).
mkdir "$tmp/files"
expect_files_refused() {
  cp "$tmp/compose.prod.yaml" "$tmp/compose.prod.loopback.yaml" "$root/compose.yaml" "$tmp/files/"
  sed -i -E "$2" "$tmp/files/$1"
  if (check_oauth_files "$tmp/files" compose.prod.yaml compose.prod.loopback.yaml compose.yaml 2>/dev/null); then
    echo "check-prod-compose: self-test: accepted an unsafe $1: $2" >&2; exit 1
  fi
}
# shellcheck disable=SC2016  # a literal compose variable, not a shell one
expect_files_refused compose.prod.yaml \
  's|^(      CLASSBEAT_OAUTH_VISIBLE_PROVIDERS:.*)$|\1\n      CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET: ${OAUTH_GOOGLE_CLIENT_SECRET:-}|'
expect_files_refused compose.prod.yaml 's|^  oauth_google_client_secret:$|  oauth_google_client_secret: {content: abc}|'
expect_files_refused compose.prod.yaml 's|file: \./secrets/oauth_github_client_secret|environment: OAUTH_GITHUB_CLIENT_SECRET|'
expect_files_refused compose.prod.loopback.yaml \
  's|^(      CLASSBEAT_TRUSTED_PROXIES:.*)$|\1\n      OAUTH_CLIENT_SECRET: abc|'
# shellcheck disable=SC2016  # a literal compose variable, not a shell one
expect_files_refused compose.yaml \
  's|^(      CLASSBEAT_OAUTH_VISIBLE_PROVIDERS:.*)$|\1\n      CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET: ${OAUTH_GOOGLE_CLIENT_SECRET:-}|'
expect_files_refused compose.yaml 's|file: \./secrets/oauth_github_client_secret|environment: OAUTH_GITHUB_CLIENT_SECRET|'
