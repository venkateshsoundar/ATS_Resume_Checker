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
import spacy

# Load NLP model for skill extraction
nlp = spacy.load("en_core_web_sm")

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

# Extract relevant keywords from text using SpaCy
def extract_keywords(text):
    doc = nlp(text)
    return set([token.lemma_.lower() for token in doc if token.pos_ in ['NOUN', 'PROPN', 'VERB', 'ADJ'] and not token.is_stop])

# LLM-enhanced score refinement and bullet suggestions
def llm_adjust_score(score, resume_text, jd_text):
    prompt = f"""
    You're an advanced ATS evaluator. Analyze the resume and job description below.
    Return:
    - Revised score out of 100
    - Two strengths
    - Two weaknesses
    - Three resume bullet improvements

    Resume: {resume_text[:1200]}
    Job Description: {jd_text[:1200]}

    Respond in JSON:
    {{
      "score": 78,
      "strengths": [""],
      "weaknesses": [""],
      "suggestions": [""],
    }}
    """
    try:
        response = client.chat.completions.create(
            model="deepseek/deepseek-r1-0528:free",
            messages=[{"role": "user", "content": prompt}]
        )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        return min(parsed.get("score", score), 100), parsed.get("strengths", []), parsed.get("weaknesses", []), parsed.get("suggestions", [])
    except Exception:
        return score, [], [], []

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
    matched = [token for token, count in zip(job_tokens, resume_counts) if count > 0]
    missing = [token for token, count in zip(job_tokens, resume_counts) if count == 0]

    emb_jd = model.encode(jd_clean, convert_to_tensor=True)
    emb_resume = model.encode(resume_clean, convert_to_tensor=True)
    semantic_score = util.cos_sim(emb_jd, emb_resume).item()
    score_semantic = round(semantic_score * 100, 2)

    # Adaptive weight logic
    if len(jd_clean.split()) > 200:
        final_score = round((score_keywords * 0.3 + score_semantic * 0.7), 2)
    else:
        final_score = round((score_keywords * 0.5 + score_semantic * 0.5), 2)

    adjusted_score, strengths, weaknesses, bullet_suggestions = llm_adjust_score(final_score, resume_text, job_description)

    tips = [f"Consider including the term '{word}' in your resume." for word in missing[:10]]

    fit_status = "✅ Strong match!" if adjusted_score >= 75 else ("⚠️ Moderate match." if adjusted_score >= 50 else "❌ Low match.")

    return adjusted_score, score_semantic, score_keywords, matched, missing, tips, fit_status, strengths, weaknesses, bullet_suggestions

# Streamlit UI
st.set_page_config(page_title="ATS Resume Scanner", layout="wide")
st.title("📄 ATS Resume Scanner")

col1, col2 = st.columns(2)
with col1:
    uploaded_resume = st.file_uploader("📁 Upload Resume (PDF, DOCX, TXT)", type=["pdf", "docx", "txt"])
    resume_text = ""
    if uploaded_resume:
        resume_text = extract_text_from_file(uploaded_resume)
        st.success("Resume loaded successfully.")
    else:
        resume_text = st.text_area("Or paste your Resume Text", height=250)

with col2:
    jd_input = st.text_area("📌 Paste Job Description", height=300)

if resume_text and jd_input:
    with st.spinner("Analyzing your resume against the job description..."):
        ats_score, semantic_score, keyword_score, matched, missing, tips, fit_status, strengths, weaknesses, suggestions = calculate_ats_score(resume_text, jd_input)

    st.subheader("✅ ATS Match Results")
    st.metric("Final ATS Score", f"{ats_score}%")
    st.metric("Semantic Similarity", f"{semantic_score}%")
    st.metric("Keyword Match Score", f"{keyword_score}%")

    st.markdown(f"### 💬 Fit Evaluation: {fit_status}")

    if strengths:
        st.success("**Strengths:** " + ", ".join(strengths))
    if weaknesses:
        st.warning("**Weaknesses:** " + ", ".join(weaknesses))

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
