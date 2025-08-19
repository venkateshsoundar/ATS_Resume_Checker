
# ATS Resume Optimizer (Streamlit) — with OpenRouter AI

This app:
- Upload a **DOCX** resume
- Paste a **Job Description**
- Extracts JD keywords, computes **baseline ATS score**
- Rewrites **PROFILE** to a compact 5–6 lines (optionally via **OpenRouter AI**)
- Trims content to target **≤ 2 pages** (caps bullets, trims projects & awards, merges PD & Interests)
- Outputs **optimized DOCX** and an **ATS report**

## Run Locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Cloud
1. Create a repo with:
   - `app.py`
   - `requirements.txt`
   - `README.md`
2. Deploy via Streamlit Cloud.
3. In **App → Settings → Secrets**, add:
```
OPENROUTER_API_KEY="your_key_here"
```

## Notes
- Formatting is preserved because edits are **in-place** with `python-docx` (no restyling applied).
- Page length is approximated by content trimming. Adjust caps in **Advanced Options** if needed.
- ATS score = keyword presence (with fuzzy matching). It's an approximation, not a specific vendor’s ATS.
