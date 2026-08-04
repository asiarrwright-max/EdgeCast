# EdgeCast — GitHub Collaboration Workflow

## Branch strategy

| Branch | Purpose | Who pushes |
|--------|---------|-----------|
| `main` | Stable, deployable code | Merged PRs only — never pushed to directly |
| `replit-work` | Active Replit development | Agent pushes here after every completed task |
| `<feature>` | External contributor branches | Opened as PRs against `main` for review |

**The Replit workspace always operates on `replit-work`, not `main`.**  
No development commits go directly to `main`.

---

## Replit agent — commit & push checklist

Before every commit on `replit-work`:

1. **Run the full test suite** — no push if any test fails.
   ```bash
   cd artifacts/api-server && python -m pytest tests/ -q
   ```
2. **Confirm no forbidden files are staged:**
   - `.env` / `.env.*` / `*.env`
   - Any file containing an API key, database URL, Kalshi credential, private key, or deployment secret
   - The `.local/` directory (already excluded by `.gitignore`)
3. **Commit with a descriptive message** describing what changed and why.
4. **Push `replit-work` to GitHub.**

---

## Merging `replit-work` → `main`

Only the repository owner merges `replit-work` into `main` via a GitHub Pull Request.  
The agent does **not** merge branches automatically.

---

## Reviewing outside Pull Requests

When a PR arrives from ChatGPT, another developer, or any external source:

1. **Do not auto-merge.** Fetch the branch and inspect it locally first.
2. **Run the full test suite** against the PR branch.
3. **Report all of the following before any merge decision:**

   | Category | What to look for |
   |----------|-----------------|
   | 🗄️ Database migrations | New `ALTER TABLE`, `CREATE TABLE`, new columns, index changes, model changes |
   | 🔮 Prediction changes | Any edit to `probability_engine_*.py`, `v3_*`, `eligibility.py`, sigma/bias logic |
   | 📈 Trading changes | Any edit to `paper_trading_*.py`, trade decision logic, eligibility guards |
   | 💰 Settlement changes | Any edit to `settlement.py` or outcome/P&L calculation |
   | 🚀 Deployment risks | Changes to `artifact.toml`, `.replit`, `requirements.txt`, workflow commands, environment variable handling |
   | 🔒 Secret exposure | Any new credential, token, key, or connection string appearing in tracked files |

4. **Never approve a PR that enables real-money order submission.**  
   EdgeCast is permanently paper-trading only. Any code path that calls a Kalshi order placement endpoint (anything other than read-only market data) must be blocked.

---

## What must never be committed

```
.env
.env.*
*.env
.env.local
secrets.json / secrets.yaml / secrets.yml
*.pem / *.key / *.pfx / *.p12 / *.cer
id_rsa / id_ecdsa / id_ed25519
Any file containing KALSHI_API_KEY, DATABASE_URL, SECRET_KEY, SESSION_SECRET, ADMIN_PASSWORD
```

All of these are excluded in `.gitignore`. Verify with `git ls-files | grep -i env` before pushing.

---

## EdgeCast is paper-trading only

Real-money order submission is **permanently disabled**.  
The system only reads Kalshi market data and records simulated trades in the local database.

Any PR or code change that:
- Adds a call to a Kalshi order-placement endpoint
- Introduces `place_order`, `submit_order`, `create_order`, or equivalent
- Removes the `is_executable` / `eligibility_status` guards without replacement

…must be **rejected immediately** and flagged to the repository owner.

---

## Quick-reference commands

```bash
# Always work on replit-work
git checkout replit-work

# Run full test suite before committing
cd artifacts/api-server && python -m pytest tests/ -q

# Check nothing secret is staged
git ls-files | grep -iE "\.env|secret|credential|\.pem|\.key"

# Commit and push
git add -A
git commit -m "<descriptive message>"
# push via Replit git integration (gitPush callback)
```
