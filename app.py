import streamlit as st
from sentence_transformers import SentenceTransformer, util
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from PyPDF2 import PdfReader
import docx2txt
import tempfile
import re
import matplotlib.pyplot as plt
import seaborn as sns
from openai import OpenAI
import json

# Load SBERT model
@st.cache_resource(show_spinner="Loading embedding model...")
def load_model():
    return SentenceTransformer('all-MiniLM-L6-v2')

model = load_model()

# Setup DeepSeek LLM client
@st.cache_resource(show_spinner="Connecting to DeepSeek AI...")
def load_llm_client():
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=st.secrets["DEEPSEEK_API_KEY"]
    )

client = load_llm_client()

# Clean text
def clean_text(text):
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text.lower())
    return re.sub(r'\s+', ' ', text).strip()

# Extract resume content
def extract_text_from_file(uploaded_file):
    if uploaded_file.name.endswith(".pdf"):
        pdf_reader = PdfReader(uploaded_file)
        return "\n".join(page.extract_text() for page in pdf_reader.pages if page.extract_text())
    elif uploaded_file.name.endswith(".docx"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            tmp.write(uploaded_file.read())
            return docx2txt.process(tmp.name)
    else:
        return uploaded_file.read().decode("utf-8")

# LLM-enhanced score refinement and bullet suggestions
def llm_adjust_score(score, resume_text, jd_text):
    prompt = f"""
    You're an ATS resume assistant. The user has a resume and a job description. The initial score is {score}%.
    Analyze the alignment based on role expectations, soft skills, and language alignment.
    Suggest a revised score (0–100), a brief feedback note, and 3 improved bullet points for the resume.

    Resume:
    {resume_text[:1500]}

    Job Description:
    {jd_text[:1500]}

    Respond with JSON:
    {{
      "score": revised_score,
      "note": "short feedback",
      "suggestions": ["bullet 1", "bullet 2", "bullet 3"]
    }}
    """
    try:
        response = client.chat.completions.create(
            model="deepseek/deepseek-r1-0528:free",
            messages=[{"role": "user", "content": prompt}]
        )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        return min(parsed.get("score", score), 100), parsed.get("note", ""), parsed.get("suggestions", [])
    except Exception as e:
        return score, "(LLM feedback unavailable)", []

# Calculate ATS score
def calculate_ats_score(resume_text: str, job_description: str):
    resume_clean = clean_text(resume_text)
    jd_clean = clean_text(job_description)

    tfidf = TfidfVectorizer(stop_words="english")
    vectors = tfidf.fit_transform([jd_clean, resume_clean])
    jd_vec, resume_vec = vectors[0], vectors[1]
    keyword_similarity = cosine_similarity(jd_vec, resume_vec)[0][0]
    score_keywords = round(keyword_similarity * 100, 2)

    job_tokens = tfidf.get_feature_names_out()
    resume_counts = resume_vec.toarray()[0]
    matching = [token for token, count in zip(job_tokens, resume_counts) if count > 0]
    missing = [token for token, count in zip(job_tokens, resume_counts) if count == 0]

    emb_jd = model.encode(jd_clean, convert_to_tensor=True)
    emb_resume = model.encode(resume_clean, convert_to_tensor=True)
    semantic_score = util.cos_sim(emb_jd, emb_resume).item()
    score_semantic = round(semantic_score * 100, 2)

    final_score = round((score_keywords * 0.4 + score_semantic * 0.6), 2)
    adjusted_score, llm_feedback, bullet_suggestions = llm_adjust_score(final_score, resume_text, job_description)

    improvement_tips = [
        f"Consider including the term '{word}' in your resume." for word in missing[:10]
    ]

    fit_status = "✅ You are a strong match for this role!" if adjusted_score >= 75 else ("⚠️ You might need some improvements." if adjusted_score >= 50 else "❌ Currently not a fit. Revise your resume.")

    return adjusted_score, score_semantic, score_keywords, matching, missing, improvement_tips, fit_status, llm_feedback, bullet_suggestions


# UI
st.set_page_config(page_title="ATS Resume Scanner", layout="wide")
st.title("📄 ATS Resume Scanner")
st.markdown("Upload your resume and paste the job description to get your ATS match score.")

col1, col2 = st.columns(2)
with col1:
    uploaded_resume = st.file_uploader("📁 Upload Resume (PDF, DOCX, TXT)", type=["pdf", "docx", "txt"])
    resume_text = ""
    if uploaded_resume:
        resume_text = extract_text_from_file(uploaded_resume)
        st.success("Resume text extracted successfully.")
    else:
        resume_text = st.text_area("Or paste your Resume Text", height=250)

with col2:
    jd_input = st.text_area("📌 Paste Job Description", height=300)

if resume_text and jd_input:
    with st.spinner("Analyzing your resume against the job description..."):
        ats_score, semantic_score, keyword_score, matched, missing, tips, fit_status, llm_feedback, suggestions = calculate_ats_score(resume_text, jd_input)

    st.subheader("✅ ATS Match Results")
    st.metric("Final ATS Score", f"{ats_score}%")
    st.metric("Semantic Similarity", f"{semantic_score}%")
    st.metric("Keyword Match Score", f"{keyword_score}%")

    st.markdown(f"### 💬 Role Fit Evaluation\n**{fit_status}**")

    if llm_feedback:
        st.info(f"🤖 LLM Feedback: {llm_feedback}")

    if suggestions:
        with st.expander("✍️ Suggested Resume Bullet Points"):
            for i, tip in enumerate(suggestions, 1):
                st.markdown(f"**{i}.** {tip}")

    with st.expander("✅ Matching Keywords"):
        st.write(", ".join(matched) if matched else "No keywords matched.")

    with st.expander("❌ Missing Keywords"):
        st.write(", ".join(missing) if missing else "All keywords matched!")

    with st.expander("💡 Suggestions to Improve Match"):
        st.write("\n".join(tips) if tips else "Your resume already covers most keywords!")
else:
    st.info("Please upload a resume and enter a job description to get started.")
