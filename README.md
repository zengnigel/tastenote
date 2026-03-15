# Tastenote

**Say what you taste.** Voice-first tasting notes: record what you smell and taste, then get a text summary, a **tasting wheel** (8 segments), and an **animated radar chart** (mouthfeel). Two recording modes: **rolling 30s buffer** or **manual record** (up to 5 min). Notes are persisted in JSON file; **Repertoire** lists all notes.

This is the **standalone deploy** version. It can be deployed to [ai-builders.space](https://ai-builders.space) as a public repo.

---

## Quick start (local)

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your SUPER_MIND_API_KEY
python server.py
```

Open http://127.0.0.1:8000

---

## Deploy to ai-builders.space

1. **Create a new GitHub repo** (public, e.g. `tastenote` or `tastenote-deploy`).
2. **Push this folder** as the repo root:

   ```bash
   cd tastenote-deploy
   git init
   git add .
   git commit -m "Initial tastenote deploy"
   git remote add origin https://github.com/zengnigel/tastenote.git
   git branch -M main
   git push -u origin main
   ```

3. **Deploy** via the platform API or Deployment Portal with:
   - **Repo URL**: `https://github.com/zengnigel/tastenote`
   - **Service name**: `tastenote` (becomes `https://tastenote.ai-builders.space`)
   - **Branch**: `main`

   `AI_BUILDER_TOKEN` is injected automatically by the platform. No extra env vars needed for basic deployment.

---

## Security & API keys

- **Never commit** `.env` or real API keys. `.env` is in `.gitignore`.
- **Deployment:** The platform injects `AI_BUILDER_TOKEN` at runtime (same key you use when calling the deploy API). Do **not** add it to `env_vars` in the deploy request.
- **Local dev:** Copy `.env.example` to `.env`. Add your platform API key as `SUPER_MIND_API_KEY`. Keep `.env` local only.
- **No secrets in repo:** Only `.env.example` with placeholder values. Ensure `.env` is listed in `.gitignore` before pushing.

---

## Updating from upstream

This deploy version is synced from an upstream tastenote project. To update: copy server.py, index.html, mcp_config.py, requirements.txt from upstream; merge deploy-specific changes in server.py (see [SYNC.md](SYNC.md)). Commit, push, then trigger redeploy if needed.

---

## Tech

- FastAPI + uvicorn
- OpenAI API (transcription + chat) via AI Builders backend
- MCP for API base URL (falls back to default when unavailable)
- Static HTML + vanilla JS (no build step)
