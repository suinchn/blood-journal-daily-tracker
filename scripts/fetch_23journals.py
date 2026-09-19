#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_23journals.py - PubMed 23本肿瘤/血液学期刊指定日期抓取脚本
提取元数据（标题/摘要/作者/DOI/PMCID/发表日期/卷期页码/关键词），输出 JSON + 初稿报告。

Usage:
    python fetch_23journals.py                       # 抓取最近1天（动态日期，带去重 — cron 用）
    python fetch_23journals.py --date 2026-08-09     # 抓取指定日期（不去重 — 历史补抓/指定日期用）
    python fetch_23journals.py --date 2026-08-09 --days 3   # 指定日期往前 N 天窗口（默认 1 天）

关键设计：
1. 使用 [TA] (NLM Title Abbreviation) 搜索期刊
2. 同时查 pdat AND edat 两种日期字段（Blood 主刊用 edat，BA 等用 pdat）
3. 通过 efetch XML 提取 DOI/PMCID（不用 esummary）
4. 无硬编码日期
5. --date 指定日期时跳过去重（历史数据不污染、不遗漏）
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from collections import defaultdict

# ============================================================
# Configuration
# ============================================================
def get_nlm_api_key():
    """NLM E-utilities API key：优先环境变量 NLM_API_KEY，否则读 ~/.config/nlm/api_key。"""
    key = os.environ.get("NLM_API_KEY", "")
    if not key:
        try:
            with open(os.path.join(os.path.expanduser("~"), ".config", "nlm", "api_key"), "r", encoding="utf-8") as f:
                key = f.read().strip()
        except (OSError, IOError):
            key = ""
    return key


NLM_API_KEY = get_nlm_api_key()

# 23 top hematology/oncology journals: (display_name, NLM_Title_Abbreviation)
JOURNALS = [
    ("Transplantation and Cellular Therapy", "Transplant Cell Ther"),
    ("Blood Cancer Journal", "Blood Cancer J"),
    ("American Journal of Hematology", "Am J Hematol"),
    ("British Journal of Haematology", "Br J Haematol"),
    ("Blood", "Blood"),
    ("Blood Advances", "Blood Adv"),
    ("Bone Marrow Transplantation", "Bone Marrow Transplant"),
    ("Journal of Clinical Oncology", "J Clin Oncol"),
    ("Journal of Hematology & Oncology", "J Hematol Oncol"),
    ("Journal for ImmunoTherapy of Cancer", "J Immunother Cancer"),
    ("Signal Transduction and Targeted Therapy", "Signal Transduct Target Ther"),
    ("The Lancet Haematology", "Lancet Haematol"),
    ("New England Journal of Medicine", "N Engl J Med"),
    ("Nature Cancer", "Nat Cancer"),
    ("Leukemia", "Leukemia"),
    ("Cancer Cell", "Cancer Cell"),
    ("Experimental Hematology & Oncology", "Exp Hematol Oncol"),
    ("CA: A Cancer Journal for Clinicians", "CA Cancer J Clin"),
    ("Mayo Clinic Proceedings", "Mayo Clin Proc"),
    ("Journal of Translational Medicine", "J Transl Med"),
    ("The Lancet Regional Health – Western Pacific", "Lancet Reg Health West Pac"),
    ("Haematologica", "Haematologica"),
    ("Annals of Hematology", "Ann Hematol"),
]

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SKILL_DIR), "data")
os.makedirs(DATA_DIR, exist_ok=True)

ARTICLES_FILE = os.path.join(SKILL_DIR, "fetched_articles.json")  # 仅无 --date 时使用

# ============================================================
# Hematology relevance filter (血液相关性过滤)
# 纯血液学期刊全量保留；综合/肿瘤期刊（JCO/Nat Cancer/Cancer Cell/STTT/NEJM 等）按
# 标题+摘要的血液信号词命中情况过滤，剔除纯实体瘤文章。被剔除文章存入 JSON 的 excluded。
# ============================================================
PURE_HEMATOLOGY_JOURNALS = {
    "Blood", "Blood Adv", "Am J Hematol", "Br J Haematol", "Haematologica",
    "Leukemia", "Lancet Haematol", "Blood Cancer J", "Bone Marrow Transplant",
    "Transplant Cell Ther", "Ann Hematol",
}

