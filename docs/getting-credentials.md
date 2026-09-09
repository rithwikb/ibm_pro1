# How to Get Your API Key and Project ID

This doc covers the credentials and endpoint values used by the pipeline.

For Bob API access, use `BOB_API_TOKEN` and `BOB_API_URL` when your Bob service
provides them. The token is sent directly as a bearer token; it is not exchanged
through IBM Cloud IAM.

The older `BOB_API_KEY` plus `WATSONX_PROJECT_ID` settings remain supported for
legacy watsonx deployments.

---

## Step 1 — Get your `BOB_API_KEY` (IBM Cloud API Key)

For a Bob-native setup, skip this step and configure:
```env
BOB_API_TOKEN=<your Bob token>
BOB_API_URL=<your complete Bob chat endpoint>
```
Use the exact endpoint supplied by your Bob service documentation. Do not guess
the URL or commit the token.

1. Go to **[https://cloud.ibm.com](https://cloud.ibm.com)** and sign in with your IBM ID.
2. Click your **name/avatar** in the top-right → **"Manage"** → **"Access (IAM)"**.
3. In the left sidebar click **"API keys"**.
4. Click **"Create an IBM Cloud API key"**.
5. Give it a name (e.g. `ai-dev-team-local`) and click **"Create"**.
6. **Copy the key immediately** — IBM only shows it once. If you miss it, delete and recreate.
7. Paste it into your `.env` file:
   ```
   BOB_API_KEY=<the key you just copied>
   ```

> **Note:** The pipeline uses this key as a Bearer token in the `Authorization` header when calling watsonx.ai. It is the same IBM Cloud API key used everywhere in the IBM Cloud ecosystem.

---

## Step 2 — Get your `WATSONX_PROJECT_ID`

1. Go to **[https://dataplatform.cloud.ibm.com](https://dataplatform.cloud.ibm.com)** (IBM watsonx.ai console).
2. Sign in with the same IBM ID.
3. On the home page, click **"Projects"** in the left sidebar.
4. Either **create a new project** (click "+ New project" → name it `ai-dev-team`) or open an existing one.
5. Inside the project, click the **"Manage"** tab.
6. Under **"General"**, look for **"Project ID"** — it looks like `5239930f-a812-4f55-aa05-b686dc9a4f56`.
7. Copy it and paste into your `.env`:
   ```
   WATSONX_PROJECT_ID=<your project ID>
   ```

> **Note:** The project ID tells watsonx which billing account and resource group to charge the inference calls to. Without it, the API returns a 400 error even with a valid API key.

---

## Step 3 — Create your local `.env` file

```powershell
# Run this once in the repo root
Copy-Item .env.example .env
```

Then open `.env` in any text editor and replace:
- `your-bob-api-key-here` → your real IBM Cloud API key
- `your-watsonx-project-id-here` → your real watsonx project ID

The `.env` file is in `.gitignore` and will never be committed.

---

## Step 4 — Verify the setup

Run this one-liner to confirm both values are loaded and the API responds:

```powershell
cd "c:\Users\Srikar Sagala\OneDrive - iiit-b\Desktop\ai-dev-team"
python -c "
from dotenv import load_dotenv; import os; load_dotenv()
key = os.environ.get('BOB_API_KEY','')
pid = os.environ.get('WATSONX_PROJECT_ID','')
print('BOB_API_KEY set:', bool(key) and not key.startswith('your-'))
print('WATSONX_PROJECT_ID set:', bool(pid) and not pid.startswith('your-'))
"
```

Expected output:
```
BOB_API_KEY set: True
WATSONX_PROJECT_ID set: True
```

---

## Step 5 — Test the Reflection Agent end-to-end

Once `.env` is filled in, run a real Reflection Agent call with the mock manager report:

```powershell
cd "c:\Users\Srikar Sagala\OneDrive - iiit-b\Desktop\ai-dev-team"
python agents/reflection_agent/reflection.py --report agents/reflection_agent/test_data/mock_manager_report.json
```

If `BOB_API_KEY` and `WATSONX_PROJECT_ID` are set correctly, it will call the watsonx API and print a real rewrite. If either is missing or wrong, it falls back to stub mode and prints:
```
[reflection.py] STUB MODE: BOB_API_KEY not set — returning placeholder rewrite.
```

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `STUB MODE: BOB_API_KEY not set` | `.env` not loaded or key is still a placeholder | Check `.env` exists in repo root and key is real |
| `HTTP 401` from watsonx | API key is wrong or expired | Regenerate at cloud.ibm.com → IAM → API keys |
| `HTTP 400` from watsonx | Project ID is wrong or missing | Double-check the ID from the project Manage tab |
| `HTTP 403` from watsonx | Key doesn't have access to the project | In the project, go to Manage → Collaborators → add your IBM ID with Editor role |
| `HTTP 404` from watsonx | Wrong region URL | Change `WATSONX_URL` in `.env` — use `eu-de` or `eu-gb` if you're not in us-south |
