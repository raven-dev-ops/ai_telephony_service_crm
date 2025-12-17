GCP / GKE Deployment Guide
==========================

This guide describes how to go from a working local prototype to a production-style deployment on
Google Cloud Platform (GCP) using Google Kubernetes Engine (GKE) and Docker. It assumes you are
deploying the existing backend service (FastAPI + Uvicorn) behind a Kubernetes `Service` for
scaling and load balancing.

Prerequisites
-------------

- A GCP project with billing enabled.
- `gcloud`, `kubectl`, and `docker` installed and authenticated:
  - `gcloud auth login`
  - `gcloud auth application-default login`
  - `gcloud config set project YOUR_PROJECT_ID`
- A GitHub repository for this codebase (see below).
- A domain name you can point at a GKE ingress or LoadBalancer.
- Twilio account and phone number (for voice + SMS).
- Google Calendar and a service account for the Calendar API.


1. Ensure the Project Is Green Locally
--------------------------------------

From the repo root:

- Run the backend test suite:

  ```bash
  cd backend
  pytest
  ```

- Optional but recommended:

  ```bash
  ruff check .
  black --check .
  ```

All backend tests should pass before shipping to GCP (see `DEV_WORKFLOW.md` for more detail). The
current test suite passes as of this guide.


2. Prepare for GitHub Push
--------------------------

This repo already has:

- A `backend` package with tests (`backend/tests`).
- A `backend/Dockerfile` suitable for production.
- A basic CI workflow at `.github/workflows/backend-ci.yml`.

Before pushing to GitHub:

- Ensure you are not committing local virtualenvs or dev-only artefacts:
  - `.gitignore` is configured to exclude `.venv/`, `__pycache__/`, `.pytest_cache/`, and
    `backend/app.db`.
  - If any of these are already tracked, remove them from git history or delete them before the
    first push.

To push to GitHub (example):

```bash
git add .
git commit -m "Initial AI Telephony backend & dashboards"
git branch -M main
git remote add origin git@github.com:YOUR_ORG/ai_telephony_service_crm.git
git push -u origin main
```

Adjust the remote URL for HTTPS if you prefer.


3. Build and Publish the Docker Image (Artifact Registry)
---------------------------------------------------------

1. Enable Artifact Registry in your project:

   ```bash
   gcloud services enable artifactregistry.googleapis.com
   ```

2. Create a Docker repository (example: `ai-telephony` in region `us-central1`):

   ```bash
   gcloud artifacts repositories create ai-telephony \
     --repository-format=docker \
     --location=us-central1 \
     --description="AI Telephony Docker images"
   ```

3. Configure Docker to use gcloud as a credential helper:

   ```bash
   gcloud auth configure-docker us-central1-docker.pkg.dev
   ```

4. Build and tag the backend image from the repo root:

   ```bash
   docker build \
     -t us-central1-docker.pkg.dev/YOUR_PROJECT_ID/ai-telephony/ai-telephony-backend:v1 \
     -f backend/Dockerfile backend
   ```

5. Push the image:

   ```bash
   docker push us-central1-docker.pkg.dev/YOUR_PROJECT_ID/ai-telephony/ai-telephony-backend:v1
   ```

You will reference this image tag in the Kubernetes `Deployment`. In CI, the release workflow
(`.github/workflows/backend-release.yml`) builds and pushes images tagged with the Git tag
name (for example, `v1.0.0`) as well as `latest`. In that case, set the `image:` field in
`k8s/backend.yaml` to the CI-managed tag (for example,
`us-central1-docker.pkg.dev/YOUR_PROJECT_ID/ai-telephony/ai-telephony-backend:v1.0.0`) so your
GKE deployment always pulls the image built for that release.


4. Create or Use a GKE Cluster
------------------------------

You can use a standard (node-based) cluster or an Autopilot cluster. Example with Autopilot:

```bash
gcloud container clusters create-auto ai-telephony-cluster \
  --region=us-central1
```

Fetch cluster credentials for `kubectl`:

```bash
gcloud container clusters get-credentials ai-telephony-cluster \
  --region=us-central1 \
  --project=YOUR_PROJECT_ID
```

Verify:

```bash
kubectl get nodes
```


5. Configure Secrets and Config for the Backend
-----------------------------------------------

This repo includes a Kubernetes manifest at `k8s/backend.yaml` with:

- `ConfigMap ai-telephony-backend-config` (non-secret settings).
- `Secret ai-telephony-backend-secrets` (sensitive settings + Calendar JSON).
- `Deployment ai-telephony-backend` (backend pods).
- `Service ai-telephony-backend` (type `LoadBalancer`).
- `HorizontalPodAutoscaler ai-telephony-backend-hpa` (CPU-based autoscaling).

Before applying the manifest, edit `k8s/backend.yaml`:

