# Helm Chart for Eclipse MAT Service on OpenShift — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a production-ready Helm chart that deploys the Eclipse MAT Service on OpenShift with persistent storage, health probes, configurable resources, and TLS-terminated Route.

**Architecture:** Single Deployment with two PVCs (`/heapdumps`, `/reports`), a ClusterIP Service, an OpenShift Route (TLS edge), a ConfigMap for all env vars, and a ServiceAccount. All values configurable via `values.yaml`.

**Tech Stack:** Helm 3, Kubernetes/OpenShift YAML manifests, Go templates

---

### Task 1: Chart metadata — `Chart.yaml`

**Files:**
- Create: `helm/eclipse-mat-service/Chart.yaml`

**Step 1: Create directory structure**

```bash
mkdir -p helm/eclipse-mat-service/templates
```

**Step 2: Write Chart.yaml**

```yaml
apiVersion: v2
name: eclipse-mat-service
description: Eclipse MAT heap analysis service for OpenShift
type: application
version: 0.1.0
appVersion: "3.1.0"
keywords:
  - eclipse-mat
  - heap-analysis
  - java
  - memory-profiling
home: https://github.com/xiabai84/eclipse-mat-service
```

**Step 3: Commit**

```bash
git add helm/eclipse-mat-service/Chart.yaml
git commit -m "feat(helm): add Chart.yaml metadata"
```

---

### Task 2: Values file — `values.yaml`

**Files:**
- Create: `helm/eclipse-mat-service/values.yaml`

**Step 1: Write values.yaml**

All defaults match the Dockerfile and `backend/config.py`.

```yaml
# -- Number of pod replicas (MAT is CPU/memory-bound; 1 is recommended)
replicaCount: 1

image:
  # -- Container image repository
  repository: eclipse-mat
  # -- Image tag
  tag: latest
  # -- Image pull policy
  pullPolicy: IfNotPresent

# -- Image pull secrets for private registries
imagePullSecrets: []

# -- Override the chart name
nameOverride: ""
# -- Override the full release name
fullnameOverride: ""

serviceAccount:
  # -- Create a dedicated ServiceAccount
  create: true
  # -- Annotations for the ServiceAccount
  annotations: {}
  # -- Override the ServiceAccount name
  name: ""

service:
  # -- Service type
  type: ClusterIP
  # -- Service port
  port: 8080

route:
  # -- Create an OpenShift Route
  enabled: true
  # -- Annotations for the Route
  annotations: {}
  # -- Hostname override (leave empty for auto-generated)
  host: ""
  tls:
    # -- TLS termination type (edge, passthrough, reencrypt)
    termination: edge
    # -- Redirect HTTP to HTTPS
    insecureEdgeTerminationPolicy: Redirect

resources:
  requests:
    memory: "4Gi"
    cpu: "1"
  limits:
    memory: "16Gi"
    cpu: "4"

persistence:
  heapdumps:
    # -- Enable PVC for /heapdumps
    enabled: true
    # -- Storage size for heapdumps
    size: 50Gi
    # -- Storage class (empty = cluster default)
    storageClassName: ""
    # -- Access mode
    accessMode: ReadWriteOnce
  reports:
    # -- Enable PVC for /reports
    enabled: true
    # -- Storage size for reports
    size: 10Gi
    # -- Storage class (empty = cluster default)
    storageClassName: ""
    # -- Access mode
    accessMode: ReadWriteOnce

# -- Pod-level security context
podSecurityContext:
  runAsUser: 1001
  runAsNonRoot: true
  fsGroup: 1001

# -- Container-level security context
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: false
  capabilities:
    drop:
      - ALL

# -- Node selector labels
nodeSelector: {}

# -- Tolerations for pod scheduling
tolerations: []

# -- Affinity rules for pod scheduling
affinity: {}

# -- Additional pod annotations
podAnnotations: {}

# -- Additional pod labels
podLabels: {}

# -- Application configuration (mapped to env vars via ConfigMap)
config:
  matTimeout: "600"
  uvicornWorkers: "4"
  logLevel: "INFO"
  logJson: "false"
  maxUploadSizeBytes: "21474836480"
  # -- Suspects analyzer thresholds (SUSPECTS_ prefix)
  suspects:
    primaryLeakHighMb: "500"
    significantSuspectMb: "50"
    significantSuspectRatio: "0.2"
    secondaryLeakHighMb: "200"
    heapLeakCriticalPct: "70"
    heapLeakWarningPct: "40"
  # -- Overview analyzer thresholds (OVERVIEW_ prefix)
  overview:
    largeHeapHighMb: "2048"
    largeHeapMediumMb: "1024"
    highObjectCount: "1000000"
    elevatedObjectCount: "500000"
    highClassloaderCount: "20"
    elevatedClassloaderCount: "10"
    highGcRootCount: "5000"
    threadLeakMb: "50"
    threadLeakSevereMb: "100"
    largeArrayMb: "100"
    largeStringMb: "100"
    largeCacheMb: "100"
  # -- Top Components analyzer thresholds (TOP_COMPONENTS_ prefix)
  topComponents:
    dominantClassloaderMb: "200"
    dominantClassloaderHighPct: "50"
    dominantConsumerMb: "500"
    dominantConsumerPct: "40"
    largeConsumerMb: "100"
    wasteProblemMb: "50"
    wasteWarningMb: "10"
```

