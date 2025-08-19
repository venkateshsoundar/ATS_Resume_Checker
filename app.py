
import streamlit as st
from docx import Document
from io import BytesIO
from rapidfuzz import fuzz
import re, datetime, os, httpx, yake

st.set_page_config(page_title="ATS Resume Optimizer — Two-Step", page_icon="🧰", layout="wide")
st.title("ATS Resume Optimizer — Two-Step Workflow")

st.markdown("""
**Flow**
1) Upload **DOCX resume** and **paste the Job Description**  
2) Click **Check ATS** → see **baseline ATS score + report**  
3) Click **Optimize Resume** → see **optimized ATS score + report** and **download files**
""")

# -------- Helpers --------
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
    heads = []
    for i, p in enumerate(doc.paragraphs):
        t = (p.text or "").strip()
        if not t: continue
        if len(t) <= 60 and not t.endswith("."):
            uc_ratio = sum(1 for c in t if c.isupper()) / max(1, sum(1 for c in t if c.isalpha()))
            if uc_ratio > 0.5 or t.istitle() or t.isupper():
                heads.append((i, t))
    seen = set(); out = []
    for i,t in heads:
        key = t.upper()
        if key not in seen:
            seen.add(key); out.append((i,t))
    return out

def next_section_index(doc: Document, after_idx: int):
    det = {i for i,_ in detect_headings(doc)}
    for i in range(after_idx+1, len(doc.paragraphs)):
        if i in det:
            return i
    return len(doc.paragraphs)

def delete_paragraph(paragraph):
    element = paragraph._element
    element.getparent().remove(element)
    paragraph._p = paragraph._element = None

def insert_before_index(doc: Document, index: int, text: str):
    if index < len(doc.paragraphs):
        doc.paragraphs[index].insert_paragraph_before(text)
    else:
        doc.add_paragraph(text)

