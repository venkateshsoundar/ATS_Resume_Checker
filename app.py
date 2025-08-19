
"""
ATS Optimizer — Streamlit Cloud (Preserve DOCX Styles)
Author: Venky (Venkatesh)

What’s new vs your current cloud app
- When a DOCX is uploaded, we **edit only the three sections** inside the original file
  (PROFILE / CORE SKILLS / EMPLOYMENT EXPERIENCE) and keep the **original styles/layout**.
- We ask OpenRouter to return JSON for those sections and then reinsert them into the original DOCX.

Notes
- On Streamlit Cloud, PDF export via docx2pdf is not available (requires MS Word on Windows).
- For PDFs as input, we still fall back to a clean DOCX (layout cannot be preserved).
"""

import io
import os
import re
import json
import difflib
import requests
import streamlit as st
from PyPDF2 import PdfReader

try:
    import docx  # python-docx
    from docx import Document
except ImportError:
    docx = None
    Document = None

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = st.secrets.get("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))

AVAILABLE_MODELS = [
    "deepseek/deepseek-r1-0528:free",
    "openai/gpt-oss-20b:free"
]

SECTION_NAMES = ["profile", "core skills", "employment experience"]

# ---------- file helpers ----------

def read_docx_text_and_bytes(uploaded_file):
    """Return (text, raw_bytes, Document) for a DOCX upload."""
    if Document is None:
        st.error("python-docx not installed. Add it to requirements.txt")
        return "", None, None
    raw = uploaded_file.getvalue()
    bio = io.BytesIO(raw)
    d = Document(bio)
    text = "\n".join(p.text for p in d.paragraphs)
    return text, raw, d

def read_pdf_text(file) -> str:
    reader = PdfReader(file)
    return "\n".join(page.extract_text() or "" for page in reader.pages)

def write_docx_from_text(text: str) -> bytes:
    """Fallback builder for when we don't have an original DOCX template to preserve."""
    if Document is None:
        st.error("python-docx not installed.")
        return b""
    d = Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    bio = io.BytesIO(); d.save(bio); bio.seek(0)
    return bio.read()

# ---------- docx section editing ----------

def _para_text(p):
    return (p.text or "").strip()

def _is_section_heading(p):
    return _para_text(p).lower() in SECTION_NAMES

def _find_section_ranges(document):
    """Return dict: name -> (start_idx_of_heading, end_idx_exclusive)."""
    paras = document.paragraphs
    heads = []
    for i,p in enumerate(paras):
        if _is_section_heading(p):
            heads.append((i, _para_text(p).lower()))
    ranges = {}
    for idx,(i,name) in enumerate(heads):
        j = heads[idx+1][0] if idx+1 < len(heads) else len(paras)
        ranges[name] = (i, j)
    return ranges

def _delete_paragraph(p):
    p._element.getparent().remove(p._element)
    p._p = None

def _insert_paragraph_after(paragraph, text=""):
    return paragraph.insert_paragraph_after(text)

def _apply_lines_into_section(document, section_name, lines):
    """Replace content under the given section heading, keeping the heading and styles."""
    paras = document.paragraphs
    ranges = _find_section_ranges(document)
    if section_name not in ranges:
        return
    start, end = ranges[section_name]
    heading_para = paras[start]

    # Delete old content (but keep the heading)
    for k in range(end-1, start, -1):
        _delete_paragraph(paras[k])

    # Insert new content after heading
    last = heading_para
    for line in lines:
        text = str(line).rstrip("\n")
        if not text:
            last = _insert_paragraph_after(last, "")
            continue
        # bullets heuristic
        bullet = False
        if re.match(r"^\s*[•\-\u2022]", text):
            bullet = True
            text = re.sub(r"^\s*[•\-\u2022]\s*", "", text)
        last = _insert_paragraph_after(last, text)
        try:
            if bullet and ("List Bullet" in document.styles):
                last.style = document.styles["List Bullet"]
        except Exception:
            pass

def build_docx_from_edited_sections(original_docx_bytes: bytes, edited_sections: dict) -> bytes:
    """Open the uploaded DOCX and replace only the specified sections' contents while preserving styles/layout."""
    if Document is None:
        return b""
    bio = io.BytesIO(original_docx_bytes)
    d = Document(bio)
    for key, lines in edited_sections.items():
        _apply_lines_into_section(d, key, lines)
    out = io.BytesIO(); d.save(out); out.seek(0)
    return out.read()

# ---------- LLM calls ----------

def call_openrouter_sections(model: str, base_resume: str, jd: str, role: str) -> dict:
    """Ask the model to return STRICT JSON: {profile:[], core_skills:[], employment_experience:[]}"""
    if not OPENROUTER_API_KEY:
        st.error("Missing OPENROUTER_API_KEY (set it in Streamlit Secrets).")
        return {}
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "X-Title": "ATS Optimizer (sections)"
    }
    system_prompt = (
        "Return STRICT JSON for section rewrites. Keys: 'profile', 'core_skills', 'employment_experience'. "
        "Each value must be an array of lines (strings). Do NOT include any other keys or text. "
        "Rewrite conservatively; preserve bullets as lines starting with '- ' if appropriate. No fabrication."
    )
    user_prompt = (
        f"TARGET ROLE: {role}\n\n"
        f"JOB DESCRIPTION:\n{jd}\n\n"
        f"CURRENT RESUME:\n{base_resume}\n\n"
        "TASK: Rewrite ONLY those three sections and return JSON ONLY."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.2
    }
    try:
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            st.error(f"OpenRouter error: {r.text[:200]}")
            return {}
        content = r.json().get("choices", [{}])[0].get("message", {}).get("content", "{}")
        content = re.sub(r"^```json|^```|```$", "", content.strip())
        data = {}
        try:
            data = json.loads(content)
        except Exception:
            m = re.search(r"\{[\s\S]*\}$", content)
            if m:
                data = json.loads(m.group(0))
        return {
            "profile": list(map(str, data.get("profile", []))),
            "core skills": list(map(str, data.get("core_skills", data.get("core skills", [])))),
            "employment experience": list(map(str, data.get("employment_experience", data.get("employment experience", []))))
        }
    except Exception as e:
        st.error(f"OpenRouter call failed: {e}")
        return {}