**Step 2: Commit**

```bash
git add helm/eclipse-mat-service/values.yaml
git commit -m "feat(helm): add values.yaml with all configurable defaults"
```

---

### Task 3: Template helpers — `_helpers.tpl`

**Files:**
- Create: `helm/eclipse-mat-service/templates/_helpers.tpl`

**Step 1: Write _helpers.tpl**

```gotemplate
{{/*
Expand the name of the chart.
*/}}
{{- define "eclipse-mat-service.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "eclipse-mat-service.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version for the chart label.
*/}}
{{- define "eclipse-mat-service.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "eclipse-mat-service.labels" -}}
helm.sh/chart: {{ include "eclipse-mat-service.chart" . }}
{{ include "eclipse-mat-service.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "eclipse-mat-service.selectorLabels" -}}
app.kubernetes.io/name: {{ include "eclipse-mat-service.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use.
*/}}
{{- define "eclipse-mat-service.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "eclipse-mat-service.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
```

**Step 2: Commit**

```bash
git add helm/eclipse-mat-service/templates/_helpers.tpl
git commit -m "feat(helm): add template helpers"
```

---

### Task 4: ConfigMap — `configmap.yaml`

**Files:**
- Create: `helm/eclipse-mat-service/templates/configmap.yaml`

**Step 1: Write configmap.yaml**

Maps all `values.config.*` to the env var names expected by `backend/config.py`.

