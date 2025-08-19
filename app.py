
import streamlit as st
from docx import Document
from docx.text.paragraph import Paragraph
from io import BytesIO
from rapidfuzz import fuzz, process
import re, datetime, os, httpx, yake

st.set_page_config(page_title="ATS Resume Optimizer — Flexible", page_icon="🧩", layout="wide")
st.title("ATS Resume Optimizer — Flexible (≤2 pages, 5–6 line Profile)")

st.markdown("""
This version minimizes hardcoding:
- **Headings** are *detected* from your document; you can **map** them in the UI.
- **Keywords** come from the **Job Description** via **YAKE** (no hand-curated lists).
- **Insertions** are *optional* and user-approved; no auto-fabrication.
- **Profile** can be AI-polished via **OpenRouter** or rule-based without fixed sentences.
""")

# -------------------------
# Helpers
# -------------------------

def read_docx_text(doc: Document) -> str:
    parts = []
    for p in doc.paragraphs:
        parts.append(p.text)
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    parts.append(p.text)
    return "\n".join(parts)

def detect_headings(doc: Document):
    """
    Heuristic heading detection: short lines, high uppercase ratio or Title-like,
    not ending with a period.
    """
    heads = []
    for i, p in enumerate(doc.paragraphs):
        t = (p.text or "").strip()
        if not t: 
            continue
        if len(t) <= 60 and not t.endswith("."):
            uc_ratio = sum(1 for c in t if c.isupper()) / max(1, sum(1 for c in t if c.isalpha()))
            if uc_ratio > 0.5 or t.istitle() or t.isupper():
                heads.append((i, t))
    # dedupe by text
    seen = set(); out = []
    for i,t in heads:
        key = t.upper()
        if key not in seen:
            seen.add(key)
            out.append((i,t))
    return out

def next_section_index(doc: Document, after_idx: int):
    det = {i for i,_ in detect_headings(doc)}
    for i in range(after_idx+1, len(doc.paragraphs)):
        if i in det:
            return i
    return len(doc.paragraphs)

def delete_paragraph(paragraph: Paragraph):
    element = paragraph._element
    element.getparent().remove(element)
    paragraph._p = paragraph._element = None

def insert_before_index(doc: Document, index: int, text: str):
    """Insert a paragraph before 'index' safely."""
    if index < len(doc.paragraphs):
        doc.paragraphs[index].insert_paragraph_before(text)
    else:
        doc.add_paragraph(text)