def make_unified_diff(a: str, b: str) -> str:
    return "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                          fromfile="original", tofile="optimized", lineterm=""))

# ---------- App ----------

def main():
    st.set_page_config(page_title="ATS Optimizer — Preserve DOCX", page_icon="🧠", layout="wide")
    st.title("🧠 ATS Resume Optimizer (Preserve DOCX Styles)")

    with st.sidebar:
        model = st.selectbox("Model", AVAILABLE_MODELS, index=0)
        st.caption("Set OPENROUTER_API_KEY in App → Settings → Secrets")

    uploaded_file = st.file_uploader("Upload your Resume (DOCX or PDF)", type=["docx", "pdf"])
    jd_text = st.text_area("Paste the Job Description (JD)", height=220)
    role_text = st.text_input("Target Role (e.g., Data Analyst)")

    resume_text = ""
    original_docx_bytes = None

    if uploaded_file:
        if uploaded_file.type == "application/pdf":
            # PDF input: we can't preserve layout later
            resume_text = read_pdf_text(uploaded_file)
        else:
            resume_text, original_docx_bytes, _ = read_docx_text_and_bytes(uploaded_file)

    if resume_text:
        st.subheader("Original Resume Preview")
        st.text_area("Original Resume", resume_text, height=250)

    if st.button("Optimize (Preserve DOCX Sections)") and resume_text and jd_text:
        sections = call_openrouter_sections(model, resume_text, jd_text, role_text)

        # Show editable preview (sectioned)
        st.subheader("Optimized Sections (Editable Preview)")
        preview_lines = []
        for key in ["profile", "core skills", "employment experience"]:
            if key in sections:
                preview_lines.append(key.upper())
                preview_lines.extend(sections[key])
                preview_lines.append("")
        preview_text = "\n".join(preview_lines).strip() if sections else resume_text
        edited_preview = st.text_area("Edit lines (affects DOCX export)", preview_text, height=320, key="edit_box")

        st.subheader("Unified Diff")
        st.code(make_unified_diff(resume_text, edited_preview), language="diff")

        # Downloads
        st.subheader("Download")
        st.download_button("Download Optimized (TXT)",
                           data=edited_preview.encode("utf-8"),
                           file_name="optimized_resume.txt")

        # Rebuild DOCX by replacing only those sections (if original DOCX is available)
        if original_docx_bytes and sections:
            # Parse edited preview back into dict
            rebuilt = {"profile": [], "core skills": [], "employment experience": []}
            current = None
            for ln in edited_preview.splitlines():
                low = ln.strip().lower()
                if low in rebuilt:
                    current = low
                    continue
                if current:
                    rebuilt[current].append(ln)
            out_bytes = build_docx_from_edited_sections(original_docx_bytes, rebuilt)
            st.download_button("Download Optimized (DOCX — original style)",
                               data=out_bytes,
                               file_name="optimized_resume.docx",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        else:
            # Fallback to plain writer
            docx_bytes = write_docx_from_text(edited_preview)
            st.download_button("Download Optimized (DOCX)",
                               data=docx_bytes,
                               file_name="optimized_resume.docx",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    st.markdown("---")
    if original_docx_bytes is None:
        st.caption("Upload a DOCX to preserve layout/styles. PDFs will export to a clean DOCX (not the original layout).")

if __name__ == "__main__":
    main()