```gotemplate
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "eclipse-mat-service.fullname" . }}
  labels:
    {{- include "eclipse-mat-service.labels" . | nindent 4 }}
data:
  # Core settings
  MAT_TIMEOUT: {{ .Values.config.matTimeout | quote }}
  UVICORN_WORKERS: {{ .Values.config.uvicornWorkers | quote }}
  LOG_LEVEL: {{ .Values.config.logLevel | quote }}
  LOG_JSON: {{ .Values.config.logJson | quote }}
  MAX_UPLOAD_SIZE_BYTES: {{ .Values.config.maxUploadSizeBytes | quote }}
  # Suspects analyzer thresholds
  SUSPECTS_PRIMARY_LEAK_HIGH_MB: {{ .Values.config.suspects.primaryLeakHighMb | quote }}
  SUSPECTS_SIGNIFICANT_SUSPECT_MB: {{ .Values.config.suspects.significantSuspectMb | quote }}
  SUSPECTS_SIGNIFICANT_SUSPECT_RATIO: {{ .Values.config.suspects.significantSuspectRatio | quote }}
  SUSPECTS_SECONDARY_LEAK_HIGH_MB: {{ .Values.config.suspects.secondaryLeakHighMb | quote }}
  SUSPECTS_HEAP_LEAK_CRITICAL_PCT: {{ .Values.config.suspects.heapLeakCriticalPct | quote }}
  SUSPECTS_HEAP_LEAK_WARNING_PCT: {{ .Values.config.suspects.heapLeakWarningPct | quote }}
  # Overview analyzer thresholds
  OVERVIEW_LARGE_HEAP_HIGH_MB: {{ .Values.config.overview.largeHeapHighMb | quote }}
  OVERVIEW_LARGE_HEAP_MEDIUM_MB: {{ .Values.config.overview.largeHeapMediumMb | quote }}
  OVERVIEW_HIGH_OBJECT_COUNT: {{ .Values.config.overview.highObjectCount | quote }}
  OVERVIEW_ELEVATED_OBJECT_COUNT: {{ .Values.config.overview.elevatedObjectCount | quote }}
  OVERVIEW_HIGH_CLASSLOADER_COUNT: {{ .Values.config.overview.highClassloaderCount | quote }}
  OVERVIEW_ELEVATED_CLASSLOADER_COUNT: {{ .Values.config.overview.elevatedClassloaderCount | quote }}
  OVERVIEW_HIGH_GC_ROOT_COUNT: {{ .Values.config.overview.highGcRootCount | quote }}
  OVERVIEW_THREAD_LEAK_MB: {{ .Values.config.overview.threadLeakMb | quote }}
  OVERVIEW_THREAD_LEAK_SEVERE_MB: {{ .Values.config.overview.threadLeakSevereMb | quote }}
  OVERVIEW_LARGE_ARRAY_MB: {{ .Values.config.overview.largeArrayMb | quote }}
  OVERVIEW_LARGE_STRING_MB: {{ .Values.config.overview.largeStringMb | quote }}
  OVERVIEW_LARGE_CACHE_MB: {{ .Values.config.overview.largeCacheMb | quote }}
  # Top Components analyzer thresholds
  TOP_COMPONENTS_DOMINANT_CLASSLOADER_MB: {{ .Values.config.topComponents.dominantClassloaderMb | quote }}
  TOP_COMPONENTS_DOMINANT_CLASSLOADER_HIGH_PCT: {{ .Values.config.topComponents.dominantClassloaderHighPct | quote }}
  TOP_COMPONENTS_DOMINANT_CONSUMER_MB: {{ .Values.config.topComponents.dominantConsumerMb | quote }}
  TOP_COMPONENTS_DOMINANT_CONSUMER_PCT: {{ .Values.config.topComponents.dominantConsumerPct | quote }}
  TOP_COMPONENTS_LARGE_CONSUMER_MB: {{ .Values.config.topComponents.largeConsumerMb | quote }}
  TOP_COMPONENTS_WASTE_PROBLEM_MB: {{ .Values.config.topComponents.wasteProblemMb | quote }}
  TOP_COMPONENTS_WASTE_WARNING_MB: {{ .Values.config.topComponents.wasteWarningMb | quote }}
```

**Step 2: Commit**

```bash
git add helm/eclipse-mat-service/templates/configmap.yaml
git commit -m "feat(helm): add ConfigMap with all env vars"
```

---

### Task 5: ServiceAccount — `serviceaccount.yaml`

**Files:**
- Create: `helm/eclipse-mat-service/templates/serviceaccount.yaml`

**Step 1: Write serviceaccount.yaml**

```gotemplate
{{- if .Values.serviceAccount.create -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "eclipse-mat-service.serviceAccountName" . }}
  labels:
    {{- include "eclipse-mat-service.labels" . | nindent 4 }}
  {{- with .Values.serviceAccount.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- if .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml .Values.imagePullSecrets | nindent 2 }}
{{- end }}
{{- end }}
```

**Step 2: Commit**

```bash
git add helm/eclipse-mat-service/templates/serviceaccount.yaml
git commit -m "feat(helm): add ServiceAccount template"
```

---

### Task 6: PersistentVolumeClaims

**Files:**
- Create: `helm/eclipse-mat-service/templates/pvc-heapdumps.yaml`
- Create: `helm/eclipse-mat-service/templates/pvc-reports.yaml`

**Step 1: Write pvc-heapdumps.yaml**