HEMATOLOGY_KEYWORDS = [
    # 血液肿瘤
    "leukaemia", "leukemia", "leukemic", "leukaemic", "lymphoma", "myeloma",
    "myelodysplas", "myeloproliferative", "myelofibrosis", "polycythemia", "polycythaemia",
    "essential thrombocyth", "amyloidosis", "waldenstr", "macroglobulinemia", "macroglobulinaemia",
    "monoclonal gammopathy", "plasma cell", "myeloid sarcoma", "clonal hematopoiesis",
    "chimeric antigen receptor", "car-t", "car t cell", "hematopoiesis",
    "hematolog", "haematolog", "hemato-oncology", "haemato-oncology",
    "acute myeloid", "acute lymphoblastic", "acute promyelocytic",
    "chronic myeloid", "chronic lymphocytic", "chronic lymphoblastic",
    "graft-versus-host", "graft versus host", "gvhd", "conditioning regimen",
    "stem cell transplant", "hematopoietic stem cell", "haematopoietic stem cell",
    "hematopoietic cell transplant", "haematopoietic cell transplant",
    "bone marrow transplant", "allogeneic", "autologous transplant",
    # 良性血液病与出凝血
    "aplastic", "paroxysmal nocturnal hemoglobin", "paroxysmal nocturnal haemoglobin",
    "hemoglobinopathy", "haemoglobinopathy", "sickle cell", "thalassemia", "thalassaemia",
    "hemophilia", "haemophilia", "von willebrand", "thrombocytopenia",
    "immune thrombocytopenia", "hemolytic uremic syndrome",
    "disseminated intravascular coagulation", "thrombotic thrombocytopenic",
    "thrombosis", "thrombotic", "venous thromboembolism", "pulmonary embolism", "deep vein thromb",
    "anticoagulation", "anticoagulant", "heparin", "warfarin", "direct oral anticoagulant",
    "rivaroxaban", "apixaban", "edoxaban", "dabigatran", "factor xa inhibitor",
    "coagulation", "coagulopathy", "hemostasis", "haemostasis", "fibrinogen",
    "platelet", "thrombus", "thrombocytosis", "transfusion", "red blood cell",
    "neutrophil", "eosinophil", "mast cell", "mastocytosis",
    "myeloid", "erythroid", "megakaryocyte", "plasmacytom",
    "blood cancer", "hematologic malignancy", "haematologic malignancy",
    # 血液肿瘤专科药物（强信号）
    "blinatumomab", "daratumumab", "ibrutinib", "venetoclax", "lenalidomide",
    "pomalidomide", "bortezomib", "carfilzomib", "thalidomide", "isatuximab",
    "elotuzumab", "teclistamab", "belantamab", "imatinib", "gilteritinib", "midostaurin",
]
HEMATOLOGY_ACRONYMS = ["AML", "CML", "CLL", "MDS", "MPN", "PNH", "ITP", "TTP", "HUS",
                       "DLBCL", "NHL", "cHL", "MCL", "PTCL", "HL"]

SOLID_TUMOR_KEYWORDS = [
    "breast cancer", "breast carcinoma", "lung cancer", "non-small cell lung", "nonsmall cell lung",
    "small cell lung", "colorectal", "colon cancer", "gastric cancer", "esophage",
    "pancreatic", "prostate", "bladder cancer", "urothelial", "ovarian", "endometrial",
    "cervical cancer", "hepatocellular", "cholangiocarcinoma", "renal cell",
    "glioblastoma", "glioma", "head and neck", "thyroid cancer", "melanoma", "osteosarcoma",
    "neuroblastoma", "retinoblastoma", "nephroblastoma", "squamous cell carcinoma",
    "adenocarcinoma", "solid tumor", "solid tumour", "nasopharyngeal carcinoma",
]

# 短缩写区分大小写 + 词边界，避免误伤普通词（如 ALL/all/MM）
_HEME_RE = re.compile("|".join(re.escape(k) for k in HEMATOLOGY_KEYWORDS), re.IGNORECASE)
_SOLID_RE = re.compile("|".join(re.escape(k) for k in SOLID_TUMOR_KEYWORDS), re.IGNORECASE)
_ACRONYM_RE = re.compile(r"\b(?:" + "|".join(re.escape(a) for a in HEMATOLOGY_ACRONYMS) + r")\b")


def is_hematology_relevant(article):
    """血液相关性判定：纯血液期刊→保留；其余期刊要求 标题+摘要 至少命中1条血液信号，
    且 ①无实体瘤信号 或 ②血液信号≥2 且超过实体瘤信号 才保留。"""
    if article.get("journal_iso", "") in PURE_HEMATOLOGY_JOURNALS:
        return True
    text = f"{article.get('title', '')}\n{article.get('abstract', '')}"
    heme_hits = len(_HEME_RE.findall(text)) + len(_ACRONYM_RE.findall(text))
    solid_hits = len(_SOLID_RE.findall(text))
    if solid_hits == 0:
        return heme_hits >= 1
    return heme_hits >= 2 and heme_hits > solid_hits


def filter_hematology(articles):
    """按血液相关性过滤文章，返回 (保留列表, 排除列表)。"""
    kept, excluded = [], []
    for art in articles:
        (kept if is_hematology_relevant(art) else excluded).append(art)
    return kept, excluded

# ============================================================
# Keyword translation map (first-pass only, AI 后续完整翻译)
# ============================================================
KEYWORD_MAP = {
    "multiple myeloma": "多发性骨髓瘤",
    "acute myeloid leukemia": "急性髓系白血病",
    "acute lymphoblastic leukemia": "急性淋巴细胞白血病",
    "chronic lymphocytic leukemia": "慢性淋巴细胞白血病",
    "myeloproliferative neoplasm": "骨髓增殖性肿瘤",
    "myelodysplastic syndrome": "骨髓增生异常综合征",
    "CAR-T": "CAR-T",
    "chimeric antigen receptor T-cell": "嵌合抗原受体T细胞",
    "lymphoma": "淋巴瘤",
    "non-Hodgkin lymphoma": "非霍奇金淋巴瘤",
    "Hodgkin lymphoma": "霍奇金淋巴瘤",
    "myelofibrosis": "骨髓纤维化",
    "polycythemia vera": "真性红细胞增多症",
    "essential thrombocythemia": "原发性血小板增多症",
    "paroxysmal nocturnal hemoglobinuria": "阵发性睡眠性血红蛋白尿",
    "aplastic anemia": "再生障碍性贫血",
    "immune thrombocytopenia": "免疫性血小板减少症",
    "thalassemia": "地中海贫血",
    "sickle cell disease": "镰状细胞病",
    "hemophilia": "血友病",
    "deep vein thrombosis": "深静脉血栓形成",
    "pulmonary embolism": "肺栓塞",
    "disseminated intravascular coagulation": "弥散性血管内凝血",
    "minimal residual disease": "微小残留病",
    "MRD": "MRD",
    "allogeneic hematopoietic stem cell transplantation": "异基因造血干细胞移植",
    "autologous hematopoietic stem cell transplantation": "自体造血干细胞移植",
    "novel": "",
    "randomized": "",
    "phase 3": "",
    "trial": "",
}

