
# ATS Optimizer — Streamlit Cloud

## Quick Deploy (Streamlit Community Cloud)
1. Push these files to a public GitHub repo:
   - `app.py`
   - `requirements.txt`
   - `.streamlit/secrets.toml` (create in Cloud UI instead of pushing plaintext secrets)
2. In Streamlit Cloud, click **New app** → choose your repo/branch and set file = `app.py`.
3. In the app settings → **Secrets**, add:
   ```toml
   OPENROUTER_API_KEY = "sk-or-xxxxxxxxxxxxxxxx"
   ```
4. Deploy.

## Local Run
```bash
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-xxxxxxxxxxxxxxxx
streamlit run app.py
```

## Notes
- PDF export via `docx2pdf` is intentionally disabled on Streamlit Cloud (requires Word on Windows).
- This build reads the OpenRouter key from `st.secrets` (Cloud) or `OPENROUTER_API_KEY` env var (local).
- You can extend this with ATS scoring and tracked-changes locally, then redeploy.