```gotemplate
{{- if .Values.persistence.heapdumps.enabled -}}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "eclipse-mat-service.fullname" . }}-heapdumps
  labels:
    {{- include "eclipse-mat-service.labels" . | nindent 4 }}
spec:
  accessModes:
    - {{ .Values.persistence.heapdumps.accessMode }}
  resources:
    requests:
      storage: {{ .Values.persistence.heapdumps.size }}
  {{- if .Values.persistence.heapdumps.storageClassName }}
  storageClassName: {{ .Values.persistence.heapdumps.storageClassName | quote }}
  {{- end }}
{{- end }}
```

**Step 2: Write pvc-reports.yaml**

```gotemplate
{{- if .Values.persistence.reports.enabled -}}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "eclipse-mat-service.fullname" . }}-reports
  labels:
    {{- include "eclipse-mat-service.labels" . | nindent 4 }}
spec:
  accessModes:
    - {{ .Values.persistence.reports.accessMode }}
  resources:
    requests:
      storage: {{ .Values.persistence.reports.size }}
  {{- if .Values.persistence.reports.storageClassName }}
  storageClassName: {{ .Values.persistence.reports.storageClassName | quote }}
  {{- end }}
{{- end }}
```

**Step 3: Commit**

```bash
git add helm/eclipse-mat-service/templates/pvc-heapdumps.yaml \
        helm/eclipse-mat-service/templates/pvc-reports.yaml
git commit -m "feat(helm): add PVC templates for heapdumps and reports"
```

---

### Task 7: Service — `service.yaml`

**Files:**
- Create: `helm/eclipse-mat-service/templates/service.yaml`

**Step 1: Write service.yaml**

```gotemplate
apiVersion: v1
kind: Service
metadata:
  name: {{ include "eclipse-mat-service.fullname" . }}
  labels:
    {{- include "eclipse-mat-service.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  ports:
    - port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
      name: http
  selector:
    {{- include "eclipse-mat-service.selectorLabels" . | nindent 4 }}
```

**Step 2: Commit**

```bash
git add helm/eclipse-mat-service/templates/service.yaml
git commit -m "feat(helm): add Service template"
```

---

### Task 8: OpenShift Route — `route.yaml`

**Files:**
- Create: `helm/eclipse-mat-service/templates/route.yaml`

**Step 1: Write route.yaml**

```gotemplate
{{- if .Values.route.enabled -}}
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: {{ include "eclipse-mat-service.fullname" . }}
  labels:
    {{- include "eclipse-mat-service.labels" . | nindent 4 }}
  {{- with .Values.route.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  {{- if .Values.route.host }}
  host: {{ .Values.route.host | quote }}
  {{- end }}
  to:
    kind: Service
    name: {{ include "eclipse-mat-service.fullname" . }}
    weight: 100
  port:
    targetPort: http
  tls:
    termination: {{ .Values.route.tls.termination }}
    insecureEdgeTerminationPolicy: {{ .Values.route.tls.insecureEdgeTerminationPolicy }}
  wildcardPolicy: None
{{- end }}
```

**Step 2: Commit**

```bash
git add helm/eclipse-mat-service/templates/route.yaml
git commit -m "feat(helm): add OpenShift Route template"
```

---

### Task 9: Deployment — `deployment.yaml`

**Files:**
- Create: `helm/eclipse-mat-service/templates/deployment.yaml`

**Step 1: Write deployment.yaml**

This is the largest template. References ConfigMap, ServiceAccount, PVCs, probes, security context.