def translate_keywords(text):
    """First-pass keyword replacement for titles."""
    result = text
    for en, cn in KEYWORD_MAP.items():
        if cn:
            result = re.sub(re.escape(en), cn, result, flags=re.IGNORECASE)
    return result


# ============================================================
# Duplicate checking
# ============================================================
def load_existing_pmid_set():
    """Load PMIDs from fetched_articles.json for deduplication."""
    pmids = set()
    if os.path.exists(ARTICLES_FILE):
        try:
            with open(ARTICLES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for article in data:
                    pmid = article.get("pmid")
                    if pmid:
                        pmids.add(str(pmid))
        except (json.JSONDecodeError, IOError):
            pass
    return pmids


def is_duplicate(pmid, existing_pmids):
    return str(pmid) in existing_pmids


# ============================================================
# PubMed API functions
# ============================================================
def pubmed_request(url, max_retries=3, ssl_verify=True):
    """Make a request to PubMed E-utilities with retry."""
    import ssl
    # Windows schannel 证书吊销检查（CRYPT_E_REVOCATION_OFFLINE）会导致请求失败/挂起。
    # 本脚本仅访问 eutils.ncbi.nlm.nih.gov（公开文献接口，不传输任何凭证/隐私数据），故可跳过证书校验。
    ctx = None
    if not ssl_verify:
        ctx = ssl._create_unverified_context()
        ctx.check_hostname = False
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'BloodJournalTracker/1.0 (research; contact@example.com)')
            if ctx is not None:
                with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                    return response.read()
            else:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return response.read()
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                print(f"  ERROR after {max_retries} retries: {e}", file=sys.stderr)
                return None
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                print(f"  ERROR after {max_retries} retries: {e}", file=sys.stderr)
                return None
    return None


def search_pubs(ta, datetype, date_from, date_to):
    """Search PubMed for articles by journal TA and date type.
    CRITICAL: Must call TWICE per journal — pdat and edat (Blood uses edat, others pdat).
    """
    import ssl
    # Disable SSL verification due to Windows certificate revocation issues on some networks
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = urllib.parse.urlencode({
        "db": "pubmed",
        "term": f'"{ta}"[TA]',
        "datetype": datetype,
        "mindate": date_from,
        "maxdate": date_to,
        "retmax": 50,
        "retmode": "json",
        "api_key": NLM_API_KEY,
    })
    url = f"{base_url}?{params}"
    data = pubmed_request(url, ssl_verify=False)
    if data is None:
        return []
    try:
        result = json.loads(data.decode("utf-8"))
        return result.get("esearchresult", {}).get("idlist", [])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []


def efetch_pmids(pmids):
    """Fetch full article XML for a batch of PMIDs and parse."""
    if not pmids:
        return []

    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(str(p) for p in pmids),
        "retmode": "xml",
        "rettype": "manifest",
        "api_key": NLM_API_KEY,
    })
    url = f"{base_url}?{params}"

    time.sleep(0.3)

    data = pubmed_request(url, ssl_verify=False)
    if data is None:
        return []

    return parse_articles_xml(data)


