// Railway production topology for QuantDinger, mirroring docker-compose.yml.
//
// Secrets (SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY, ADMIN_*, MCP_AUTH_TOKEN) are
// Railway shared variables set outside source control and referenced through
// `ctx.shared`. Backend services build from GitHub `main` on every push that
// touches backend_api_python/. mcp stays unconnected until its Agent token
// exists; deploy it with
//   railway up mcp_server --path-as-root --service mcp
// Compose-only helpers are intentionally absent: `migration` and `kafka-init`
// run as the backend pre-deploy command, and `env-sync` only edits a local .env.
import {
  defineRailway,
  github,
  group,
  image,
  postgres,
  preserve,
  project,
  redis,
  service,
  volume,
} from "railway/iac";

// Singapore: Binance and Bybit geo-block US egress (HTTP 451/403).
const REGION = "asia-southeast1-eqsg3a";
// Start commands replace the image ENTRYPOINT, so call it explicitly to keep the
// SECRET_KEY validation, runtime volume ownership fix, and privilege drop.
const ENTRYPOINT = "/usr/local/bin/docker-entrypoint.sh";
// Pinned to what Railway provisions so plans stay drift-free.
const VOLUME = { region: REGION, sizeMB: 50000 };
const KAFKA_BOOTSTRAP = "${{Kafka.RAILWAY_PRIVATE_DOMAIN}}:9092";

// Deploy on push to main without waiting for CI (checkSuites off). Watch paths
// match from the repo root even with a root directory, so pushes elsewhere skip.
const fromRepo = (dir: string) => ({
  source: github("logic-panda/xqnt.ai", { branch: "main", rootDirectory: `/${dir}`, checkSuites: false }),
  build: { builder: "DOCKERFILE" as const, dockerfilePath: "Dockerfile", watchPatterns: [`/${dir}/**`] },
});

// Per-replica caps. Railway bills actual usage; caps only bound a runaway
// process. Sized at several times the measured idle footprint.
const GB = 1024 ** 3;
const size = (cpu: number, memoryGB: number) => ({
  restartPolicyType: "ALWAYS" as const,
  limitOverride: { containers: { cpu, memoryBytes: Math.round(memoryGB * GB) } },
});
// Services take placement via `replicas`; database nodes only honor the raw
// deploy field.
const placement = { replicas: { [REGION]: 1 } };
const dbSize = (cpu: number, memoryGB: number) => ({
  ...size(cpu, memoryGB),
  multiRegionConfig: { [REGION]: { numReplicas: 1 } },
});

// Railway's Redis template command plus the compose memory policy, so the
// cache evicts before it reaches the container cap.
const redisStart = (flags: string) =>
  `/bin/sh -c "rm -rf $RAILWAY_VOLUME_MOUNT_PATH/lost+found/ && exec docker-entrypoint.sh ` +
  `redis-server --requirepass $REDIS_PASSWORD --dir $RAILWAY_VOLUME_MOUNT_PATH ${flags}"`;