```gotemplate
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "eclipse-mat-service.fullname" . }}
  labels:
    {{- include "eclipse-mat-service.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      {{- include "eclipse-mat-service.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
      {{- with .Values.podAnnotations }}
        {{- toYaml . | nindent 8 }}
      {{- end }}
      labels:
        {{- include "eclipse-mat-service.labels" . | nindent 8 }}
        {{- with .Values.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      serviceAccountName: {{ include "eclipse-mat-service.serviceAccountName" . }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
          ports:
            - name: http
              containerPort: 8080
              protocol: TCP
          envFrom:
            - configMapRef:
                name: {{ include "eclipse-mat-service.fullname" . }}
          livenessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 10
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          volumeMounts:
            {{- if .Values.persistence.heapdumps.enabled }}
            - name: heapdumps
              mountPath: /heapdumps
            {{- end }}
            {{- if .Values.persistence.reports.enabled }}
            - name: reports
              mountPath: /reports
            {{- end }}
      volumes:
        {{- if .Values.persistence.heapdumps.enabled }}
        - name: heapdumps
          persistentVolumeClaim:
            claimName: {{ include "eclipse-mat-service.fullname" . }}-heapdumps
        {{- end }}
        {{- if .Values.persistence.reports.enabled }}
        - name: reports
          persistentVolumeClaim:
            claimName: {{ include "eclipse-mat-service.fullname" . }}-reports
        {{- end }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
```

**Step 2: Commit**

```bash
git add helm/eclipse-mat-service/templates/deployment.yaml
git commit -m "feat(helm): add Deployment template with probes, PVCs, ConfigMap"
```

---

### Task 10: Lint and validate the chart

**Step 1: Run helm lint**

```bash
helm lint helm/eclipse-mat-service/
```

Expected: `1 chart(s) linted, 0 chart(s) failed`

**Step 2: Run helm template (dry-run render)**

```bash
helm template test-release helm/eclipse-mat-service/
```

Expected: All 7 resources render without errors (Deployment, Service, Route, 2x PVC, ConfigMap, ServiceAccount).

**Step 3: Verify rendered output**

Spot-check the rendered YAML:
- Deployment has correct `image`, `envFrom`, `volumeMounts`, `securityContext`, probes
- ConfigMap has all 30+ env vars
- Route has `tls.termination: edge`
- PVCs have correct sizes (50Gi, 10Gi)

**Step 4: Commit if any lint fixes were needed**

```bash
git add -A helm/
git commit -m "fix(helm): lint fixes"
```

---

### Task 11: Update .gitignore and documentation

**Files:**
- Modify: `.gitignore` (no Helm-specific additions needed unless chart packages are generated)
- Modify: `README.md` — add Helm deployment section
- Modify: `CLAUDE.md` — add Helm chart to architecture

**Step 1: Add Helm deployment section to README.md**

Add after the existing "Run the Service" section:

```markdown
### Deploy on OpenShift (Helm)

```bash
# Install
helm install mat-service helm/eclipse-mat-service/ \
  --set image.repository=your-registry.io/eclipse-mat \
  --set image.tag=latest

# Custom values
helm install mat-service helm/eclipse-mat-service/ -f my-values.yaml

# Upgrade
helm upgrade mat-service helm/eclipse-mat-service/

# Uninstall
helm uninstall mat-service
```

**Step 2: Update CLAUDE.md architecture section**

Add the `helm/` directory to the project structure tree.

**Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: add Helm chart deployment instructions"
```

---

### Task 12: Final verification and squash commit

**Step 1: Run helm lint one more time**

```bash
helm lint helm/eclipse-mat-service/
```

**Step 2: Run helm template and count resources**

```bash
helm template test helm/eclipse-mat-service/ | grep "^kind:" | sort | uniq -c
```

Expected output (7 resources):
```
1 kind: ConfigMap
1 kind: Deployment
1 kind: PersistentVolumeClaim   (heapdumps)
1 kind: PersistentVolumeClaim   (reports)
1 kind: Route
1 kind: Service
1 kind: ServiceAccount
```

**Step 3: Test with persistence disabled**

```bash
helm template test helm/eclipse-mat-service/ \
  --set persistence.heapdumps.enabled=false \
  --set persistence.reports.enabled=false \
  | grep "kind:" | sort | uniq -c
```

Expected: 5 resources (no PVCs, no volumeMounts in Deployment).

**Step 4: Test with route disabled**

```bash
helm template test helm/eclipse-mat-service/ \
  --set route.enabled=false \
  | grep "kind:" | sort | uniq -c
```

Expected: 6 resources (no Route).

**Step 5: Run backend tests to confirm nothing broke**

```bash
cd backend && python -m pytest tests/ -v
```

Expected: All 30 tests pass (Helm chart is additive, no backend changes).
