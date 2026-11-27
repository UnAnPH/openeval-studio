# Kubernetes deployment guide

Manifests for a local Kind/Minikube cluster or a lightweight K3s/AKS/EKS cluster.
They are a starting point, not a production reference: read the database note first.

## 1. What the manifests contain

- **`deployment.yaml`** — 2 replicas, rolling updates, `/api/health` liveness and
  readiness probes, non-root security context, 512 MiB memory limit, 5 GiB PVC.
- **`service.yaml`** — `ClusterIP` on port 80 → container port 8000.
- **`ingress.yaml`** — a placeholder host with cert-manager TLS. Edit `host`/`tls` to
  a domain you control; the hosted demo is <https://openeval.studio/demo>.
- **`configmap.yaml` / `secret.yaml`** — non-secret env and API keys. The ConfigMap
  still carries `WATCHER_STORAGE_DIR`, a legacy path; PostgreSQL is authoritative.
- **`job-eval-sample.yaml`** — ephemeral batch evaluation pod with
  `ttlSecondsAfterFinished: 300`.

> **Database required.** The app talks to PostgreSQL 16 (`DATABASE_URL`,
> `server/db.py`) and `/api/health` returns **503** without it. The stock manifests do
> not include a database and the ConfigMap does not set `DATABASE_URL`, so the
> readiness probe will fail until you either deploy PostgreSQL in-cluster or point
> `DATABASE_URL` at a managed instance. Apply migrations once
> (`uv run alembic upgrade head`, or a `Job`) before the app serves traffic.

Also note the 2 replicas share one `ReadWriteOnce` PVC. That works on a single-node
Kind cluster; on multi-node clusters use a `ReadWriteMany` class or move session state
to PostgreSQL/Redis and drop the volume.

## 2. Local cluster (Kind)

```bash
brew install kind kubectl
kind create cluster --name openeval

kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml          # edit the placeholder keys first
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

kubectl get pods -l app=openeval-studio
kubectl rollout status deployment/openeval-studio
kubectl port-forward svc/openeval-service 8000:80
```

Open `http://localhost:8000`. To exercise the public path, set an ingress host you
control, install an ingress controller plus cert-manager, then
`kubectl apply -f k8s/ingress.yaml`.

## 3. Public exposure without cloud cost

```bash
brew install cloudflare/cloudflare/cloudflared
cloudflared tunnel --url http://localhost:8000
```

Point a CNAME for a domain you own at the tunnel URL. Do not reuse the placeholder
host from `ingress.yaml` unless you control that zone.

## 4. Minimal production checklist

1. Create a PostgreSQL 16 service (or managed DB), set `DATABASE_URL` in the Secret,
   and run Alembic `0001`–`0004`.
2. Replace placeholder `GEMINI_API_KEY` / `OPENEVAL_API_KEY` values; add
   `SESSION_SECRET` for cookie signing.
3. Set `OPENEVAL_DEMO_SEED=0` unless you intentionally want the demo tenant.
4. Pin an image digest instead of `:latest`, set resource requests/limits from real
   measurements, and restrict ingress to your domain.
5. Decide whether you need `WATCHER_STORAGE_DIR` at all; the runtime gate and the
   Analyzer read from PostgreSQL.
