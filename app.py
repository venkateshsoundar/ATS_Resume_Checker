
"""
ATS Optimizer — Streamlit Cloud Ready
Author: Venky (Venkatesh)

What it does
- Upload resume (DOCX/PDF)
- Paste JD + choose target role
- Optimize with OpenRouter (model picker)
- Edit optimized draft, view unified diff
- Download TXT/DOCX

Notes for Cloud
- Set your OPENROUTER_API_KEY in Streamlit Secrets (App settings → Secrets).
- PDF export via docx2pdf is disabled on Cloud (requires MS Word). Local-only.
"""

import io
import os
import difflib
import re
import requests
import streamlit as st
from PyPDF2 import PdfReader

try:
    import docx  # python-docx
except ImportError:
    docx = None

# ---------------------------
# Config
# ---------------------------
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# read API key from st.secrets first, then env fallback
OPENROUTER_API_KEY = st.secrets.get("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))

AVAILABLE_MODELS = [
    "deepseek/deepseek-r1-0528:free",
    "openai/gpt-oss-20b:free"
]

# ---------------------------
# Utils
# ---------------------------
def read_docx_text(file) -> str:
    if docx is None:
        st.error("python-docx not installed. Add it to requirements.txt")
        return ""
    d = docx.Document(file)
    return "\n".join(p.text for p in d.paragraphs)

def read_pdf_text(file) -> str:
    reader = PdfReader(file)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return text

def write_docx_from_text(text: str) -> bytes:
    if docx is None:
        st.error("python-docx not installed. Add it to requirements.txt")
        return b""
    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    bio = io.BytesIO()
    d.save(bio); bio.seek(0)
    return bio.read()

def make_unified_diff(a: str, b: str) -> str:
    return "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                          fromfile="original", tofile="optimized", lineterm=""))

def call_openrouter_api(model: str, base_resume: str, jd: str, role: str) -> str:
    if not OPENROUTER_API_KEY:
        st.error("No OpenRouter API key found. Add OPENROUTER_API_KEY in Streamlit Secrets.")
        return base_resume
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "X-Title": "ATS Optimizer"
    }
    system_prompt = (
        "You are an ATS resume optimizer. Preserve original section structure and bullet style. "
        "Do not fabricate experience. Keep the tone concise and professional."
    )
    user_prompt = (
        f"TARGET ROLE: {role}\n\n"
        f"JOB DESCRIPTION:\n{jd}\n\n"
        f"CURRENT RESUME:\n{base_resume}\n\n"
        "TASK: Rewrite the resume conservatively to maximize alignment with the JD. "
        "Output ONLY the revised resume text."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.2
    }
    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
    if resp.status_code == 200:
        return resp.json()["choices"][0]["message"]["content"]
    else:
        st.error(f"OpenRouter API error: {resp.text[:300]}")
        return base_resume

# ---------------------------
# App
# ---------------------------
def main():
    st.set_page_config(page_title="ATS Optimizer", page_icon="🧠", layout="wide")
    st.title("🧠 ATS Resume Optimizer (Streamlit Cloud)")

    with st.sidebar:
        st.subheader("Model")
        model = st.selectbox("Choose AI model", AVAILABLE_MODELS, index=0)
        st.caption("Set your OPENROUTER_API_KEY in App → Settings → Secrets")

    uploaded_file = st.file_uploader("Upload your Resume (DOCX or PDF)", type=["docx", "pdf"])
    jd_text = st.text_area("Paste the Job Description (JD)", height=220, placeholder="Paste full JD here…")
    role_text = st.text_input("Target Role", placeholder="e.g., Data Analyst, QA Engineer")

    resume_text = ""
    if uploaded_file:
        if uploaded_file.type == "application/pdf":
            resume_text = read_pdf_text(uploaded_file)
        else:
            resume_text = read_docx_text(uploaded_file)

    if resume_text:
        st.subheader("Original Resume Preview")
        st.text_area("Original Resume", resume_text, height=250)

    if st.button("Optimize Resume with AI") and resume_text and jd_text:
        optimized_text = call_openrouter_api(model, resume_text, jd_text, role_text)
        st.subheader("Optimized Resume (Editable)")
        edited_text = st.text_area("Edit if needed", optimized_text, height=300, key="edit_box")

        st.subheader("Difference (Unified Diff)")
        st.code(make_unified_diff(resume_text, edited_text), language="diff")

        # Downloads
        txt_bytes = edited_text.encode("utf-8")
        st.download_button("Download Optimized (TXT)", data=txt_bytes, file_name="optimized_resume.txt")

        docx_bytes = write_docx_from_text(edited_text)
        if docx_bytes:
            st.download_button("Download Optimized (DOCX)", data=docx_bytes,
                               file_name="optimized_resume.docx",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    st.markdown("---")
    st.caption("This build is Streamlit Cloud friendly (no local-only PDF export). Add ATS metrics later if desired.")

if __name__ == "__main__":
    main()
