# Kubernetes Deployment Guide for OpenEval Studio

Production-ready Kubernetes manifests for deploying OpenEval Studio across local clusters (Kind/Minikube) or lightweight cloud clusters (K3s, AKS, EKS).

---

## 1. Architecture in Kubernetes

- **`Deployment` (`deployment.yaml`):** Runs 2 stateless replicas of OpenEval Studio with rolling updates, HTTP liveness/readiness health probes (`/api/health`), and memory boundaries.
- **`Service` (`service.yaml`):** Exposes pods internally on port 80 via `ClusterIP`.
- **`Ingress` (`ingress.yaml`):** Routes incoming traffic for `demo.openeval.studio` with automated TLS (compatible with Traefik, Nginx Ingress, and Cloudflare Tunnels).
- **`ConfigMap` & `Secret` (`configmap.yaml`, `secret.yaml`):** Decouples environment variables and API keys from container images.
- **`Job` (`job-eval-sample.yaml`):** Demonstrates **ephemeral batch evaluation** — spinning up isolated pods for Inspect AI benchmarks with dedicated resource quotas that clean themselves up post-run (`ttlSecondsAfterFinished: 300`).

---

## 2. Quickstart: 100% Free Local Cluster (Kind or Minikube)

You can run the entire Kubernetes cluster locally on your Mac with zero cloud cost:

```bash
# 1. Create a local cluster using Kind
brew install kind
kind create cluster --name openeval

# 2. Apply the manifests
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 3. Verify rollout
kubectl get pods -l app=openeval-studio
kubectl rollout status deployment/openeval-studio

# 4. Port forward to view in your browser
kubectl port-forward svc/openeval-service 8000:80
```
Visit `http://localhost:8000` to interact with your live Kubernetes deployment!

---

## 3. Public Exposure via Free Cloudflare Tunnel ($0 / month)

To expose your local Kubernetes cluster to the public internet at `https://demo.openeval.studio` without opening firewall ports or paying cloud fees:

```bash
brew install cloudflare/cloudflare/cloudflared
cloudflared tunnel --url http://localhost:8000
```
Point your domain CNAME on Cloudflare or Name.com to the tunnel URL. You now have a live, public Kubernetes portfolio deployment!
