# Syncing from upstream

When you update the upstream tastenote project, sync changes to this repo before pushing.

## Files to copy

| From (upstream) | To (this repo) |
|----------------|----------------|
| index.html     | index.html    |
| mcp_config.py  | mcp_config.py |
| requirements.txt | requirements.txt |

## server.py — manual merge

`server.py` has deploy-specific differences. When merging from upstream `server.py`:

1. **ROOT_DIR / BASE_DIR**: Keep `BASE_DIR = Path(__file__).resolve().parent` (no `.parent.parent`).
2. **_env_path**: `BASE_DIR / ".env"` (not `ROOT_DIR / ".env"`).
3. **get_client()**: Must accept `AI_BUILDER_TOKEN` as fallback:
   ```python
   api_key = os.getenv("SUPER_MIND_API_KEY") or os.getenv("AI_BUILDER_TOKEN")
   ```
4. **uvicorn at bottom**: Must use `PORT` and `host="0.0.0.0"`:
   ```python
   port = int(os.getenv("PORT", "8000"))
   uvicorn.run(app, host="0.0.0.0", port=port)
   ```

## After syncing

```bash
git add .
git commit -m "Sync from upstream"
git push
```

Then trigger redeploy via the Deployment Portal or `POST /v1/deployments` if needed.