export default defineRailway((ctx) => {
  const pg = Object.assign(postgres("Postgres"), { deploy: dbSize(2, 2) });
  const cache = Object.assign(redis("Redis"), {
    deploy: {
      ...dbSize(0.5, 0.5),
      startCommand: redisStart("--save 60 1 --maxmemory 128mb --maxmemory-policy allkeys-lru"),
    },
  });
  // Celery broker/results: durable, never evicted (compose redis-jobs).
  const jobs = Object.assign(redis("Redis-jobs"), {
    deploy: {
      ...dbSize(0.5, 1),
      startCommand: redisStart(
        "--save 60 1000 --appendonly yes --appendfsync everysec --maxmemory 512mb --maxmemory-policy noeviction",
      ),
    },
  });

  const kafka = service("Kafka", {
    source: image("apache/kafka:4.3.1"),
    ...placement,
    deploy: size(1, 1.5),
    env: {
      // The image runs as a non-root user; Railway volumes mount root-owned.
      RAILWAY_RUN_UID: "0",
      KAFKA_NODE_ID: "1",
      KAFKA_PROCESS_ROLES: "broker,controller",
      KAFKA_LISTENERS: "CONTROLLER://:9093,PLAINTEXT://:9092",
      KAFKA_ADVERTISED_LISTENERS: "PLAINTEXT://${{RAILWAY_PRIVATE_DOMAIN}}:9092",
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT",
      KAFKA_CONTROLLER_LISTENER_NAMES: "CONTROLLER",
      KAFKA_INTER_BROKER_LISTENER_NAME: "PLAINTEXT",
      KAFKA_CONTROLLER_QUORUM_VOTERS: "1@localhost:9093",
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: "1",
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: "1",
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: "1",
      KAFKA_TRANSACTION_STATE_LOG_NUM_PARTITIONS: "12",
      KAFKA_OFFSETS_TOPIC_NUM_PARTITIONS: "12",
      KAFKA_NUM_PARTITIONS: "6",
      KAFKA_HEAP_OPTS: "-Xms256m -Xmx768m",
      KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS: "0",
      // Subdirectory keeps the volume's lost+found out of Kafka's log dir scan.
      KAFKA_LOG_DIRS: "/var/lib/kafka/data/logs",
      CLUSTER_ID: "MkU3OEVBNTcwNTJENDM2Qk",
    },
    volumeMounts: { "/var/lib/kafka/data": volume("kafka-data", VOLUME) },
  });

  // Mirrors x-worker-environment in docker-compose.yml.
  const runtimeEnv = {
    SECRET_KEY: ctx.shared.SECRET_KEY,
    CREDENTIAL_ENCRYPTION_KEY: ctx.shared.CREDENTIAL_ENCRYPTION_KEY,
    ADMIN_USER: ctx.shared.ADMIN_USER,
    ADMIN_PASSWORD: ctx.shared.ADMIN_PASSWORD,
    TZ: "Asia/Shanghai",
    LOG_FORMAT: "json",
    DATABASE_URL: pg.env.DATABASE_URL,
    DB_TYPE: "postgresql",
    REDIS_HOST: cache.env.REDISHOST,
    REDIS_PORT: cache.env.REDISPORT,
    REDIS_PASSWORD: cache.env.REDISPASSWORD,
    REDIS_CACHE_NAMESPACE: "quantdinger:cache:v1",
    CACHE_ENABLED: "true",
    CELERY_TASKS_ENABLED: "true",
    CELERY_REDIS_HOST: jobs.env.REDISHOST,
    CELERY_REDIS_PORT: jobs.env.REDISPORT,
    CELERY_REDIS_PASSWORD: jobs.env.REDISPASSWORD,
    CELERY_BROKER_DB: "0",
    CELERY_RESULT_DB: "1",
    DB_POOL_ACQUIRE_TIMEOUT: "10",
    DB_POOL_HEALTH_CHECK: "true",
    SKIP_AUTO_MIGRATE: "true",
    // Desktop brokers (IBKR TWS/Gateway) are unreachable from Railway.
    ALLOW_LOCAL_DESKTOP_BROKERS: "false",
    MARKET_CATALOG_AUTO_SYNC: "true",
    MARKET_CATALOG_SYNC_INTERVAL_SEC: "86400",
    KAFKA_BOOTSTRAP_SERVERS: KAFKA_BOOTSTRAP,
    LIVE_TRADING_WS_ORDER_ENTRY: "true",
    LIVE_TRADING_WS_ORDER_CONNECT_TIMEOUT_SEC: "2.5",
    LIVE_TRADING_WS_ORDER_RESPONSE_TIMEOUT_SEC: "4",
    LIVE_TRADING_WS_ORDER_IDLE_RECONNECT_SEC: "20",
    LIVE_TRADING_WS_ORDER_MAX_CONNECTIONS: "64",
  };

  // Pool sizes are trimmed from the compose defaults so every pool together
  // stays below the Railway Postgres default max_connections (100).
  // ponytail: raise pools together with Postgres max_connections when scaling.
  const backendService = (
    name: string,
    [cpu, memoryGB]: [number, number],
    command: string | undefined,
    env: Record<string, string>,
    extra: Record<string, unknown> = {},
  ) =>
    service(name, {
      ...fromRepo("backend_api_python"),
      ...(command ? { start: `${ENTRYPOINT} ${command}` } : {}),
      ...placement,
      deploy: size(cpu, memoryGB),
      env: { ...runtimeEnv, DB_APPLICATION_NAME: `quantdinger-${name}`, ...env },
      ...extra,
    });

  const backend = backendService(
    "backend",
    [2, 2],
    undefined,
    {
      QD_PROCESS_ROLE: "api",
      PORT: "5000",
      // Bind dual-stack so the private network (IPv6) reaches gunicorn.
      PYTHON_API_HOST: "[::]",
      PYTHON_API_PORT: "5000",
      INDICATOR_PREVIEW_DIR: "/app/data/indicator_previews",
      PROMETHEUS_MULTIPROC_DIR: "/tmp/quantdinger-prometheus",
      STRATEGY_COMMANDS_ENABLED: "true",
      DB_POOL_MIN: "2",
      DB_POOL_MAX: "10",
      DB_APPLICATION_NAME: "quantdinger-api",
      MARKET_EXECUTOR_WORKERS: "6",
      PORTFOLIO_EXECUTOR_WORKERS: "3",
      RESOURCE_FD_COOLDOWN_SEC: "180",
      PORTFOLIO_MONITOR_DUE_BATCH_LIMIT: "5",
      GUNICORN_WORKERS: "2",
      GUNICORN_THREADS: "4",
      FRONTEND_URL:
        "https://${{frontend.RAILWAY_PUBLIC_DOMAIN}},https://${{mobile.RAILWAY_PUBLIC_DOMAIN}}",
    },
    {
      // Replaces the compose `migration` and `kafka-init` one-shot services.
      // The runtime env sets SKIP_AUTO_MIGRATE=true; the migrate step must not.
      preDeploy: [
        `${ENTRYPOINT} sh -c "SKIP_AUTO_MIGRATE=false python -m app.commands.migrate && python -m app.commands.kafka_topics"`,
      ],
      healthcheck: "/api/health",
      healthcheckTimeout: 120,
      volumeMounts: { "/app/data": volume("backend-data", VOLUME) },
    },
  );

  const workers = [
    backendService("trading-worker", [1, 1], "python -m app.commands.trading_worker", {
      QD_PROCESS_ROLE: "trading",
      STRATEGY_COMMANDS_ENABLED: "false",
      DB_POOL_MIN: "4",
      DB_POOL_MAX: "16",
      DB_APPLICATION_NAME: "quantdinger-trading",
      KAFKA_CLIENT_ID: "quantdinger-trading",
    }),
    backendService(
      "kafka-audit-worker",
      [0.25, 0.25],
      "python -m app.commands.kafka_audit_worker",
      {
        QD_PROCESS_ROLE: "kafka-audit",
        DB_POOL_MIN: "1",
        DB_POOL_MAX: "3",
        DB_APPLICATION_NAME: "quantdinger-kafka-audit",
        KAFKA_AUDIT_GROUP_ID: "quantdinger-runtime-audit-v1",
      },
    ),
    backendService(
      "strategy-dispatcher-worker",
      [0.5, 0.5],
      "python -m app.commands.strategy_dispatcher_worker",
      {
        QD_PROCESS_ROLE: "strategy-dispatcher",
        DB_POOL_MIN: "1",
        DB_POOL_MAX: "4",
        DB_APPLICATION_NAME: "quantdinger-strategy-dispatcher",
        KAFKA_CLIENT_ID: "quantdinger-strategy-dispatcher",
        KAFKA_STRATEGY_DISPATCH_GROUP_ID: "quantdinger-strategy-dispatch-v1",
      },
    ),
    backendService(
      "strategy-evaluator-worker",
      [1, 1],
      "python -m app.commands.strategy_evaluator_worker",
      {
        QD_PROCESS_ROLE: "strategy-evaluator",
        DB_POOL_MIN: "1",
        DB_POOL_MAX: "4",
        DB_APPLICATION_NAME: "quantdinger-strategy-evaluator",
        KAFKA_CLIENT_ID: "quantdinger-strategy-evaluator",
        KAFKA_STRATEGY_EVALUATOR_GROUP_ID: "quantdinger-strategy-evaluator-active-v1",
      },
    ),
    backendService("scheduler-worker", [0.5, 0.75], "python -m app.commands.scheduler", {
      QD_PROCESS_ROLE: "scheduler",
      DB_POOL_MIN: "1",
      DB_POOL_MAX: "6",
      DB_APPLICATION_NAME: "quantdinger-scheduler",
    }),
    // Backtests and AI jobs run here; two prefork children.
    backendService(
      "celery-worker",
      [2, 2],
      "celery -A app.celery_app:celery_app worker --loglevel=INFO -Q jobs,ai,maintenance --concurrency=2",
      {
        QD_PROCESS_ROLE: "celery",
        DB_POOL_MIN: "1",
        DB_POOL_MAX: "6",
        DB_APPLICATION_NAME: "quantdinger-celery-worker",
      },
    ),
    backendService(
      "celery-beat",
      [0.25, 0.25],
      "celery -A app.celery_app:celery_app beat --loglevel=INFO --schedule=/app/data/celerybeat-schedule",
      {
        QD_PROCESS_ROLE: "celery",
        DB_POOL_MIN: "1",
        DB_POOL_MAX: "2",
        DB_APPLICATION_NAME: "quantdinger-celery-beat",
      },
      { volumeMounts: { "/app/data": volume("celery-beat-data", VOLUME) } },
    ),
  ];

  // Prebuilt nginx SPAs; BACKEND_URL is substituted into proxy_pass at start.
  // The image template hardcodes Docker's embedded DNS (127.0.0.11), which does
  // not exist on Railway. Point nginx at the container's resolv.conf nameserver
  // (IPv6 on Railway) before the stock entrypoint renders the template.
  // ponytail: drop this override once the frontend image stops hardcoding 127.0.0.11.
  const NGINX_START =
    `/bin/sh -c 'ns=$(awk "/^nameserver/{print \\$2; exit}" /etc/resolv.conf); ` +
    `case "$ns" in *:*) ns="[$ns]";; esac; ` +
    `sed -i "s|resolver [^;]*;|resolver $ns valid=10s;|" /etc/nginx/templates/default.conf.template ` +
    `&& exec /docker-entrypoint.sh nginx -g "daemon off;"'`;
  const webService = (name: string, imageName: string) =>
    service(name, {
      source: image(imageName),
      start: NGINX_START,
      ...placement,
      deploy: size(0.25, 0.25),
      env: {
        PORT: "80",
        BACKEND_URL: "http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:5000",
      },
    });

  const frontend = webService("frontend", "ghcr.io/openbyteinc/quantdinger-frontend:latest");
  const mobile = webService("mobile", "ghcr.io/openbyteinc/quantdinger-mobile:latest");

  // Network MCP gateway (compose `mcp` profile). Clients authenticate with
  // MCP_AUTH_TOKEN; upstream calls use an Agent Gateway token that an admin
  // issues in the app and sets directly on this service (never in source).
  const mcp = service("mcp", {
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    ...placement,
    deploy: size(0.25, 0.25),
    env: {
      PORT: "7800",
      QUANTDINGER_BASE_URL: "http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:5000",
      QUANTDINGER_AGENT_TOKEN: preserve(),
      QUANTDINGER_MCP_TRANSPORT: "streamable-http",
      QUANTDINGER_MCP_HOST: "0.0.0.0",
      QUANTDINGER_MCP_PORT: "7800",
      QUANTDINGER_MCP_PUBLIC_URL: "https://${{RAILWAY_PUBLIC_DOMAIN}}",
      QUANTDINGER_MCP_AUTH_TOKEN: ctx.shared.MCP_AUTH_TOKEN,
    },
  });

  return project("xqnt-ai", {
    environments: ["production"],
    resources: [
      group("Data", [pg, cache, jobs, kafka]),
      group("API", [backend, frontend, mobile, mcp]),
      group("Workers", workers),
    ],
  });
});
