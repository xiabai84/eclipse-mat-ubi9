# Design: Helm Chart for Eclipse MAT Service on OpenShift

**Date:** 2026-03-02
**Status:** Approved

## Context

The Eclipse MAT Service runs as a Docker container (UBI9-minimal, non-root UID 1001, port 8080) with two volume mounts (`/heapdumps`, `/reports`). It needs a Helm chart for deployment on OpenShift clusters.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage | Separate PVCs for `/heapdumps` (50Gi) and `/reports` (10Gi) | Data persists across pod restarts; different sizing needs |
| Networking | OpenShift Route with TLS edge termination | Native OpenShift ingress; auto-generated hostname |
| Resources | Configurable via values.yaml, medium defaults (4Gi/16Gi) | Users tune per environment; MAT needs large JVM heap |
| Image registry | Generic external registry (configurable) | Maximum flexibility; optional imagePullSecrets |
| Scaling | Single replica default, no HPA | MAT is single-threaded CPU/memory-bound; horizontal scaling doesn't help |

## Chart Structure

```
helm/eclipse-mat-service/
├── Chart.yaml
├── values.yaml
├── templates/
│   ├── _helpers.tpl
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── route.yaml
│   ├── pvc-heapdumps.yaml
│   ├── pvc-reports.yaml
│   ├── configmap.yaml
│   └── serviceaccount.yaml
```

## Resources Produced

| Resource | Purpose |
|----------|---------|
| Deployment | Single replica, non-root UID 1001, port 8080 |
| Service (ClusterIP) | Internal routing on port 8080 |
| Route (OpenShift) | External TLS-edge-terminated hostname |
| PVC x2 | `/heapdumps` (RWO, 50Gi) and `/reports` (RWO, 10Gi) |
| ConfigMap | All env vars (MAT_TIMEOUT, LOG_LEVEL, analyzer thresholds, etc.) |
| ServiceAccount | Dedicated SA with optional image pull secrets |

## Key values.yaml Defaults

- `image.repository`: `eclipse-mat`, `image.tag`: `latest`
- `resources.requests`: 4Gi RAM / 1 CPU; `resources.limits`: 16Gi RAM / 4 CPU
- `persistence.heapdumps.size`: 50Gi; `persistence.reports.size`: 10Gi
- `route.enabled`: true; `route.tls.termination`: edge
- `config.matTimeout`: 600; `config.uvicornWorkers`: 4; `config.logLevel`: INFO

## Security

- `runAsUser: 1001`, `runAsNonRoot: true`, `fsGroup: 1001` (matches Dockerfile `mat` user)
- OpenShift SCC compatible (restricted-v2)

## Health Probes

- Liveness: `GET /health`, initialDelay 10s, period 30s
- Readiness: `GET /health`, initialDelay 5s, period 10s