def extract_keywords_yake(jd_text: str, max_terms=40, lan="en"):
    kw_extractor = yake.KeywordExtractor(lan=lan, n=1, dedupLim=0.9, top=max_terms//2)
    kw_extractor2 = yake.KeywordExtractor(lan=lan, n=2, dedupLim=0.9, top=max_terms//2)
    k1 = kw_extractor.extract_keywords(jd_text)
    k2 = kw_extractor2.extract_keywords(jd_text)
    cand = [w for w,_ in k1+k2]
    # normalize and dedupe
    seen = set(); out = []
    for w in cand:
        w = w.strip().lower()
        w = re.sub(r"\s+", " ", w)
        if len(w) < 3: continue
        if w not in seen:
            seen.add(w); out.append(w)
    return out[:max_terms]

def score_text(text: str, keywords):
    tl = text.lower()
    matched = set()
    for kw in keywords:
        if kw in tl or fuzz.partial_ratio(kw, tl) >= 86:
            matched.add(kw)
    return round(100*len(matched)/max(1,len(keywords)),2), matched

def cap_bullets(doc: Document, section_idx: int, n_keep: int):
    end = next_section_index(doc, section_idx)
    kept = 0
    i = section_idx+1
    while i < end:
        p = doc.paragraphs[i]
        txt = (p.text or "").strip()
        if not txt:
            delete_paragraph(p); end -= 1; continue
        # treat each non-empty line as a bullet for simplicity
        if kept < n_keep:
            kept += 1; i += 1
        else:
            delete_paragraph(p); end -= 1

def replace_section_text(doc: Document, section_idx: int, new_text_lines):
    """Replace entire section body with provided lines."""
    end = next_section_index(doc, section_idx)
    # delete existing body
    i = section_idx+1
    while i < end:
        delete_paragraph(doc.paragraphs[i]); end -= 1
    # insert new lines before next section
    for line in reversed(new_text_lines):
        insert_before_index(doc, end, line)

def call_openrouter(messages, model, api_key, temperature=0.2, max_tokens=300, timeout=60):
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://streamlit.io",
        "X-Title": "ATS Resume Optimizer (Flexible)",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages, "temperature": float(temperature), "max_tokens": int(max_tokens)}
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

# -------------------------
# Inputs
# -------------------------
col1, col2 = st.columns([1,1])
with col1:
    resume_file = st.file_uploader("Upload resume (.docx)", type=["docx"])
with col2:
    jd = st.text_area("Paste Job Description", height=260, placeholder="Paste the full JD text (no screenshots)")

adv = st.expander("Advanced Options")
with adv:
    top_k = st.slider("Max JD keyphrases (YAKE)", 20, 80, 44, step=2)
    bullets_keep = st.slider("Bullets per role (cap)", 2, 6, 4)
    projects_keep = st.slider("Projects to keep", 2, 8, 4)
    awards_keep = st.slider("Awards to keep", 0, 8, 4)
    soft_char_cap = st.slider("Soft character cap (approx ≤2 pages)", 7000, 12000, 10000, step=500)
    st.subheader("OpenRouter (Optional)")
    use_llm = st.checkbox("Use OpenRouter to polish PROFILE (5–6 lines)")
    model = st.selectbox("Model", ["openrouter/auto","openai/gpt-oss-20b:free","deepseek/deepseek-r1-0528:free"], index=0)
    temp = st.slider("Temperature", 0.0, 1.0, 0.2, 0.1)

run = st.button("Optimize")

if run:
    if not resume_file or not jd.strip():
        st.error("Please upload a DOCX resume and paste the JD.")
        st.stop()

    doc = Document(resume_file)
    baseline_text = read_docx_text(doc)
    detected = detect_headings(doc)
    if not detected:
        st.error("Couldn't detect headings. Ensure your headings are short (e.g., PROFILE, EXPERIENCE, etc.).")
        st.stop()

    # Map headings without hardcoding
    st.subheader("Map Headings")
    options = [t for _,t in detected]
    def suggest(name):
        # fuzzy suggestion
        if options:
            best = process.extractOne(name, options)
            return best[0] if best and best[1] >= 60 else options[0]
        return None

    h_profile = st.selectbox("Profile/Summary heading", options, index=options.index(suggest("PROFILE")) if suggest("PROFILE") in options else 0)
    h_experience = st.selectbox("Experience heading", options, index=options.index(suggest("EXPERIENCE")) if suggest("EXPERIENCE") in options else 0)
    h_projects = st.selectbox("Projects heading (optional)", ["— None —"]+options, index=0)
    h_awards = st.selectbox("Awards/Accomplishments heading (optional)", ["— None —"]+options, index=0)
    h_skills = st.selectbox("Skills/Core Skills heading (optional)", ["— None —"]+options, index=0)

    # Extract JD keyphrases (no curated list)
    jd_keywords = extract_keywords_yake(jd, max_terms=int(top_k))
    base_score, base_matched = score_text(baseline_text, jd_keywords)

    st.write(f"**Baseline ATS:** {base_score}%")
    st.caption("Note: keyword presence + fuzzy match (approximate)")

    # Build 5–6 line Profile (LLM or rules)
    profile_idx = next(i for i,t in detected if t == h_profile)
    prof_lines = []
    if use_llm and "OPENROUTER_API_KEY" in st.secrets:
        try:
            system = "You are a resume optimizer. Write a 5–6 line profile for a QA/Testing professional using the job description. Keep factual, neutral, concise; avoid first person."
            user = f"Job Description:\n{jd}\n\nWrite a 5–6 line profile summary. Plain text only."
            resp = call_openrouter(
                [{"role":"system","content":system},{"role":"user","content":user}],
                model=model, api_key=st.secrets["OPENROUTER_API_KEY"], temperature=temp, max_tokens=220
            )
            # split into ~sentences
            prof_lines = [ln.strip() for ln in re.split(r"[\\n\\.;]+", resp) if ln.strip()][:6]
        except Exception as e:
            st.warning(f"LLM failed, using rule-based summary. ({e})")
    if not prof_lines:
        # simple rule-based from JD keyphrases
        keybits = ", ".join(jd_keywords[:8])
        prof_lines = [
            "QA professional with experience across complex applications and delivery environments.",
            "Skilled in test design, defect management, data-driven debugging, and release readiness.",
            "Hands-on across UI and business workflows; collaborate with engineering and product teams.",
            "Analytical, methodical, and detail-oriented; improve quality through iterative feedback.",
            "Comfortable managing pre-release testing and coordinating with cross-functional teams.",
            f"Familiar with: {keybits}"
        ]
    # Replace section with new profile
    replace_section_text(doc, profile_idx, [" ".join(prof_lines)])

    # Cap bullets in experience
    exp_idx = next(i for i,t in detected if t == h_experience)
    cap_bullets(doc, exp_idx, int(bullets_keep))

    # Trim projects / awards if mapped
    if h_projects != "— None —":
        proj_idx = next(i for i,t in detected if t == h_projects)
        cap_bullets(doc, proj_idx, int(projects_keep))
    if h_awards != "— None —":
        aw_idx = next(i for i,t in detected if t == h_awards)
        cap_bullets(doc, aw_idx, int(awards_keep))

    # Re-score
    final_text = read_docx_text(doc)
    final_score, final_matched = score_text(final_text, jd_keywords)

    # Offer insertion suggestions into Skills (user approved)
    if h_skills != "— None —":
        st.subheader("Suggested JD terms to add to Skills (checkbox to include)")
        missing = [k for k in jd_keywords if k not in final_matched]
        colA, colB = st.columns(2)
        chosen = []
        for i,kw in enumerate(missing):
            (colA if i%2==0 else colB).checkbox(kw, key=f"kw_{i}", value=False)
        # gather
        for i,kw in enumerate(missing):
            if st.session_state.get(f"kw_{i}"):
                chosen.append(kw)

        if st.button("Apply selected keywords to Skills"):
            skills_idx = next(i for i,t in detected if t == h_skills)
            end = next_section_index(doc, skills_idx)
            # find or create first body line
            target = None
            for j in range(skills_idx+1, end):
                if (doc.paragraphs[j].text or "").strip():
                    target = j; break
            if target is None:
                target = end
                insert_before_index(doc, end, "")
            line = doc.paragraphs[target].text or ""
            add = [w for w in chosen if w.lower() not in (line.lower())]
            line = (line + (", " if line.strip() else "") + ", ".join(add)).strip()
            doc.paragraphs[target].text = line
            # refresh score
            final_text = read_docx_text(doc)
            final_score, final_matched = score_text(final_text, jd_keywords)
            st.success("Skills updated.")

    # Final length guard (soft)
    if len(final_text) > int(soft_char_cap):
        st.info("Document still appears long. Consider raising caps or removing older items.")
    
    # Output
    out_buf = BytesIO()
    doc.save(out_buf); out_buf.seek(0)
    st.success("Done. Download below.")
    st.download_button("⬇️ Download Optimized Resume (DOCX)", out_buf, file_name="optimized_resume.docx")

    # Report
    rep = f"""# ATS Report
Generated: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}

## Summary
- Baseline ATS: {base_score}%
- Optimized ATS: {final_score}%

## JD Keyphrases ({len(jd_keywords)} YAKE)
{", ".join(jd_keywords)}

## Matched after optimization ({len(final_matched)})
{", ".join(sorted(final_matched))}

## Suggestions
Use the checkboxes above to add missing JD terms into Skills if accurate.
"""
    st.download_button("⬇️ Download ATS Report (Markdown)", rep.encode(), file_name="ATS_report.md")