def parse_articles_xml(xml_data):
    """Parse PubMed XML response to extract article metadata.
    NOTE: PubMed efetch XML does NOT use namespaces — plain tags.
    """
    articles = []
    try:
        xml_str = xml_data.decode("utf-8")
        root = ET.fromstring(xml_str)
    except (UnicodeDecodeError, ET.ParseError) as e:
        print(f"  XML parse error: {e}", file=sys.stderr)
        return articles

    for article in root.iter("PubmedArticle"):
        try:
            med_cita = article.find("MedlineCitation")
            if med_cita is None:
                continue

            pmid_elem = med_cita.find("PMID")
            pmid = pmid_elem.text if pmid_elem is not None else ""

            article_elem = med_cita.find("Article")
            if article_elem is None:
                continue

            # Title — 用 itertext() 完整提取（标题可能含 <i>/<b> 子标签，.text 会截断）
            title_elem = article_elem.find(".//ArticleTitle")
            title = "".join(title_elem.itertext()).strip() if title_elem is not None else ""

            # Abstract — 完整提取：AbstractText 可能含 <b>/<i>/<u> 子标签，
            # 必须用 itertext() 拼接全部文本，否则摘要被截断（如 "TGF-β1 drives ... to promote CD8" 后半丢失）
            abstract_parts = []
            for abs_text in article_elem.findall(".//AbstractText"):
                text = "".join(abs_text.itertext()).strip()
                label = abs_text.get("Label", "")
                if label:
                    text = f"[{label}] {text}"
                if text:
                    abstract_parts.append(text)
            abstract = " ".join(abstract_parts)

            # Authors
            authors = []
            for auth in article_elem.findall(".//Author"):
                last = (auth.find("LastName") is not None and auth.find("LastName").text) or ""
                fore = (auth.find("ForeName") is not None and auth.find("ForeName").text) or ""
                if last:
                    authors.append(f"{fore} {last}".strip())

            # Journal
            journal_elem = article_elem.find(".//Journal/Title")
            journal_full = journal_elem.text if journal_elem is not None else ""
            iso_abbr_elem = article_elem.find(".//Journal/ISOAbbreviation")
            journal_iso = iso_abbr_elem.text if iso_abbr_elem is not None else ""

            # Publication date
            pub_date = ""
            for date_tag in ["PubDate", "JournalIssue/PubDate"]:
                date_elem = article_elem.find(f".//{date_tag}")
                if date_elem is not None:
                    year = (date_elem.find("Year") is not None and date_elem.find("Year").text) or ""
                    month = (date_elem.find("Month") is not None and date_elem.find("Month").text) or ""
                    day = (date_elem.find("Day") is not None and date_elem.find("Day").text) or ""
                    if year:
                        pub_date = f"{year}-{month.zfill(2) if month else '01'}-{day.zfill(2) if day else '01'}"
                        break

            # DOI and PMCID
            doi = ""
            pmcid = ""
            article_ids = article_elem.findall(".//ArticleId")
            for aid in article_ids:
                id_type = aid.get("IdType", "")
                if id_type == "doi":
                    doi = aid.text or ""
                elif id_type == "pmc":
                    pmcid = aid.text or ""

            # Volume, Issue, Pages
            volume = ""
            issue = ""
            pages = ""
            journal_issue = article_elem.find(".//JournalIssue")
            if journal_issue is not None:
                vol_elem = journal_issue.find("Volume")
                iss_elem = journal_issue.find("Issue")
                if vol_elem is not None:
                    volume = vol_elem.text or ""
                if iss_elem is not None:
                    issue = iss_elem.text or ""
            pagination_elem = article_elem.find(".//Pagination")
            if pagination_elem is not None:
                medline_pg = pagination_elem.find("MedlinePgn")
                if medline_pg is not None:
                    pages = medline_pg.text or ""

            # Keywords
            keywords = []
            for kw in article_elem.findall(".//Keyword"):
                keywords.append(kw.text or "")

            article_data = {
                "pmid": str(pmid),
                "title": title,
                "abstract": abstract.strip(),
                "authors": authors,
                "author_string": ", ".join(authors[:5]) + (", et al." if len(authors) > 5 else ""),
                "journal_full": journal_full,
                "journal_iso": journal_iso,
                "pub_date": pub_date,
                "doi": doi,
                "pmcid": pmcid,
                "volume": volume,
                "issue": issue,
                "pages": pages,
                "keywords": keywords,
            }
            articles.append(article_data)
        except Exception as e:
            print(f"  Failed to parse article: {e}", file=sys.stderr)
            continue

    return articles


# ============================================================
# Rating / Prioritization
# ============================================================
def rate_article(article):
    """Rate an article 1-5 based on keywords and content."""
    score = 3  # default
    title_lower = (article.get("title", "") + " " + article.get("abstract", "")).lower()

    p0_keywords = [
        "randomized controlled trial", "phase 3", "phase iii", "randomised",
        "meta-analysis", "systematic review", "guideline", "consensus",
        "overall survival", "os benefit", "progression-free survival",
        "car-t", "chimeric antigen receptor", "bispecific", "blinatumomab",
        "allogeneic transplant", "autologous transplant", "stem cell transplantation",
        "mrd-negative", "mrd阴性", "minimal residual disease",
        "front-line", "first-line", "newly diagnosed",
    ]

    p1_keywords = [
        "novel", "prospective", "retrospective cohort", "real-world",
        "mechanism", "mutation", "genomic", "biomarker",
        "resistance", "relapse", "refractory",
        "ibrutinib", "venetoclax", "carfilzomib", "daratumumab",
        "bortezomib", "lenalidomide", "pomalidomide", "isatuximab",
        "belantamab", "teclistamab", "tarlatamab",
    ]

    for kw in p0_keywords:
        if kw in title_lower:
            score = max(score, 5)
            break

    if score < 5:
        for kw in p1_keywords:
            if kw in title_lower:
                score = max(score, 4)
                break

    low_value = ["case report", "letter to the editor", "brief communication"]
    for kw in low_value:
        if kw in title_lower:
            score = min(score, 2)
            break

    return min(max(score, 1), 5)


# 临床/实验分类：强信号加权判定（旧版"计数比拼"会把机制文章里的 patients/survival/cohort
# 泛词误算为临床，导致机制/动物/细胞研究被错分——已根治，2026-08-27）
DECISIVE_TRIAL_TERMS = [
    "randomized", "randomised", "clinical trial", "guideline", "consensus",
    "position statement", "meta-analysis", "systematic review", "real-world",
    "registry", "open-label", "double-blind", "multicenter", "multi-center",
]
_PHASE_RE = re.compile(r"\bphase\s*(?:[1-3]|i{1,3})\b", re.IGNORECASE)
# "104 patients/12 cases" 等患者队列数字是临床研究最可靠的特征（机制论文通常只提"from patients"）
# "104 patients / 104 CAR-T recipients / 12 cases" 等患者队列数字是临床研究最可靠的特征
# （机制论文通常只提 "from patients"）；数字后先吞空白，中间允许至多 4 个修饰词
_PATIENT_COUNT_RE = re.compile(
    r"\b\d{1,4}\b[\s&,-]*(?:[\w./-]+[\s&,-]*){0,4}"
    r"(?:patients?|participants?|subjects?|individuals?|cases?|recipients?|adults?|children?)\b",
    re.IGNORECASE)