1. **Update the image** in the `Deployment` to match your Artifact Registry path:

   ```yaml
   image: us-central1-docker.pkg.dev/YOUR_PROJECT_ID/ai-telephony/ai-telephony-backend:v1
   ```

2. **ConfigMap (`ai-telephony-backend-config`)**:

   - `GOOGLE_CALENDAR_ID`: leave as `primary` or set to a tenant-specific calendar.
   - `CALENDAR_USE_STUB`: set to `"false"` once you are ready to use the real Calendar API.
   - `REQUIRE_BUSINESS_API_KEY`: keep `"true"` for production multi-tenant safety.
   - `SMS_PROVIDER`: `"twilio"` for real Twilio SMS, `"stub"` for dry runs.
   - `VERIFY_TWILIO_SIGNATURES`: `"true"` for production.
   - `GOOGLE_CALENDAR_CREDENTIALS_FILE`: typically `/secrets/google-calendar.json`.

3. **Secret (`ai-telephony-backend-secrets`)**:

   Replace all `REPLACE_ME` values with real secrets:

   - `DATABASE_URL`: a production database (for example, Cloud SQL for Postgres).
   - `ADMIN_API_KEY`: protects `/v1/admin/*`.
   - `OWNER_DASHBOARD_TOKEN`: protects owner/CRM dashboards.
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `SMS_FROM_NUMBER`, `SMS_OWNER_NUMBER`.
   - `OPENAI_API_KEY` if you enable non-stub speech (`SPEECH_PROVIDER=openai`).
   - `google-calendar.json`: paste the Google service account JSON for Calendar.

   For production, you may prefer managing this `Secret` via `kubectl create secret` or a
   GitHub Actions workflow instead of committing real values to git. Treat the checked-in
   `k8s/backend.yaml` as a template only.


6. Deploy the Backend to GKE
----------------------------

Apply the manifest:

```bash
kubectl apply -f k8s/backend.yaml
```

Check rollout and pods:

```bash
kubectl get deployments
kubectl get pods
```

Once the `Service` is ready, it will expose an external IP:

```bash
kubectl get svc ai-telephony-backend
```

Use the external IP to test the backend:

```bash
curl http://EXTERNAL_IP/healthz
curl http://EXTERNAL_IP/docs
```


7. DNS, TLS, and Twilio Webhooks
--------------------------------

1. **DNS**:
   - Create an `A` record (for example, `api.example.com`) pointing to the external IP of
     `ai-telephony-backend`.

2. **TLS**:
   - For an initial deployment, you can terminate TLS at a Cloud Load Balancer in front of the
     GKE Service or use an Ingress with cert-manager. This guide assumes HTTP while you are wiring
     up basics; see `RUNBOOK.md` for operational expectations before going to production.

3. **Twilio** (see also `DEPLOYMENT_CHECKLIST.md` and `RUNBOOK.md`):
   - In the Twilio Console, configure:
     - Voice webhook: `POST https://api.example.com/twilio/voice`
     - Messaging webhook: `POST https://api.example.com/twilio/sms`
   - Optional: add `?business_id=<tenant_id>` for per-tenant routing.


8. Owner & Admin Dashboards
---------------------------

With the backend reachable at `https://api.example.com`:

- Serve `dashboard/index.html` and `dashboard/admin.html` behind the same origin (for example,
  via a static hosting bucket or a small frontend service).
- Configure:
  - `X-API-Key` (tenant `api_key`) and `X-Owner-Token` (env `OWNER_DASHBOARD_TOKEN`) for the owner
    dashboard.
  - `X-Admin-API-Key` (env `ADMIN_API_KEY`) for the admin dashboard.

For full details on dashboard flows and API usage, see `DASHBOARD.md`, `API_REFERENCE.md`, and
`DATA_MODEL.md`.


9. Observability and Scaling
----------------------------

- **Health and metrics**:
  - The backend exposes `/healthz` and `/metrics` as JSON.
  - Prometheus-style metrics are available at `/metrics/prometheus`.
  - You can temporarily port-forward for local inspection:

    ```bash
    kubectl port-forward svc/ai-telephony-backend 8000:80
    curl http://localhost:8000/metrics
    ```

- **Autoscaling**:
  - The `HorizontalPodAutoscaler` in `k8s/backend.yaml` scales between 2 and 10 pods based on CPU
    utilization.
  - Verify:

    ```bash
    kubectl get hpa ai-telephony-backend-hpa
    ```

See `RUNBOOK.md` and `PILOT_RUNBOOK.md` for operational runbooks, incident handling, and safe
deployment practices. Use `DEPLOYMENT_CHECKLIST.md` alongside this guide to ensure environment,
tenant, and Twilio/Calendar wiring are complete before onboarding a real business.