def extract_keywords_yake(jd_text: str, max_terms=44, lan="en"):
    kw1 = yake.KeywordExtractor(lan=lan, n=1, dedupLim=0.9, top=max_terms//2).extract_keywords(jd_text)
    kw2 = yake.KeywordExtractor(lan=lan, n=2, dedupLim=0.9, top=max_terms//2).extract_keywords(jd_text)
    cand = [w for w,_ in kw1+kw2]
    seen = set(); out = []
    for w in cand:
        w = re.sub(r"\s+", " ", w.strip().lower())
        if len(w) >= 3 and w not in seen:
            seen.add(w); out.append(w)
    return out[:max_terms]

def score_text(text: str, keywords):
    tl = text.lower()
    matched = set()
    for kw in keywords:
        if kw in tl or fuzz.partial_ratio(kw, tl) >= 86:
            matched.add(kw)
    return round(100*len(matched)/max(1,len(keywords)),2), matched

def replace_profile(doc: Document, heading_guess: str, new_text: str):
    det = detect_headings(doc)
    # find best matching heading
    choices = [t for _,t in det]
    heading_guess_upper = heading_guess.upper()
    h_idx = None
    for i,t in det:
        if t.upper() == heading_guess_upper:
            h_idx = i; break
    if h_idx is None and choices:
        h_idx = det[0][0]  # fallback: first heading
    if h_idx is None:
        # no headings at all—prepend
        if doc.paragraphs:
            doc.paragraphs[0].insert_paragraph_before(new_text)
            doc.paragraphs[0].insert_paragraph_before("PROFILE")
        else:
            doc.add_paragraph("PROFILE"); doc.add_paragraph(new_text)
        return
    end = next_section_index(doc, h_idx)
    i = h_idx+1
    while i < end:
        delete_paragraph(doc.paragraphs[i]); end -= 1
    insert_before_index(doc, end, new_text)

def cap_bullets(doc: Document, section_heading: str, n_keep: int):
    det = detect_headings(doc)
    tgt = None
    for i,t in det:
        if t.upper() == section_heading.upper():
            tgt = i; break
    if tgt is None: return
    end = next_section_index(doc, tgt)
    kept = 0; i = tgt+1
    while i < end:
        p = doc.paragraphs[i]
        txt = (p.text or "").strip()
        if not txt:
            delete_paragraph(p); end -= 1; continue
        if kept < n_keep:
            kept += 1; i += 1
        else:
            delete_paragraph(p); end -= 1

def optimize_profile_from_jd(jd_keywords):
    # 5–6 lines rule-based from JD terms
    keybits = ", ".join(jd_keywords[:8])
    lines = [
        "QA professional experienced across desktop, web, and mobile applications.",
        "Skilled in exploratory/manual testing, defect management, and release readiness.",
        "Hands-on with SQL/logs for debugging and root-cause analysis in complex systems.",
        "Collaborate with product, design, and engineering to build the right solutions.",
        "Disciplined, analytical, and methodical; improve processes and QA outcomes.",
        f"Familiar with: {keybits}"
    ]
    return " ".join(lines)

def append_skills_keywords(doc: Document, skills_heading: str, kws_to_add):
    det = detect_headings(doc)
    tgt = None
    for i,t in det:
        if t.upper() == skills_heading.upper():
            tgt = i; break
    if tgt is None or not kws_to_add: return False
    end = next_section_index(doc, tgt)
    target = None
    for j in range(tgt+1, end):
        if (doc.paragraphs[j].text or "").strip():
            target = j; break
    if target is None:
        target = end
        insert_before_index(doc, end, "")
    line = doc.paragraphs[target].text or ""
    existing = set([x.strip().lower() for x in re.split(r"[;,/|]", line) if x.strip()])
    add = [k for k in kws_to_add if k.lower() not in existing]
    if not add: return False
    combined = (line + (", " if line.strip() else "") + ", ".join(add)).strip()
    doc.paragraphs[target].text = combined
    return True

# -------- UI Inputs --------
c1, c2 = st.columns([1,1])
with c1:
    resume_file = st.file_uploader("Upload resume (.docx)", type=["docx"])
with c2:
    jd = st.text_area("Paste Job Description", height=240, placeholder="Paste the full JD text here...")

adv = st.expander("Advanced Options")
with adv:
    top_k = st.slider("JD keyphrases (YAKE)", 20, 80, 44, step=2)
    heading_profile = st.text_input("Profile heading text in your doc", value="PROFILE")
    heading_experience = st.text_input("Experience heading text in your doc", value="EMPLOYMENT EXPERIENCE")
    heading_skills = st.text_input("Skills/Core Skills heading text in your doc", value="CORE SKILLS")
    bullets_keep = st.slider("Bullets per role (cap during optimize)", 2, 6, 4)
    add_missing_to_skills = st.checkbox("Add missing JD keywords into Skills (safe nouns only)")
    safe_nouns_only = st.multiselect("Restrict additions to (edit as needed)", 
        ["exploratory testing","functional testing","regression testing","integration testing","ui","ux",
         "test plans","defect management","root-cause analysis","sql","logs","selenium","cypress","playwright",
         "automation","manual testing","release readiness","pre-release testing"],
        default=["exploratory testing","functional testing","regression testing","integration testing",
                 "test plans","defect management","sql","logs","automation","manual testing","release readiness"]
    )

st.divider()

# Step 1: Check ATS
check = st.button("Check ATS")
if check:
    if not resume_file or not jd.strip():
        st.error("Please upload a DOCX resume and paste the Job Description.")
        st.stop()
    src = Document(resume_file)
    baseline_text = read_docx_text(src)
    jd_keywords = extract_keywords_yake(jd, max_terms=int(top_k))
    base_score, base_matched = score_text(baseline_text, jd_keywords)

    st.subheader("Baseline ATS")
    st.metric("ATS Score", f"{base_score}%")
    missing = [k for k in jd_keywords if k not in base_matched]
    report = f"""# Baseline ATS Report
Generated: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}

## JD Keyphrases ({len(jd_keywords)} via YAKE)
{", ".join(jd_keywords)}

## Matched ({len(base_matched)})
{", ".join(sorted(base_matched))}

## Missing
{", ".join(missing) if missing else "—"}
"""
    st.download_button("⬇️ Download Baseline ATS Report (Markdown)", report.encode(), file_name="baseline_ATS_report.md")
    with st.expander("Preview: first 2000 chars of resume text"):
        st.write(baseline_text[:2000])

st.divider()

# Step 2: Optimize
opt = st.button("Optimize Resume")
if opt:
    if not resume_file or not jd.strip():
        st.error("Please upload a DOCX resume and paste the Job Description.")
        st.stop()

    doc = Document(resume_file)
    jd_keywords = extract_keywords_yake(jd, max_terms=int(top_k))

    # 1) Replace Profile
    new_profile = optimize_profile_from_jd(jd_keywords)
    replace_profile(doc, heading_profile, new_profile)

    # 2) Cap bullets under Experience (keeps layout but shortens)
    cap_bullets(doc, heading_experience, int(bullets_keep))

    # 3) Optionally append missing nouns to Skills safely
    if add_missing_to_skills:
        # choose nouns that are in user-approved list
        baseline_text = read_docx_text(doc)
        _, matched_after_profile = score_text(baseline_text, jd_keywords)
        missing_now = [k for k in jd_keywords if k not in matched_after_profile]
        safe_add = [k for k in missing_now if k in [x.lower() for x in safe_nouns_only]]
        appended = append_skills_keywords(doc, heading_skills, safe_add)
        if appended:
            st.info("Added selected missing JD nouns into Skills.")

    # Re-score
    final_text = read_docx_text(doc)
    final_score, final_matched = score_text(final_text, jd_keywords)
    final_missing = [k for k in jd_keywords if k not in final_matched]

    # Downloads
    out_buf = BytesIO(); doc.save(out_buf); out_buf.seek(0)

    st.subheader("Optimized Result")
    st.metric("Optimized ATS Score", f"{final_score}%")
    opt_report = f"""# Optimized ATS Report
Generated: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}

## JD Keyphrases ({len(jd_keywords)} via YAKE)
{", ".join(jd_keywords)}

## Matched after optimization ({len(final_matched)})
{", ".join(sorted(final_matched))}

## Still Missing
{", ".join(final_missing) if final_missing else "—"}

## Changes Applied
- Rewrote PROFILE to 5–6-line condensed text derived from JD keyphrases
- Capped bullets under Experience to {bullets_keep} per role
- {'Added selected JD nouns into Skills' if add_missing_to_skills else 'No auto-insert into Skills'}
"""
    st.download_button("⬇️ Download Optimized Resume (DOCX)", out_buf, file_name="optimized_resume.docx")
    st.download_button("⬇️ Download Optimized ATS Report (Markdown)", opt_report.encode(), file_name="optimized_ATS_report.md")

    with st.expander("Preview: first 2000 chars (optimized text)"):
        st.write(final_text[:2000])