CLINICAL_STRONG = [
    "randomized", "randomised", "controlled trial", "clinical trial",
    "guideline", "consensus", "meta-analysis", "systematic review", "real-world",
    "registry", "retrospective", "prospective", "cohort",
    "overall survival", "progression-free", "disease-free", "event-free",
    "median follow-up", "open-label", "double-blind", "multicenter", "multi-center",
    "single-arm", "objective response", "complete response", "partial response",
    "adverse events", "safety profile", "case report", "case series",
]
CLINICAL_WEAK = [
    "patients", "patient", "treatment", "therapy", "therapeutic", "outcome",
    "survival", "prognos", "diagnos", "incidence", "prevalence", "clinical",
    "trial", "safety", "efficacy",
]

EXPERIMENTAL_STRONG = [
    "in vitro", "in vivo", "mice", "mouse", "murine", "xenograft",
    "patient-derived xenograft", "cell line", "cell lines", "organoid", "zebrafish",
    "knockout", "knock-in", "knockdown", "sirna", "shrna", "crispr", "sgrna",
    "plasmid", "transfection", "transduction", "overexpress",
    "western blot", "immunoblot", "scRNA-seq", "single-cell", "single cell",
    "chip-seq", "atac-seq", "transcriptomic", "spatial transcriptomic", "proteomic",
    "flow cytometry", "immunohistochemistry", "co-culture",
    "signaling pathway", "signalling pathway", "phosphorylation", "lactylation",
    "self-renewal", "colony formation", "apoptosis", "apoptotic", "cell cycle",
    "cytotoxicity", "cytotoxic activity", "t-cell activation", "t cell activation",
    "leukemia stem cell", "cancer stem cell", "stem-like",
    "microenvironment", "extracellular vesicle", "exosome", "oncosome",
    "mesenchymal stromal", "stromal cell",
    "molecular mechanism", "preclinical", "animal model", "mouse model",
    "murine model", "xenotransfusion", "tumorigenesis", "oncogenic",
]
EXPERIMENTAL_WEAK = [
    "proliferation", "differentiation", "expression", "pathway", "mechanism",
    "engineered", "engineering", "regulates",
]


def classify_study_type(article):
    """Classify as 'clinical' or 'experimental' based on content.

    判定优先级（强信号加权，根治泛词误判）：
    1. 明确试验/指南类词（RCT/phase/guideline/meta-analysis/real-world/registry 等）
       且实验强信号≤1 → 临床
    2. 实验强信号≥2 且临床强信号≤1 → 实验
    3. 明确试验/指南类词 → 临床（无论实验信号）
    4. 临床强信号≥2 → 临床
    5. 实验强信号≥1 → 实验（单个强实验信号优先于弱临床词）
    6. 临床强信号≥1 → 临床
    7. 兜底：弱信号计数比较
    """
    text = (article.get("title", "") + " " + article.get("abstract", "")).lower()
    exp_s = sum(1 for kw in EXPERIMENTAL_STRONG if kw in text)
    cli_s = sum(1 for kw in CLINICAL_STRONG if kw in text)
    cli_s += 1 if _PATIENT_COUNT_RE.search(text) else 0  # "104 patients" 患者队列
    exp_w = sum(1 for kw in EXPERIMENTAL_WEAK if kw in text)
    cli_w = sum(1 for kw in CLINICAL_WEAK if kw in text)
    trial = any(kw in text for kw in DECISIVE_TRIAL_TERMS) or bool(_PHASE_RE.search(text))

    if trial and exp_s <= 1:
        return "clinical"
    if exp_s >= 2 and cli_s <= 1:
        return "experimental"
    if trial:
        return "clinical"
    if cli_s >= 2:
        return "clinical"
    if exp_s >= 1 and cli_s == 0:
        return "experimental"
    if cli_s >= 1:
        return "clinical"
    return "experimental" if exp_w > cli_w else "clinical"


# ============================================================
# Full-text availability check
# ============================================================
def get_fulltext_status(article):
    """Check if full text is available via PMC OA or publisher."""
    pmcid = article.get("pmcid", "")

    if pmcid and pmcid.startswith("PMC"):
        return "fulltext"  # 📖全文

    journal_iso = article.get("journal_iso", "").lower()
    oa_journals = ["blood adv", "blood cancer j", "j hematol oncol", "signal transduct target ther", "exp hematol oncol"]
    if journal_iso in oa_journals:
        return "fulltext"

    return "abstract_only"  # 📄仅摘要


# ============================================================
# Save & Load articles
# ============================================================
def save_articles(new_articles, existing_pmids, report_date, today):
    """Append new articles to fetched_articles.json and trim old entries."""
    all_articles = []

    if os.path.exists(ARTICLES_FILE):
        try:
            with open(ARTICLES_FILE, "r", encoding="utf-8") as f:
                all_articles = json.load(f)
        except (json.JSONDecodeError, IOError):
            all_articles = []

    for art in new_articles:
        all_articles.append({
            **art,
            "fetched_date": report_date,
        })
        existing_pmids.add(str(art["pmid"]))

    cutoff = (today - timedelta(days=60)).strftime("%Y%m%d")
    trimmed = [a for a in all_articles if a.get("fetched_date", "0") >= cutoff]

    if len(trimmed) > 1000:
        trimmed = sorted(trimmed, key=lambda x: x.get("fetched_date", ""), reverse=True)[:1000]

    with open(ARTICLES_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


# ============================================================
# Abstract completion (补抓摘要)
# ============================================================
def refetch_abstracts(articles):
    """对摘要为空或过短(<30字符)的文章逐篇重抓一次（单篇 efetch），
    修复批量请求偶发字段缺失；仍无摘要则标记 abstract_source="none"。
    返回补抓到摘要的篇数。"""
    recovered = 0
    for art in articles:
        if len(art.get("abstract", "")) >= 30:
            art["abstract_source"] = "pubmed"
            continue
        pmid = art.get("pmid", "")
        if not pmid:
            art["abstract_source"] = "none"
            continue
        print(f"  Refetching abstract for PMID {pmid}...", file=sys.stderr)
        time.sleep(0.3)
        retried = efetch_pmids([pmid])
        if retried and len(retried[0].get("abstract", "")) >= 30:
            art["abstract"] = retried[0]["abstract"]
            art["title"] = retried[0]["title"] or art["title"]
            art["abstract_source"] = "pubmed"
            recovered += 1
        else:
            art["abstract_source"] = "none"
    return recovered


# ============================================================
# Generate initial report (first-pass, needs AI rewrite)
# ============================================================
def generate_report(all_articles, report_date_str, today, excluded_count=0):
    """Generate the first-pass markdown report."""
    for art in all_articles:
        art["rating"] = rate_article(art)
        art["study_type"] = classify_study_type(art)
        art["fulltext_status"] = get_fulltext_status(art)

    p0_clinical = [a for a in all_articles if a["rating"] == 5 and a["study_type"] == "clinical"]
    p1_clinical = [a for a in all_articles if a["rating"] == 4 and a["study_type"] == "clinical"]
    p0_experimental = [a for a in all_articles if a["rating"] == 5 and a["study_type"] == "experimental"]
    p1_experimental = [a for a in all_articles if a["rating"] == 4 and a["study_type"] == "experimental"]
    p2_others = [a for a in all_articles if a["rating"] <= 3]

    def sort_key(a):
        return (-a["rating"], a.get("pub_date", ""))

    p0_clinical.sort(key=sort_key)
    p1_clinical.sort(key=sort_key)
    p0_experimental.sort(key=sort_key)
    p1_experimental.sort(key=sort_key)
    p2_others.sort(key=sort_key)

    journals_with_results = set(a["journal_iso"] for a in all_articles)
    fulltext_count = sum(1 for a in all_articles if a["fulltext_status"] == "fulltext")
    clinical_count = sum(1 for a in all_articles if a["study_type"] == "clinical")
    experimental_count = sum(1 for a in all_articles if a["study_type"] == "experimental")

    lines = []
    lines.append(f"# 🩸 血液朝夕录 — {report_date_str}")
    lines.append("")
    lines.append("## 📊 今日概览")
    lines.append(f"- **获取期刊数**: {len(journals_with_results)}/23 ✅")
    lines.append(f"- **新文章数**: {len(all_articles)} 篇（📖全文 {fulltext_count} / 📄仅摘要 {len(all_articles)-fulltext_count}）")
    lines.append(f"- **临床研究**: {clinical_count} 篇 | **实验研究**: {experimental_count} 篇")
    if excluded_count:
        lines.append(f"- **非血液相关已剔除**: {excluded_count} 篇")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- Clinical Research Section ----
    lines.append("## 🔬 第一部分：临床研究")
    lines.append("")

    if p0_clinical:
        lines.append("### P0 — 重磅临床研究（★★★★★）")
        lines.append("")
        for i, art in enumerate(p0_clinical, 1):
            lines.extend(format_article(art, i, stars=5))
            lines.append("---")
            lines.append("")

    if p1_clinical:
        lines.append("### P1 — 重要临床研究（★★★★）")
        lines.append("")
        for i, art in enumerate(p1_clinical, len(p0_clinical)+1):
            lines.extend(format_article(art, i, stars=4))
            lines.append("---")
            lines.append("")

    if not p0_clinical and not p1_clinical:
        all_clinical = [a for a in all_articles if a["study_type"] == "clinical"]
        if all_clinical:
            lines.append("### 临床研究")
            lines.append("")
            for i, art in enumerate(all_clinical, 1):
                stars = min(art["rating"], 5)
                lines.extend(format_article(art, i, stars=stars))
                lines.append("---")
                lines.append("")
        else:
            lines.append("*今日无新的临床研究文章*")
            lines.append("")

    lines.append("#### 📌 临床经验提升总结")
    lines.append("> [待AI补充：请根据上述研究数据填写具体临床启示]")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- Experimental Research Section ----
    lines.append("## 🧪 第二部分：实验研究（基础科学）")
    lines.append("")

    if p0_experimental:
        lines.append("### P0 — 重磅基础研究（★★★★★）")
        lines.append("")
        for i, art in enumerate(p0_experimental, 1):
            lines.extend(format_article(art, i, stars=5))
            lines.append("---")
            lines.append("")

    if p1_experimental:
        lines.append("### P1 — 重要基础研究（★★★★）")
        lines.append("")
        for i, art in enumerate(p1_experimental, len(p0_experimental)+1):
            lines.extend(format_article(art, i, stars=4))
            lines.append("---")
            lines.append("")

    if not p0_experimental and not p1_experimental:
        all_exp = [a for a in all_articles if a["study_type"] == "experimental"]
        if all_exp:
            lines.append("### 实验研究")
            lines.append("")
            for i, art in enumerate(all_exp, 1):
                stars = min(art["rating"], 5)
                lines.extend(format_article(art, i, stars=stars))
                lines.append("---")
                lines.append("")
        else:
            lines.append("*今日无新的实验研究文章*")
            lines.append("")

    lines.append("#### 💡 研究启示总结")
    lines.append("> [待AI补充：请根据上述研究数据填写具体研究启示]")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- P2 Others Table ----
    if p2_others:
        lines.append("## 📋 P2 — 其他值得关注（★★★）精选")
        lines.append("| # | 期刊 | 中文标题 | 类型 | 一句话总结 | PMID | PubMed链接 | 全文状态 |")
        lines.append("|---|------|---------|------|-----------|------|------------|---------|")
        for i, art in enumerate(p2_others[:30], 1):
            title_cn = translate_keywords(art["title"])
            ft_mark = "📖全文" if art["fulltext_status"] == "fulltext" else "📄仅摘要 ⚠️"
            pubmed_link = f"[PubMed](https://pubmed.ncbi.nlm.nih.gov/{art['pmid']}/)"
            a_type = infer_article_type(art)
            lines.append(f"| {i} | {art['journal_iso']} | {title_cn} | {a_type} | [AI填写一句话总结] | {art['pmid']} | {pubmed_link} | {ft_mark} |")
        lines.append("")

    # ---- Footer ----
    lines.append("---")
    lines.append("")
    lines.append(f"*本报告由血液期刊追踪 skill 自动生成于 {report_date_str} | 数据来源: PubMed + PMC OA*")
    lines.append("")
    lines.append("### 📌 今日快速提醒")
    lines.append("[待AI补充：3条以内的关键提醒]")

    return "\n".join(lines)


def format_article(art, index, stars=3):
    """Format a single article entry for the report."""
    title_cn = translate_keywords(art["title"])
    ft_mark = "📖全文" if art["fulltext_status"] == "fulltext" else "📄仅摘要 ⚠️"
    star_str = "★" * stars + "☆" * (5 - stars)

    lines = []
    lines.append(f"### {index}. {title_cn}")
    lines.append(f"*{art['title']}*")
    lines.append("")
    pubmed_link = f"[PubMed](https://pubmed.ncbi.nlm.nih.gov/{art['pmid']}/)"
    lines.append(f"**来源**：{art['journal_iso']} | PMID: {art['pmid']} ({pubmed_link}) | {star_str} | {ft_mark}")
    lines.append(f"- **作者**: {art['author_string']}")
    lines.append(f"- **发表日期**: {art.get('pub_date', 'N/A')}")
    lines.append("")

    if art["abstract"]:
        abs_preview = art["abstract"][:500] + ("..." if len(art["abstract"]) > 500 else "")
        lines.append(f"> **中文摘要**: [待翻译] {abs_preview}")
    else:
        lines.append("> ⚠️ **PubMed 无摘要**（该文章类型可能为勘误/回复信/评论等，需 AI 按标题与类型给出说明）")
    lines.append("")

    return lines


def infer_article_type(art):
    """根据标题/内容推断文章类型（用于 P2 表格的「类型」列，AI 最终报告可再精化）。"""
    text = (art.get("title", "") + " " + art.get("abstract", "")).lower()
    if "correction" in text or text.startswith("erratum") or "corrigendum" in text:
        return "勘误"
    if any(k in text for k in ["response to", "reply", "correspondence", "comment on", "in reply"]):
        return "回复信"
    if "case report" in text or "case series" in text:
        return "病例报告"
    if any(k in text for k in ["review", "systematic review", "meta-analysis", "guideline", "consensus", "position statement"]):
        return "综述"
    # 基础研究信号词
    if any(k in text for k in ["single-cell", "in vitro", "mouse", "mice", "xenograft",
                               "mechanism", "signaling", "cell line", "crispr", "knockout",
                               "transcriptomic", "proteomic", "pathway", "trained immunity"]):
        return "基础研究"
    # 转化研究信号词（生物标志物/血浆水平类）
    if any(k in text for k in ["elevated", "biomarker", "levels", "plasma", "serum", "predict"]):
        return "转化研究"
    if art.get("study_type") == "experimental":
        return "基础研究"
    if "letter" in text or "brief communication" in text:
        return "通讯"
    return "临床研究"


# ============================================================
# Main execution
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="23本血液/肿瘤学期刊 PubMed 抓取")
    parser.add_argument("--date", type=str, default=None,
                        help="指定抓取日期 YYYY-MM-DD（默认：最近1天，带去重）")
    parser.add_argument("--days", type=int, default=1,
                        help="日期窗口天数（仅与 --date 联用，默认1天）")
    args = parser.parse_args()

    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print(f"ERROR: 日期格式错误: {args.date}，应为 YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
        today = target
        report_date = target.strftime("%Y%m%d")
        date_from = (target - timedelta(days=args.days - 1)).strftime("%Y/%m/%d")
        date_to = target.strftime("%Y/%m/%d")
        skip_dedup = True
    else:
        today = datetime.now()
        report_date = today.strftime("%Y%m%d")
        date_from = (today - timedelta(days=1)).strftime("%Y/%m/%d")
        date_to = today.strftime("%Y/%m/%d")
        skip_dedup = False

    output_json = os.path.join(DATA_DIR, f"daily_report_{report_date}.json")
    output_md = os.path.join(DATA_DIR, f"daily_report_{report_date}.md")

    print(f"=== Blood Journal Tracker (23 journals) ===", file=sys.stderr)
    print(f"Report date: {report_date}", file=sys.stderr)
    print(f"Search window: {date_from} to {date_to}", file=sys.stderr)
    print(f"Skip dedup: {skip_dedup}", file=sys.stderr)

    existing_pmids = set() if skip_dedup else load_existing_pmid_set()
    print(f"Existing PMIDs in database: {len(existing_pmids)}", file=sys.stderr)

    all_new_pmids = []
    journal_counts = {}

    for journal_name, ta in JOURNALS:
        pmids_pdat = search_pubs(ta, "pdat", date_from, date_to)
        pmids_edat = search_pubs(ta, "edat", date_from, date_to)

        merged_pmids = list(set(pmids_pdat + pmids_edat))

        if skip_dedup:
            new_for_journal = merged_pmids
        else:
            new_for_journal = [p for p in merged_pmids if not is_duplicate(p, existing_pmids)]

        count_all = len(merged_pmids)
        count_new = len(new_for_journal)
        journal_counts[journal_name] = {"total": count_all, "new": count_new}

        print(f"  {journal_name}: found={count_all}, new={count_new}", file=sys.stderr)

        if new_for_journal:
            all_new_pmids.extend(new_for_journal)

    total_new = len(all_new_pmids)
    print(f"\nTotal PMIDs to fetch: {total_new}", file=sys.stderr)

    if not all_new_pmids:
        if os.path.exists(output_json) and os.path.getsize(output_json) > 100:
            print(f"No new papers. Using existing data from {output_json}", file=sys.stderr)
            with open(output_json, "r", encoding="utf-8") as f:
                existing = json.load(f)
                articles = existing.get("articles", [])
                if articles:
                    report_md = generate_report(articles, report_date, today)
                    with open(output_md, "w", encoding="utf-8") as f:
                        f.write(report_md)
                    print(f"Report regenerated from cached data: {output_md}", file=sys.stderr)
                else:
                    print("No new papers found and no cached data.", file=sys.stderr)
        else:
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump({"date": report_date, "articles": [], "journal_counts": journal_counts}, f, ensure_ascii=False, indent=2)

            empty_report = f"# 🩸 血液朝夕录 — {report_date}\n\n## 📊 今日概览\n- **新文章数**: 0 篇\n\n*今日无新的血液学期刊文章。*\n"
            with open(output_md, "w", encoding="utf-8") as f:
                f.write(empty_report)

            print("No new papers found.", file=sys.stderr)
        return

    all_articles = []
    batch_size = 200

    for i in range(0, len(all_new_pmids), batch_size):
        batch = all_new_pmids[i:i+batch_size]
        print(f"  Fetching batch {i//batch_size + 1}: PMIDs {batch[0]}-{batch[-1]}", file=sys.stderr)

        articles = efetch_pmids(batch)
        all_articles.extend(articles)

        if i + batch_size < len(all_new_pmids):
            time.sleep(1)

    print(f"Fetched {len(all_articles)} article details", file=sys.stderr)

    if not all_articles:
        print("WARNING: efetch returned no articles despite PMIDs found.", file=sys.stderr)
        return

    # 补抓缺失摘要（单篇重试），确保尽可能多的文章带有完整摘要
    recovered = refetch_abstracts(all_articles)
    print(f"Abstract refetch: recovered {recovered}, missing {sum(1 for a in all_articles if a.get('abstract_source') == 'none')}", file=sys.stderr)

    # 血液相关性过滤：纯血液期刊全量保留，综合/肿瘤期刊剔除实体瘤文章
    original_count = len(all_articles)
    all_articles, excluded_articles = filter_hematology(all_articles)
    if excluded_articles:
        print(f"  Hematology filter: kept {len(all_articles)}/{original_count}, excluded {len(excluded_articles)} non-hematology", file=sys.stderr)

    # 写入 JSON 前补充评分/分类/全文状态，供 AI 翻译步骤确定性分类（P0/P1/P2、临床/实验、全文/摘要）
    for art in all_articles:
        art["rating"] = rate_article(art)
        art["study_type"] = classify_study_type(art)
        art["fulltext_status"] = get_fulltext_status(art)

    output_data = {
        "date": report_date,
        "search_window": {"from": date_from, "to": date_to},
        "articles": all_articles,
        "journal_counts": journal_counts,
        "excluded": [
            {"pmid": a["pmid"], "title": a["title"], "journal_iso": a["journal_iso"]}
            for a in excluded_articles
        ],
    }

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    if not skip_dedup:
        save_articles(all_articles, existing_pmids, report_date, today)

    report_md = generate_report(all_articles, report_date, today, excluded_count=len(excluded_articles))

    with open(output_md, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"\nResults saved:", file=sys.stderr)
    print(f"  JSON: {output_json}", file=sys.stderr)
    print(f"  Markdown: {output_md}", file=sys.stderr)
    print(f"  Total articles: {len(all_articles)}", file=sys.stderr)

    stars_5 = sum(1 for a in all_articles if rate_article(a) == 5)
    stars_4 = sum(1 for a in all_articles if rate_article(a) == 4)
    print(f"SUMMARY: {len(all_articles)} articles found, P0={stars_5}, P1={stars_4}")


if __name__ == "__main__":
    main()
