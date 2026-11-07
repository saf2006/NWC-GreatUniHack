"""
NWC (NatWest Count) - a private financial "time machine" (hackathon build)
Run:  pip install -r requirements.txt  &&  python -m streamlit run app.py

Principles (say them in your pitch):
 * Every money calculation is plain, checkable Python. AI only explains - it never does the maths.
 * No bank passwords are typed. Real version = Open Banking read-only consent.
 * All premium figures are ILLUSTRATIVE assumptions. Guidance only - not regulated financial advice.
 * Everything runs on the device: translations (built-in, or built once by a local model) and ai.nwc, the pre-set question helper
   (answers come from your own numbers - no free typing, nothing sent anywhere).
"""
import base64
import html as htmllib
import json
import math
import re
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from translations import TR

APP_NAME = "NWC"
FULL_NAME = "NatWest Count"
PURPLE, DARK, LIGHT, RED = "#5A1E8C", "#3B0F5C", "#F3ECF9", "#C8102E"
CB_BLUE, CB_ORANGE = "#0072B2", "#D55E00"  # colour-blind-safe pair (Okabe-Ito)
FIX_SUCCESS = 0.7  # assume a fix only recovers 70% of the extra cost
LOCAL_MODEL = "llama3.2"  # preferred Ollama model (any installed model is used if this one is missing)
OLLAMA_URL = "http://localhost:11434"
HERE = Path(__file__).parent
CACHE_FILE = HERE / "translations_cache.json"

st.set_page_config(page_title=f"{APP_NAME} - {FULL_NAME}", page_icon="🧭", layout="wide")
ss = st.session_state
ss.setdefault("stage", "connect")
ss.setdefault("profile", {"balance": 180.0, "buffer": 150.0, "goal": 50.0, "name": "Maya", "focus": "Building a safety net"})
ss.setdefault("df", None)
ss.setdefault("lang", "English")
ss.setdefault("chat", [])
ss.setdefault("intro1", False)
ss.setdefault("intro2", False)
TODAY = date.today()

# ---------------------------------------------------------------- languages
# Spanish, Polish and French are fully built in (translations.py). Other languages are translated ON THIS DEVICE by your local
# Ollama model the first time they are chosen, then cached in translations_cache.json. Have a native speaker check them.
LANGS = {"English": "en", "Español": "es", "Polski": "pl", "Français": "fr", "Cymraeg": "cy", "اردو": "ur", "বাংলা": "bn", "ਪੰਜਾਬੀ": "pa",
         "العربية": "ar", "Română": "ro", "Português": "pt", "中文": "zh", "हिन्दी": "hi"}
HAND_IDX = {"es": 0, "pl": 1, "fr": 2}
EN_NAME = {"es": "Spanish", "pl": "Polish", "fr": "French", "cy": "Welsh", "ur": "Urdu", "bn": "Bengali", "pa": "Punjabi", "ar": "Arabic",
           "ro": "Romanian", "pt": "Portuguese", "zh": "Simplified Chinese", "hi": "Hindi", "en": "English"}
RTL = {"ar", "ur"}

# Strings added in this version. Order: (Spanish, Polish, French). Anything missing simply shows in English.
NEW_TR = {
    "Extra costs": ("Costes extra", "Dodatkowe koszty", "Coûts supplémentaires"),
    "Your personalised hidden costs": ("Tus costes ocultos personalizados", "Twoje spersonalizowane ukryte koszty", "Vos coûts cachés personnalisés"),
    "Hear from our NWC AI Chatbox on any general queries you might have!": (
        "¡Escucha a nuestro chat de IA de NWC sobre cualquier duda general que tengas!",
        "Posłuchaj naszego czatu AI NWC w sprawie ogólnych pytań, które możesz mieć!",
        "Découvrez ce que notre chat IA NWC peut vous répondre sur toutes vos questions générales !"),
    "Ask your question": ("Haz tu pregunta", "Zadaj pytanie", "Posez votre question"),
    "ai.nwc": ("ai.nwc", "ai.nwc", "ai.nwc"),
    "Answered by ai.nwc": ("Respondido por ai.nwc", "Odpowiedź od ai.nwc", "Réponse d'ai.nwc"),
    "See Extra costs for help and payment plans.": ("Consulta Costes extra para ver ayuda y planes de pago.",
                                                    "Zobacz Dodatkowe koszty, aby poznać pomoc i plany spłat.",
                                                    "Voir Coûts supplémentaires pour de l'aide et des plans de paiement."),
    "Colour-blind friendly colours": ("Colores para daltonismo", "Kolory przyjazne dla daltonistów", "Couleurs adaptées au daltonisme"),
    "Extra text spacing": ("Más espacio en el texto", "Większe odstępy w tekście", "Espacement du texte accru"),
    "Bigger buttons and clear focus": ("Botones más grandes y foco claro", "Większe przyciski i wyraźne zaznaczenie", "Boutons plus grands et focus visible"),
    "Highlight the line I'm reading": ("Resaltar la línea que leo", "Podświetl czytany wiersz", "Surligner la ligne que je lis"),
    "Orange": ("Naranja", "Pomarańczowy", "Orange"),
    "Blue": ("Azul", "Niebieski", "Bleu"),
    "Orange line = 4 weeks of spending covered by savings.": ("Línea naranja = 4 semanas de gasto cubiertas por ahorros.",
                                                              "Pomarańczowa linia = 4 tygodnie wydatków pokryte oszczędnościami.",
                                                              "Ligne orange = 4 semaines de dépenses couvertes par l'épargne."),
}
TR.update({k: v for k, v in NEW_TR.items() if k not in TR})


def N_(s):
    """Marks a string for translation (used in data tables); translate at display time with T()."""
    return s


def _load_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


if "tcache" not in ss:
    ss.tcache = _load_cache()


def lang_code():
    return LANGS.get(ss.lang, "en")


def T(en, **kw):
    """Translate a UI string, then fill in {placeholders}. Falls back to English if a translation is missing."""
    code, out = lang_code(), en
    if code != "en":
        row = TR.get(en)
        out = row[HAND_IDX[code]] if (code in HAND_IDX and row) else ss.tcache.get(code, {}).get(en, en)
    try:
        return out.format(**kw) if kw else out
    except Exception:
        return en.format(**kw) if kw else en


def gbp(x, d=0):
    return (("-£" if x < 0 else "£") + f"{abs(x):,.{d}f}")


def month_label(ts):
    return f"{T(ts.strftime('%B'))} {ts.year}"


def cover_phrase(weeks):
    """Weeks of cover in everyday words: 'about 4 days' / 'about 3.5 weeks'."""
    if weeks < 1:
        n = max(int(round(weeks * 7)), 0)
        return T("about 1 day") if n == 1 else T("about {n} days", n=n)
    n = round(weeks, 1)
    n = int(n) if n == int(n) else n
    return T("about 1 week") if n == 1 else T("about {n} weeks", n=n)


def safety_tip(p, weekly, weeks):
    """Plain-English safety-net status: what you have, what to aim for, how long it takes."""
    target = 4 * weekly
    need = target - p["buffer"]
    have = T("You have {buf} saved - enough to pay {cover} of your usual spending.", buf=gbp(p["buffer"]), cover=cover_phrase(weeks))
    if need <= 0:
        return have + " " + T("That already reaches a good first target of 4 weeks ({target}). Brilliant.", target=gbp(target))
    if p["goal"] <= 0:
        return have + " " + T("A good first target is 4 weeks of spending ({target}). Set a monthly savings goal to see when you could get there.", target=gbp(target))
    m = math.ceil(need / p["goal"])
    when = month_label(pd.Timestamp(TODAY) + pd.DateOffset(months=m))
    return have + " " + T("A good first target is 4 weeks of spending ({target}). Saving {goal} a month, you'd get there in about {m} months (around {when}).",
                          target=gbp(target), goal=gbp(p["goal"]), m=m, when=when)


# ---------------------------------------------------------------- Ollama (local AI) - plain HTTP, no extra package needed
def ollama_models():
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2) as r:
            data = json.load(r)
        return [m.get("name") or m.get("model") for m in data.get("models", [])], None
    except Exception as e:
        return [], str(e)


def pick_model(names):
    want = (ss.get("llm_model") or "").strip()
    if want:
        return want
    return next((n for n in names if n and n.startswith(LOCAL_MODEL)), names[0] if names else None)


def ollama_chat(messages, model, timeout=120):
    body = json.dumps({"model": model, "messages": messages, "stream": False, "options": {"temperature": 0.4}}).encode()
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["message"]["content"]


def ai_translate_all(code, bar):
    """One-off: translate every UI string into `code` with the local model, cache to disk."""
    names, err = ollama_models()
    model = pick_model(names)
    if not model:
        return T("Local AI isn't connected, so this language can't be built yet.")
    cache = ss.tcache.setdefault(code, {})
    todo = [k for k in TR if k not in cache]
    for i, k in enumerate(todo):
        prompt = (f"Translate this app text from English into {EN_NAME[code]}. Keep any {{placeholders}} such as {{n}} or {{amt}} exactly unchanged. "
                  "Keep numbers and the £ sign. Use short, natural, everyday words. Reply with ONLY the translation, nothing else.\n\n" + k)
        try:
            out = ollama_chat([{"role": "user", "content": prompt}], model, 60).strip().strip('"')
            if out and set(re.findall(r"\{\w+\}", out)) == set(re.findall(r"\{\w+\}", k)):
                cache[k] = out
        except Exception:
            pass
        bar.progress((i + 1) / max(len(todo), 1))
    try:
        CACHE_FILE.write_text(json.dumps(ss.tcache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return None



# ---------------------------------------------------------------- style
CSS = """
<style>
@import url('__IMPORT__');
@property --n{syntax:'<integer>';initial-value:0;inherits:false;}
.stApp,.stApp p,.stApp li,.stApp label,.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp td,.stApp th,.stApp button,
.stApp input,.stApp textarea,.stApp [data-baseweb="tab"],.stApp [data-testid="stMetricValue"],.stApp [data-testid="stMetricLabel"],
.stApp [data-testid="stCaptionContainer"],.card,.card *,.hero,.hero *,.natbar *,.greet,.sub,.tbox *,.pot *,.chip,.leg *{font-family:'__FONT__','Segoe UI',Arial,sans-serif !important;}
.stApp p,.stApp li,.stApp label,.stApp td,.stApp th{font-size:__FS__rem;}
.stApp h1,.stApp h2,.stApp h3{color:#3B0F5C;font-weight:800;}
.stApp [data-baseweb="tab-list"]{gap:6px;flex-wrap:wrap;}
.stApp [data-baseweb="tab"]{font-size:1.1rem;padding:10px 18px;}
.stApp [data-baseweb="tab"][aria-selected="true"]{color:#5A1E8C;font-weight:800;}
.stApp div[data-baseweb="tab-highlight"]{background-color:#C8102E;height:4px;}
.stApp .stButton>button{border-radius:999px;border:2px solid #5A1E8C;padding:.4rem 1.4rem;font-weight:600;transition:all .2s;}
.stApp .stButton>button:hover{background:#5A1E8C;color:#fff;transform:translateY(-2px);}
.stApp table{border-collapse:collapse;width:100%;}
.stApp th{background:#F3ECF9;color:#3B0F5C;}
.stApp td,.stApp th{border-bottom:1px solid #E3D5EE;padding:8px 10px;}
.block-container{max-width:1280px;padding:4.5rem 3rem 3rem;}
.stApp [data-testid="stMetricValue"],.stApp [data-testid="stMetricValue"]>div{font-size:1.9rem;white-space:normal !important;overflow:visible !important;text-overflow:clip !important;line-height:1.2;}
.stApp [data-testid="stMetricLabel"] *{white-space:normal !important;overflow:visible !important;}
@keyframes up{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes cnt{from{--n:0}to{--n:var(--v)}}
@keyframes grow{from{width:0}}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(200,16,46,.55)}50%{box-shadow:0 0 0 12px rgba(200,16,46,0)}}
[data-baseweb="tab-panel"]{animation:up .5s ease both;}
.natbar{display:flex;align-items:center;gap:22px;background:linear-gradient(90deg,#3B0F5C,#5A1E8C);border-bottom:5px solid #C8102E;border-radius:16px;padding:16px 26px;margin:0 0 16px;}
.natbar .logo{background:#fff;border-radius:12px;padding:8px 16px;display:flex;align-items:center;min-height:48px;}
.natbar .logo img{height:42px;max-width:200px;object-fit:contain;}
.natbar .logo span{font-size:1.6rem;font-weight:800;color:#5A1E8C !important;letter-spacing:-.5px;}
.natbar .brand{font-size:2.1rem;font-weight:800;color:#fff !important;line-height:1.25;}
.natbar .full{font-size:1.3rem;font-weight:600;color:#E6D5F2 !important;margin-left:8px;}
.natbar .tag{font-size:.95rem;color:#E6D5F2 !important;line-height:1.3;}
.seg{display:flex;height:44px;border-radius:12px;overflow:hidden;border:1px solid #E3D5EE;margin:8px 0 10px;}
.leg{display:flex;flex-wrap:wrap;gap:8px 22px;margin-bottom:10px;}
.leg div{display:flex;align-items:center;gap:8px;font-size:1rem;color:#2B1A3A;}
.leg i{display:inline-block;width:14px;height:14px;border-radius:4px;border:1px solid #cdb9de;}
.greet{font-size:2rem;font-weight:800;color:#3B0F5C;animation:up .6s ease both;}
.sub{font-size:1.15rem;color:#5A1E8C;margin-bottom:10px;line-height:1.5;}
.card{border-radius:14px;padding:14px 20px;margin:10px 0;border-left:8px solid;animation:up .6s ease both;transition:transform .2s,box-shadow .2s;font-size:1.1rem;line-height:1.5;}
.card .t{font-weight:800;font-size:1.2em;}
.card:hover,.tbox:hover{transform:translateY(-3px);box-shadow:0 8px 22px rgba(90,30,140,.2);}
.ok{background:#F3ECF9;border-color:#5A1E8C;color:#2B1A3A;}
.care{background:#fff;border:2px solid #C8102E;border-left:8px solid #C8102E;color:#2B1A3A;}
.stop{background:#C8102E;border-color:#7A0A1C;animation:up .6s ease both,pulse 2s 1s 2;}
.stop,.stop *{color:#fff !important;}
.hero{background:linear-gradient(135deg,#3B0F5C,#5A1E8C);border-radius:20px;padding:26px 32px;animation:up .6s ease both;}
.hero.short{background:linear-gradient(135deg,#7A0A1C,#C8102E);}
.hero,.hero *{color:#fff !important;}
.hero .big{font-size:4.2rem;font-weight:800;line-height:1.05;}
.hero small{font-size:1.6rem;}
.count{counter-reset:n var(--n);animation:cnt 1.3s ease-out forwards;}
.count::after{content:counter(n);}
.tbox{border-radius:16px;padding:16px 20px;margin:6px 0;animation:up .6s ease both;transition:transform .2s,box-shadow .2s;}
.tbox.a{background:#fff;border:2px solid #C8102E;}
.tbox.b{background:#F3ECF9;border:2px solid #5A1E8C;}
.tbox .lab{font-weight:800;font-size:1.05rem;}
.tbox .big2{font-size:2.8rem;font-weight:800;color:#3B0F5C;line-height:1.1;}
.tbox.a .big2{color:#C8102E;}
.tbox .rng{font-size:.95rem;color:#6b5a7a;}
.pot{height:26px;border-radius:14px;background:#E6D5F2;overflow:hidden;margin:6px 0 4px;}
.pot>div{height:100%;background:linear-gradient(90deg,#5A1E8C,#C8102E);animation:grow 1.2s ease-out;}
.chip{display:inline-block;background:#F3ECF9;border:1px solid #5A1E8C;border-radius:999px;padding:4px 14px;margin:3px 6px 3px 0;font-size:.95rem;animation:up .5s ease both;}
</style>
"""

CSS_CONTRAST = """
<style>
.stApp,.stApp p,.stApp li,.stApp label,.stApp td,.stApp th,.stApp span,.stApp h1,.stApp h2,.stApp h3{color:#000 !important;}
.stApp{background:#fff !important;}
.stApp th{background:#fff !important;border-bottom:3px solid #000 !important;}
.natbar{background:#000 !important;border-bottom:6px solid #C8102E !important;}
.card,.tbox,.chip{border:3px solid #000 !important;background:#fff !important;color:#000 !important;}
.card *,.tbox *{color:#000 !important;}
.stop{background:#7A0A1C !important;}
.stop,.stop *{color:#fff !important;}
.hero{background:#000 !important;border:3px solid #000;}
.hero.short{background:#7A0A1C !important;}
.hero,.hero *{color:#fff !important;}
.stApp .stButton>button{border:3px solid #000 !important;color:#000 !important;background:#fff !important;}
.stApp .stButton>button:hover{background:#000 !important;color:#fff !important;}
.stApp [data-baseweb="tab"][aria-selected="true"]{color:#000 !important;text-decoration:underline;}
</style>
"""

CSS_CALM = "<style>*{animation:none !important;transition:none !important;} .count{--n:var(--v) !important;}</style>"

# Colour-blind friendly: blue/orange instead of purple/red, plus a symbol on every status card so colour is never the only clue.
CSS_CB = """
<style>
.stApp div[data-baseweb="tab-highlight"]{background-color:#D55E00 !important;}
.natbar{border-bottom-color:#E69F00 !important;}
.card.ok{background:#E8F1F8 !important;border-color:#0072B2 !important;}
.card.care{border-color:#D55E00 !important;border-left-color:#D55E00 !important;}
.card.stop{background:#8A3A00 !important;border-color:#5C2700 !important;}
.hero.short{background:linear-gradient(135deg,#8A3A00,#D55E00) !important;}
.card.ok .t::before{content:"\\2714  ";}
.card.care .t::before{content:"\\26A0  ";}
.card.stop .t::before{content:"\\2716  ";}
</style>
"""

# Extra spacing between letters, words and lines (WCAG text-spacing; helps many people with dyslexia or visual stress).
CSS_SPACING = """
<style>
.stApp p,.stApp li,.stApp label,.stApp td,.stApp th,.card,.card *,.sub{line-height:1.9 !important;letter-spacing:.05em !important;word-spacing:.18em !important;}
.stApp p,.stApp li{margin-bottom:.9em !important;text-align:left !important;}
</style>
"""

# Bigger tap targets (tremor, motor difficulty, small screens) and a bold keyboard focus ring.
CSS_BIGTAP = """
<style>
.stApp .stButton>button{min-height:3.4rem;font-size:1.15rem;padding:.7rem 1.8rem;}
.stApp [data-baseweb="tab"]{min-height:3.4rem;padding:14px 24px;}
.stApp [data-testid="stCheckbox"] label,.stApp [data-testid="stToggle"] label,.stApp [data-testid="stRadio"] label{min-height:2.8rem;padding:6px 0;}
.stApp input,.stApp [data-baseweb="select"]>div{min-height:3rem;}
.stApp *:focus-visible{outline:4px solid #FFBF00 !important;outline-offset:3px !important;box-shadow:0 0 0 2px #000 !important;}
</style>
"""

# Reading highlight: the text under the pointer is highlighted so you don't lose your place.
CSS_HL = """
<style>
.stApp p:hover,.stApp li:hover,.stApp td:hover,.stApp th:hover,.card:not(.stop):hover{background-color:#FFF3A3 !important;}
</style>
"""


def font_name():
    return "Atkinson Hyperlegible" if ss.get("easy") else "Nunito"


def inject_css():
    fam = font_name()
    css = (CSS.replace("__FS__", "1.3" if ss.get("large") else "1.05").replace("__FONT__", fam)
           .replace("__IMPORT__", "https://fonts.googleapis.com/css2?family=" + fam.replace(" ", "+") + ":wght@400;600;700;800&display=swap"))
    if ss.get("contrast"):
        css += CSS_CONTRAST
    if ss.get("calm"):
        css += CSS_CALM
    if ss.get("cb"):
        css += CSS_CB
    if ss.get("spacing"):
        css += CSS_SPACING
    if ss.get("bigtap"):
        css += CSS_BIGTAP
    if ss.get("hl"):
        css += CSS_HL
    if lang_code() in RTL:
        css += "<style>.stApp .block-container,.stApp [data-testid='stSidebar']{direction:rtl;text-align:right;}</style>"
    st.markdown(css, unsafe_allow_html=True)


def header():
    """Top banner. Save the official logo as assets/natwest_logo.png (or .svg/.jpg) next to app.py."""
    logo = "<span>NatWest</span>"
    for ext, mime in (("png", "image/png"), ("jpg", "image/jpeg"), ("jpeg", "image/jpeg"), ("svg", "image/svg+xml")):
        pth = HERE / "assets" / f"natwest_logo.{ext}"
        if pth.exists():
            logo = f"<img alt='NatWest' src='data:{mime};base64,{base64.b64encode(pth.read_bytes()).decode()}'/>"
            break
    st.markdown(f"<div class='natbar'><div class='logo'>{logo}</div><div><div class='brand'>{APP_NAME} <span class='full'>{FULL_NAME}</span></div>"
                f"<div class='tag'>{T('Your private financial time machine')}</div></div></div>", unsafe_allow_html=True)


def card(kind, title, body=""):
    st.markdown(f"<div class='card {kind}'><div class='t'>{title}</div>{body}</div>", unsafe_allow_html=True)


def fmt(text):
    """Safe HTML for coach answers: escape, **bold**, line breaks."""
    t_ = htmllib.escape(str(text))
    t_ = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t_)
    return t_.replace("\n", "<br>")


def style_chart(ch, h=300):
    f = font_name()
    return ch.properties(width="container", height=h).configure(font=f).configure_view(strokeWidth=0)\
        .configure_axis(labelFont=f, titleFont=f, labelColor="#2B1A3A", titleColor=DARK).configure_legend(labelFont=f, titleFont=f)


def greeting():
    hr = datetime.now().hour
    return T("Good morning") if hr < 12 else T("Good afternoon") if hr < 18 else T("Good evening")


INTRO_CSS = """
<style>
@keyframes iFade{0%,90%{opacity:1;visibility:visible}100%{opacity:0;visibility:hidden}}
@keyframes iRoll{0%{left:-110px}100%{left:calc(50% - 36px)}}
@keyframes iSpin{0%{transform:rotate(0)}100%{transform:rotate(1000deg)}}
@keyframes iDrop{0%{transform:translateY(0) scaleX(1);opacity:1}70%{opacity:1}100%{transform:translateY(125px) scaleX(.18);opacity:0}}
@keyframes iBounce{0%,100%{transform:scale(1)}40%{transform:scale(1.06,.93)}70%{transform:scale(.98,1.03)}}
@keyframes iIn{0%{opacity:0;transform:translateY(18px)}100%{opacity:1;transform:none}}
.intro{position:fixed;inset:0;z-index:2147483000;background:radial-gradient(circle at 50% 40%,#5A1E8C,#2a0842);display:flex;align-items:center;justify-content:center;animation:iFade __DUR__s ease forwards;}
.iwrap{width:min(720px,92vw);text-align:center;}
.itext{height:120px;opacity:0;animation:iIn .8s ease __TXT__s forwards;}
.itext .g{font-size:3rem;font-weight:800;color:#fff;line-height:1.2;font-family:'Nunito',Arial,sans-serif;}
.itext .s{font-size:1.2rem;color:#E6D5F2;margin-top:6px;font-family:'Nunito',Arial,sans-serif;}
.stage{position:relative;width:100%;height:300px;}
.shelf{position:absolute;left:0;top:170px;width:calc(50% - 110px);height:6px;border-radius:3px;background:#E6D5F2;opacity:.8;}
.cw{position:absolute;top:98px;width:72px;height:72px;left:-110px;animation:iRoll 1.7s cubic-bezier(.3,.6,.4,1) .3s forwards;}
.cf{width:72px;height:72px;animation:iDrop .5s ease-in 2.05s forwards;}
.cn{width:72px;height:72px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#f2b184,#b87333 60%,#8a4f1f);border:5px solid #d99562;box-sizing:border-box;
display:flex;align-items:center;justify-content:center;color:#5c3010;font-weight:800;font-size:1.3rem;font-family:'Nunito',Arial,sans-serif;animation:iSpin 1.7s cubic-bezier(.3,.6,.4,1) .3s forwards;box-shadow:inset 0 0 8px rgba(0,0,0,.25);}
.mbox{position:absolute;left:calc(50% - 110px);top:170px;width:220px;height:130px;border-radius:18px 18px 26px 26px;background:linear-gradient(180deg,#8a4cc0,#5A1E8C);
box-shadow:0 14px 30px rgba(0,0,0,.35);z-index:3;animation:iBounce .45s ease 2.5s;}
.mbox .slot{position:absolute;left:50%;margin-left:-48px;top:-5px;width:96px;height:12px;border-radius:7px;background:#1c0630;box-shadow:0 0 0 3px #a874d6;}
.mbox .lab{position:absolute;bottom:22px;width:100%;text-align:center;color:#fff;font-weight:800;font-size:2rem;letter-spacing:2px;font-family:'Nunito',Arial,sans-serif;}
.hellos{color:#cdb3e6;font-size:1rem;margin-top:14px;font-family:'Nunito',Arial,sans-serif;opacity:0;animation:iIn .8s ease __TXT__s forwards;}
</style>
"""


def intro(title, sub, quick=False):
    """Penny rolls into a money-box slot, then a greeting appears, then the app fades in. Skipped in calm (reduce-motion) mode."""
    if ss.get("calm"):
        return
    dur, txt = (4.6, 1.3) if quick else (5.8, 2.6)
    css = INTRO_CSS.replace("__DUR__", str(dur)).replace("__TXT__", str(txt))
    if quick:
        css += "<style>.cw{animation-delay:.1s !important}.cn{animation-delay:.1s !important}.cf{animation-delay:1.5s !important}.mbox{animation-delay:1.95s !important}</style>"
    body = ("<div class='intro' aria-hidden='true'><div class='iwrap'>"
            f"<div class='itext'><div class='g'>{htmllib.escape(title)}</div><div class='s'>{htmllib.escape(sub)}</div></div>"
            "<div class='stage'><div class='shelf'></div><div class='cw'><div class='cf'><div class='cn'>1p</div></div></div>"
            "<div class='mbox'><div class='slot'></div><div class='lab'>NWC</div></div></div>"
            "<div class='hellos'>Hello · Hola · Cześć · Bonjour · Helo · مرحبا · হ্যালো · ਸਤ ਸ੍ਰੀ ਅਕਾਲ · Bună · Olá · 你好 · नमस्ते</div></div></div>")
    st.markdown(css + body, unsafe_allow_html=True)


# ---------------------------------------------------------------- rules (ILLUSTRATIVE figures)
CATEGORY_RULES = [
    ("Overdraft fees", ["overdraft fee", "od fee", "unarranged"]),
    ("Late fees", ["late fee", "late payment", "missed payment"]),
    ("Cash-machine fees", ["atm fee", "cash machine fee", "cash withdrawal fee"]),
    ("High-cost credit charges", ["payday", "provident", "loan interest", "interest charged"]),
    ("Rent", ["rent", "landlord"]),
    ("Energy (top-up meter)", ["meter top", "prepay energy", "key top"]),
    ("Insurance (monthly)", ["insurance"]),
    ("Subscriptions", ["netflix", "spotify", "gym", "prime", "mobile contract"]),
    ("Convenience-store shopping", ["spar", "londis", "costcutter", "one stop"]),
    ("Groceries", ["tesco", "aldi", "lidl", "asda", "sainsbury", "co-op"]),
    ("Transport", ["tfl", "bus", "train", "fuel", "petrol"]),
    ("Eating out", ["greggs", "mcdonald", "deliveroo", "uber eats", "cafe"]),
]
FIXED_CATS = ["Rent", "Energy (top-up meter)", "Insurance (monthly)", "Subscriptions"]
VARIABLE_CATS = ["Groceries", "Transport", "Eating out", "Convenience-store shopping", "Other"]
FEE_CATS = ["Overdraft fees", "Late fees", "Cash-machine fees", "High-cost credit charges"]
PREMIUMS = {
    "Overdraft fees": dict(kind="fee", why=N_("Charged when your balance dips below zero - and dips are likelier when income is irregular."),
                           free=N_("Ask the bank to refund avoidable fees or agree an arranged limit."), ext=N_("Free debt advice: MoneyHelper, StepChange, Citizens Advice."),
                           msg=N_("Hello, I was charged overdraft fees. Can you review them, refund any that were avoidable, and explain fee-free options?")),
    "Late fees": dict(kind="fee", why=N_("Penalties for paying after the due date, often because pay arrives after bills."),
                      free=N_("Ask to move due dates to just after payday."), ext=N_("Free debt advice: MoneyHelper, StepChange, Citizens Advice."),
                      msg=N_("Hello, I was charged a late fee. Could you waive it and move my due date to just after payday?")),
    "Cash-machine fees": dict(kind="fee", why=N_("Some machines charge to withdraw your own money - common where free machines are scarce."),
                              free=N_("Use fee-free machines or cashback at a shop till."), ext=N_("Ask your bank whether a free cash-access option exists."),
                              msg=N_("Hello, I keep paying cash-machine fees. Where can I withdraw free, and can past fees be refunded?")),
    "High-cost credit charges": dict(kind="fee", why=N_("Short-term credit can cost many times more than ordinary borrowing."),
                                     free=N_("Ask the lender about a repayment plan."), ext=N_("Check for a local credit union loan and free debt advice."),
                                     msg=N_("Hello, I'm finding repayments hard. Can we agree an affordable plan and pause extra charges?")),
    "Energy (top-up meter)": dict(kind="markup", rate=0.10, why=N_("Pay-as-you-go meters can cost more per unit than a direct-debit tariff, which needs credit checks or a steady account."),
                                  free=N_("Ask your supplier about changing payment method or a hardship tariff."), ext=N_("Check energy bill support and local hardship funds you may be eligible for."),
                                  msg=N_("Hello, I pay by top-up meter. Can I move to a cheaper payment method, and is any hardship support or social tariff available to me?")),
    "Insurance (monthly)": dict(kind="markup", rate=0.08, why=N_("Paying monthly usually includes interest compared with paying the year upfront - which needs cash you may not have."),
                                free=N_("Ask for a discount or compare quotes."), ext=N_("A credit union may help spread a yearly payment at lower interest."),
                                msg=N_("Hello, I pay insurance monthly. What is the total cost monthly versus yearly, and is any discount or help spreading the yearly cost possible?")),
    "Convenience-store shopping": dict(kind="markup", rate=0.20, why=N_("Small local shops can cost more than supermarkets - often the only option without transport or time."),
                                       free=N_("Plan one bigger shop with a bus or delivery slot for the staples."), ext=N_("Ask about community food schemes or discount stores nearby."),
                                       msg=N_("(No message needed - check prices of your five most-bought items at a discount supermarket.)")),
}


# ---------------------------------------------------------------- data
def norm_key(d):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", str(d).lower())).strip()


def categorise(desc, amount):
    """AI HOOK: swap for a local-LLM classifier for messy descriptions."""
    if amount > 0:
        return "Income"
    d = str(desc).lower()
    for cat, kws in CATEGORY_RULES:
        if any(re.search(r"\b" + re.escape(k), d) for k in kws):
            return cat
    return "Other"


def prepare(df):
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = pd.to_numeric(df["amount"])
    df["category"] = [categorise(d, a) for d, a in zip(df["description"], df["amount"])]
    df["month"] = df["date"].dt.to_period("M")
    return df.sort_values("date").reset_index(drop=True)


def make_demo_data(today):
    """Synthetic persona 'Maya': irregular income, top-up meter, monthly insurance, occasional fees."""
    rng = np.random.default_rng(7)
    rows = []
    this_m = pd.Timestamp(today).to_period("M")
    for m in pd.period_range(this_m - 5, this_m, freq="M"):
        start = m.to_timestamp()

        def d(day):
            return start + pd.Timedelta(days=min(day, m.days_in_month) - 1)

        for day in sorted(rng.choice(range(1, 29), size=int(rng.integers(1, 5)), replace=False)):
            rows.append((d(day), "AGENCY SHIFT PAY", float(rng.integers(330, 470))))
        rows.append((d(1), "RENT LANDLORD", -480.0))
        for day in (3, 10, 17, 24):
            rows.append((d(day), "PREPAY ENERGY METER TOP UP", -30.0))
        rows.append((d(12), "CAR INSURANCE MONTHLY", -38.0))
        rows += [(d(5), "NETFLIX", -10.99), (d(8), "SPOTIFY", -11.99), (d(20), "MOBILE CONTRACT", -14.0)]
        for day in rng.choice(range(1, 29), size=7):
            rows.append((d(day), f"TESCO STORES {rng.integers(1000, 9999)}", -round(float(rng.uniform(15, 45)), 2)))
        for day in rng.choice(range(1, 29), size=4):
            rows.append((d(day), "SPAR LOCAL", -round(float(rng.uniform(6, 14)), 2)))
        for day in rng.choice(range(1, 29), size=6):
            rows.append((d(day), "TFL TRAVEL", -round(float(rng.uniform(2.5, 12)), 2)))
        for day in rng.choice(range(1, 29), size=3):
            rows.append((d(day), "GREGGS", -round(float(rng.uniform(3, 9)), 2)))
        if rng.random() < 0.6:
            rows.append((d(27), "OD FEE", -8.0))
        if rng.random() < 0.3:
            rows.append((d(15), "KLARNA LATE FEE", -6.0))
        if rng.random() < 0.4:
            rows.append((d(9), "ATM FEE", -1.99))
        if rng.random() < 0.2:
            rows.append((d(20), "PAYDAY LOAN INTEREST", -22.0))
    df = pd.DataFrame(rows, columns=["date", "description", "amount"])
    return df[df["date"] <= pd.Timestamp(today)]


# ---------------------------------------------------------------- money maths (deterministic - no AI)
def loan_interest(p, apr, n):
    if p <= 0:
        return 0.0, 0.0
    r = apr / 1200
    if r == 0:
        return p / n, 0.0
    pay = p * r / (1 - (1 + r) ** -n)
    return pay, pay * n - p


def monthly_table(full):
    g = full.groupby("month")["amount"]
    t_ = pd.DataFrame({"Income": g.apply(lambda s: s[s > 0].sum()), "Spending": g.apply(lambda s: -s[s < 0].sum())})
    t_["Net"] = t_["Income"] - t_["Spending"]
    return t_


def find_premiums(full):
    n = max(full["month"].nunique(), 1)
    rows = []
    for cat, cfg in PREMIUMS.items():
        spend = -full[(full.category == cat) & (full.amount < 0)].amount.sum()
        if spend <= 0:
            continue
        prem = spend if cfg["kind"] == "fee" else spend * cfg["rate"] / (1 + cfg["rate"])
        basis = "actual" if cfg["kind"] == "fee" else f"assumed:{int(cfg['rate']*100)}"
        rows.append({"Category": cat, "Spend per year": spend / n * 12, "Extra cost per year": prem / n * 12, "Basis": basis})
    return pd.DataFrame(rows, columns=["Category", "Spend per year", "Extra cost per year", "Basis"])


def recurring_fixed(full):
    fx = full[full.category.isin(FIXED_CATS)].copy()
    fx["key"] = fx["description"].map(norm_key)
    out = []
    for key, g in fx.groupby("key"):
        if g["month"].nunique() >= 3:
            out.append({"key": key, "typical_count": float(g.groupby("month").size().median()), "amount": float(-g["amount"].median())})
    return pd.DataFrame(out, columns=["key", "typical_count", "amount"])


def budget_snapshot(df, today, goal, balance, ov_income=None, ov_bills=None):
    cur = pd.Timestamp(today).to_period("M")
    full, now = df[df.month < cur], df[df.month == cur]
    if full["month"].nunique() < 2:
        return None  # STOP CONDITION: not enough history
    inc_m = full[full.amount > 0].groupby("month").amount.sum()
    conservative = float(inc_m.quantile(0.25))
    so_far = float(now[now.amount > 0].amount.sum())
    expected = max(so_far, conservative)
    spent = float(-now[now.amount < 0].amount.sum())
    rec = recurring_fixed(full)
    counts = now.assign(key=now["description"].map(norm_key)).groupby("key").size()
    upcoming = sum(max(r.typical_count - counts.get(r.key, 0), 0) * r.amount for _, r in rec.iterrows())
    if ov_income is not None:
        expected = ov_income
    if ov_bills is not None:
        upcoming = ov_bills
    var_m = full[full.category.isin(VARIABLE_CATS)].groupby("month").amount.sum().abs()
    var_to_come = max(float(var_m.median()) - float(-now[now.category.isin(VARIABLE_CATS)].amount.sum()), 0.0)
    left = expected - spent - upcoming - goal
    return dict(expected_income=expected, income_so_far=so_far, conservative=conservative, spent_so_far=spent, upcoming=upcoming,
                var_to_come=var_to_come, savings_goal=goal, left_after_bills=left, free=left - var_to_come,
                days_left=pd.Timestamp(today).days_in_month - today.day + 1, balance=balance)


def simulate(monthly, start, years, capture, prem_annual, rate, drop, xmas, rent_rise, n=600, seed=11):
    """Bootstrap simulation: re-shuffle the user's own past months to get a RANGE of futures, not one line."""
    rng = np.random.default_rng(seed)
    inc, sp = monthly["Income"].to_numpy(), monthly["Spending"].to_numpy()
    M = years * 12
    idx = rng.integers(0, len(inc), size=(n, M))
    out = {}
    for name, cap in (("nothing", 0.0), ("act", capture)):
        bal, path = np.full(n, float(start)), []
        path.append(bal.copy())
        for m in range(1, M + 1):
            cal = (TODAY.month + m - 1) % 12 + 1
            net = inc[idx[:, m - 1]] * (1 - drop) - sp[idx[:, m - 1]] + prem_annual * cap / 12
            if cal == 12:
                net = net - xmas
            if m > 12:
                net = net - rent_rise
            bal = bal * (1 + rate / 12) + net
            path.append(bal.copy())
        out[name] = np.percentile(np.array(path), [10, 50, 90], axis=1)
    return out


CAT_NAMES = [N_("Overdraft fees"), N_("Late fees"), N_("Cash-machine fees"), N_("High-cost credit charges"), N_("Rent"), N_("Energy (top-up meter)"),
             N_("Insurance (monthly)"), N_("Subscriptions"), N_("Convenience-store shopping"), N_("Groceries"), N_("Transport"), N_("Eating out"), N_("Other"),
             N_("January"), N_("February"), N_("March"), N_("April"), N_("May"), N_("June"), N_("July"), N_("August"), N_("September"), N_("October"),
             N_("November"), N_("December")]
FOCUS_OPTS = [N_("Building a safety net"), N_("Getting rent covered"), N_("Cutting extra costs"), N_("Saving for something big")]
HORIZONS = [N_("Today"), N_("Next month"), N_("Christmas"), N_("6 months"), N_("1 year"), N_("3 years"), N_("5 years")]


def basis_txt(b):
    if b == "actual":
        return T("Actual charges")
    return T("Assumed {pct}% extra (illustrative estimate)", pct=b.split(":")[1])


def purchase_verdict(price, essential, s):
    cash = s["balance"] - s["upcoming"]
    if price > cash:
        return "stop", T("Not this time"), [T("Your balance ({bal}) minus bills still due ({bills}) leaves {cash} - less than {price}.",
                                              bal=gbp(s["balance"]), bills=gbp(s["upcoming"]), cash=gbp(cash), price=gbp(price))]
    if essential:
        if price <= s["left_after_bills"]:
            return "ok", T("Yes - and it's covered"), [T("It fits inside the {amt} left after bills and your savings goal.", amt=gbp(s["left_after_bills"]))]
        if price <= s["left_after_bills"] + s["savings_goal"]:
            return "care", T("Yes, but it uses part of your savings goal"), [T("Essentials come first - you may save less this month.")]
        return "stop", T("Not covered this month"), [T("See Extra costs for help and payment plans.")]
    if price <= s["free"]:
        return "ok", T("Yes, this fits your plan"), [T("You have about {amt} truly free after bills, savings and everyday costs.", amt=gbp(s["free"]))]
    if price <= s["left_after_bills"]:
        return "care", T("You can, with care"), [T("It uses money you usually need for food and travel ({amt} still expected).", amt=gbp(s["var_to_come"]))]
    if price <= s["left_after_bills"] + s["savings_goal"]:
        return "care", T("It would eat into your savings goal"), [T("Waiting until next month would keep your goal intact.")]
    return "stop", T("Not this month"), [T("It is {amt} more than you have left after bills and your goal.", amt=gbp(price - s["left_after_bills"]))]


def df_now_rent_missing(df):
    cur = pd.Timestamp(TODAY).to_period("M")
    return df[(df.month == cur) & (df.category == "Rent")].empty


def heads_up(snap, monthly, full, xmas):
    """Proactive warnings - the 'before it happens' part of the brief."""
    out = []
    cash = snap["balance"] - snap["upcoming"]
    if cash < 0:
        out.append(("stop", T("You may be {amt} short for bills still due this month", amt=gbp(-cash))))
    rent = full[full.category == "Rent"]
    if not rent.empty:
        day, amt = int(rent.date.dt.day.median()), float(-rent.amount.median())
        due = date(TODAY.year, TODAY.month, min(day, 28))
        if (not df_now_rent_missing(ss.df)) or due < TODAY:
            due = date(TODAY.year + (TODAY.month == 12), TODAY.month % 12 + 1, min(day, 28))
        dd = (due - TODAY).days
        if dd <= 14:
            out.append(("stop" if snap["balance"] < amt else "care",
                        T("Rent ({amt}) is due in {d} days and your balance is {bal}", amt=gbp(amt), d=dd, bal=gbp(snap["balance"]))))
    xd = (date(TODAY.year, 12, 25) - TODAY).days
    if 0 < xd <= 80 and xmas > 0:
        w = max(xd // 7, 1)
        out.append(("care", T("Christmas is {w} weeks away. Setting aside about {amt} a week stops December tipping you into fees", w=w, amt=gbp(xmas / w))))
    short = int((monthly["Net"] < 0).sum())
    if short >= 1 and short / len(monthly) >= 0.3:
        msg = T("{short} of your last {n} months ended short.", short=short, n=len(monthly))
        od = full[full.category == "Overdraft fees"]
        if not od.empty:
            msg += " " + T("Overdraft fees have usually landed around day {d}.", d=int(od.date.dt.day.median()))
        out.append(("care", msg))
    if snap["free"] < 0:
        out.append(("care", T("Your savings goal doesn't fit this month's pattern - try a smaller goal")))
    return out


def resilience(monthly, buffer):
    weekly = monthly["Spending"].mean() * 12 / 52
    weeks = buffer / weekly if weekly else 0
    vol = monthly["Income"].std() / monthly["Income"].mean() if monthly["Income"].mean() else 1
    score = 60 * min(weeks / 12, 1) + 20 * (1 - min(vol, 1)) + 20 * float((monthly["Net"] > 0).mean())
    return int(round(score)), weeks, weekly


PRESET_QS = [
    N_("Why am I short before payday?"),
    N_("What is my biggest extra cost?"),
    N_("Can I afford a £40 treat?"),
    N_("How much can I spend each day this month?"),
    N_("How long would my savings last?"),
    N_("Is my savings goal realistic?"),
    N_("How much should I have saved for emergencies?"),
    N_("Where can I get free help with debt?"),
    N_("How do I stop being charged overdraft fees?"),
    N_("Is my data safe with this app?"),
]


def preset_answer(q, c):
    """ai.nwc answers. Every figure comes from the app's own plain Python numbers; the answers are pre-written, nothing is generated."""
    snap, monthly, prem, p = c["snap"], c["monthly"], c["prem"], c["p"]
    weeks, weekly = c["weeks"], c["weekly"]
    if q == PRESET_QS[0]:
        n = int((monthly["Net"] < 0).sum())
        vol = monthly["Income"].std() / monthly["Income"].mean()
        return T("{n} of your last {m} months ended short. Your income moves by about {vol}% from month to month, but rent, energy and insurance stay the same - "
                 "so a quieter month squeezes you before payday. A small buffer (see Stress test) is what smooths this.", n=n, m=len(monthly), vol=f"{vol*100:.0f}")
    if q == PRESET_QS[1]:
        if prem.empty:
            return T("I can't see any extra-cost patterns in your history.")
        top = prem.sort_values("Extra cost per year", ascending=False).iloc[0]
        return T("Your biggest extra cost is **{cat}**, about {amt} a year in total ({basis}). Open the Extra costs tab for free fixes first.",
                 cat=T(top.Category), amt=gbp(top["Extra cost per year"]), basis=basis_txt(top.Basis))
    if q == PRESET_QS[2]:
        kind, head, why = purchase_verdict(40.0, False, snap)
        return T("For {amt}: **{head}.** {why}", amt=gbp(40.0), head=head, why=why[0])
    if q == PRESET_QS[3]:
        per_day = snap["free"] / snap["days_left"]
        if snap["free"] >= 0:
            return T("You have {amt} a day to spend this month.", amt=gbp(per_day, 2))
        return T("You are {amt} short this month.", amt=gbp(-per_day * snap["days_left"], 2))
    if q == PRESET_QS[4]:
        return safety_tip(p, weekly, weeks)
    if q == PRESET_QS[5]:
        fits = T("fits") if snap["free"] >= 0 else T("does not fit yet - a smaller goal would be safer")
        return T("Your goal is {goal} this month. After bills and everyday costs you have {free} free, so it {fits}.", goal=gbp(snap["savings_goal"]), free=gbp(snap["free"]), fits=fits)
    if q == PRESET_QS[6]:
        return T("A good first target is 4 weeks of your usual spending, which for you is {target}. You have {buf} saved. Over time, aim for 12 weeks, built up a little at a time.",
                 target=gbp(4 * weekly), buf=gbp(p["buffer"]))
    if q == PRESET_QS[7]:
        return T("Free, independent help is available: MoneyHelper, StepChange and Citizens Advice can all talk through debt with you at no cost. "
                 "Speak to them before taking on more credit, and be wary of anyone who charges an upfront fee to 'fix' your debt.")
    if q == PRESET_QS[8]:
        return T(PREMIUMS["Overdraft fees"]["free"]) + " " + T("You can also keep your balance above zero by asking to move bill due dates to just after payday. A ready-made message is in the Extra costs tab.")
    if q == PRESET_QS[9]:
        return T("You never type a bank password here. The real version would use read-only Open Banking consent that you can withdraw at any time, and your data stays on this device. "
                 "This app gives guidance only, not regulated financial advice.")
    return ""


# ---------------------------------------------------------------- sidebar shared by every screen
def sidebar_common():
    with st.sidebar:
        st.selectbox("🌐 Language / Idioma / भाषा / 语言", list(LANGS), key="lang")
        code = lang_code()
        if code != "en" and code not in HAND_IDX and len(ss.tcache.get(code, {})) < len(TR) * 0.9:
            st.info("This language is built on your device by the local AI model (one-off, takes a few minutes, then saved). Until then the app shows English.")
            if st.button("Translate the app into this language"):
                bar = st.progress(0)
                err = ai_translate_all(code, bar)
                if err:
                    st.error(err)
                else:
                    st.rerun()
        st.markdown("**" + T("Accessibility") + "**")
        st.toggle(T("Larger text"), key="large")
        st.toggle(T("High contrast"), key="contrast")
        st.toggle(T("Easy-read font"), key="easy", help=T("Uses Atkinson Hyperlegible, designed for low vision and dyslexia."))
        st.toggle(T("Calm mode (no animations)"), key="calm")
        st.toggle(T("Colour-blind friendly colours"), key="cb", help=T("Swaps red and purple for blue and orange, and adds a symbol to every status card so you never rely on colour alone."))
        st.toggle(T("Extra text spacing"), key="spacing", help=T("Wider gaps between letters, words and lines. Can make reading easier, for example with dyslexia."))
        st.toggle(T("Bigger buttons and clear focus"), key="bigtap", help=T("Larger tap targets, plus a bold outline that shows where you are when using a keyboard."))
        st.toggle(T("Highlight the line I'm reading"), key="hl", help=T("Highlights the text under your pointer so you don't lose your place."))


# ---------------------------------------------------------------- onboarding
def onboarding():
    header()
    st.caption(T("See the money problems coming - and the cheapest way out. Runs on your device."))
    names = [T("1. Connect bank"), T("2. Savings goal"), T("3. Past transactions")]
    i = {"connect": 0, "goal": 1, "data": 2}[ss.stage]
    st.progress((i + 1) / 3, text=names[i])
    p = ss.profile
    if ss.stage == "connect":
        st.subheader(T("Connect your bank (simulated)"))
        card("ok", T("You never type a bank password here"),
             T("In the real version you approve <b>read-only</b> access on your bank's own screen (Open Banking) and can revoke it any time. Nothing is sold or shared."))
        p["name"] = st.text_input(T("What should we call you?"), value=p["name"], key="ob_name")
        p["focus"] = st.selectbox(T("What matters most to you right now?"), FOCUS_OPTS, index=FOCUS_OPTS.index(p["focus"]) if p["focus"] in FOCUS_OPTS else 0,
                                  format_func=T, key="ob_focus")
        consent = st.checkbox(T("I agree to share my transaction history (read-only) with this app, on this device"), key="ob_consent")
        p["balance"] = st.number_input(T("Current account balance (£)"), value=p["balance"], step=10.0, key="ob_bal")
        p["buffer"] = st.number_input(T("Savings you could use in an emergency (£)"), value=p["buffer"], min_value=0.0, step=10.0, key="ob_buf")
        if st.button(T("Connect (demo)"), type="primary", disabled=not consent):
            ss.stage = "goal"
            st.rerun()
    elif ss.stage == "goal":
        st.subheader(T("How much do you want to save this month?"))
        p["goal"] = st.number_input(T("Monthly savings goal (£)"), value=p["goal"], min_value=0.0, step=5.0, key="ob_goal")
        st.caption(T("Be realistic - the app will tell you honestly if it fits your income pattern."))
        if st.button(T("Next"), type="primary"):
            ss.stage = "data"
            st.rerun()
    else:
        st.subheader(T("Load your past transactions"))
        st.caption(T("Real version: pulled automatically after consent. Demo: sample persona, or a CSV with columns `date, description, amount` (spending negative)."))
        choice = st.radio(T("Source"), ["sample", "upload"], key="ob_src",
                          format_func=lambda k: T("Sample persona (Maya, irregular income)") if k == "sample" else T("Upload CSV"))
        if choice == "sample":
            if st.button(T("Load sample data"), type="primary"):
                ss.df = prepare(make_demo_data(TODAY))
                ss.stage = "app"
                st.rerun()
        else:
            f = st.file_uploader(T("CSV file"), type="csv")
            if f is not None:
                try:
                    raw = pd.read_csv(f)
                    assert {"date", "description", "amount"} <= set(raw.columns)
                    ss.df = prepare(raw)
                    ss.stage = "app"
                    st.rerun()
                except Exception:
                    st.error(T("Couldn't read that file. It needs columns: date, description, amount."))


# ---------------------------------------------------------------- main app
def main_app():
    df, p = ss.df, ss.profile
    cb = bool(ss.get("cb"))
    cr, cp = (CB_ORANGE, CB_BLUE) if cb else (RED, PURPLE)
    wr, wp = (T("Orange"), T("Blue")) if cb else (T("Red"), T("Purple"))
    with st.sidebar:
        st.divider()
        st.header(T("Settings"))
        p["name"] = st.text_input(T("Your name"), value=p.get("name", ""), key="sb_name")
        p["balance"] = st.number_input(T("Account balance (£)"), value=p["balance"], step=10.0, key="sb_bal")
        p["buffer"] = st.number_input(T("Emergency savings (£)"), value=p["buffer"], min_value=0.0, step=10.0, key="sb_buf")
        p["goal"] = st.number_input(T("Savings goal this month (£)"), value=p["goal"], min_value=0.0, step=5.0, key="sb_goal")
        st.divider()
        st.caption(T("Data stays on this device. Guidance only - not regulated financial advice."))
        if st.button(T("Start again")):
            for k in ("stage", "df", "chat", "intro2"):
                ss.pop(k, None)
            st.rerun()
    header()
    if not ss.intro2:
        nm0 = p.get("name", "").strip()
        intro(f"{greeting()}{', ' + nm0 if nm0 else ''}", T("Let's look at your money together."), quick=True)
        ss.intro2 = True

    cur = pd.Timestamp(TODAY).to_period("M")
    full = df[df.month < cur]
    ov_i = ss.get("ov_income") if ss.get("ov_on") else None
    ov_b = ss.get("ov_bills") if ss.get("ov_on") else None
    snap = budget_snapshot(df, TODAY, p["goal"], p["balance"], ov_i, ov_b)
    if snap is None:
        st.warning(T("Not enough history yet (need at least 2 full months). I won't guess - upload more transactions."))
        return
    monthly = monthly_table(full)
    prem_all = find_premiums(full)
    prem = prem_all[~prem_all.Category.map(lambda c: bool(ss.get(f"delib_{c}", False)))] if not prem_all.empty else prem_all
    total_prem = float(prem["Extra cost per year"].sum()) if not prem.empty else 0.0
    avg_net = float(monthly["Net"].mean())
    score, weeks, weekly = resilience(monthly, p["buffer"])
    xmas = float(ss.get("xmas", 150))
    month_now = month_label(pd.Timestamp(TODAY))

    tabs = st.tabs([T("Home"), T("This month"), T("Future"), T("Can I buy it?"), T("Extra costs"), T("Stress test"), T("Ask")])

    # ---- HOME
    with tabs[0]:
        raw_name = p.get("name", "").strip()
        nm = htmllib.escape(raw_name)
        greet = greeting()
        rent_rows = full[full.category == "Rent"]
        rent_amt = float(-rent_rows.amount.median()) if not rent_rows.empty else 0.0
        tips = {"Building a safety net": safety_tip(p, weekly, weeks),
                "Getting rent covered": T("Rent is {amt}. Treat it as the first bill you ring-fence each month.", amt=gbp(rent_amt)),
                "Cutting extra costs": T("About {amt} a year is up for grabs - open the Extra costs tab for a free way to fix each one.", amt=gbp(total_prem)),
                "Saving for something big": T("At {goal} a month you'd save {year} in a year.", goal=gbp(p["goal"]), year=gbp(p["goal"] * 12))}
        focus_tip = tips.get(p.get("focus"), "")
        st.markdown(f"<div class='greet'>{greet}{', ' + nm if nm else ''} 👋</div><div class='sub'>{T('Your focus')}: <b>{T(p.get('focus', ''))}</b><br>{focus_tip}</div>", unsafe_allow_html=True)

        per_day = snap["free"] / snap["days_left"]
        val = per_day if snap["free"] >= 0 else -snap["free"]
        pounds, pence = divmod(int(round(val * 100)), 100)
        cls, lab = ("hero", T("a day to spend")) if snap["free"] >= 0 else ("hero short", T("short this month"))
        st.markdown(f"<div class='{cls}'><div>{T(pd.Timestamp(TODAY).strftime('%B'))}</div><div class='big'>£<span class='count' style='--v:{pounds}'></span><small>.{pence:02d}</small></div><div>{lab}</div></div>", unsafe_allow_html=True)
        hero_say = T("You have {amt} a day to spend this month.", amt=gbp(val, 2)) if snap["free"] >= 0 else T("You are {amt} short this month.", amt=gbp(val, 2))
        st.write("")
        st.markdown("**" + T("Your safety net:") + "** " + T("your savings could pay your usual spending for {cover}.", cover=cover_phrase(weeks)))
        with st.expander(T("What does 'cover' mean?")):
            st.write(T("'Cover' tells you how long your emergency savings could pay for your normal life if your income suddenly stopped. We take your emergency savings ({buf}) "
                       "and divide them by what you usually spend in a week ({wk}) on rent, bills, food, travel and everything else. That gives {cover}.",
                       buf=gbp(p["buffer"]), wk=gbp(weekly), cover=cover_phrase(weeks)))
            st.write(T("A good first goal is 4 weeks of spending ({target}). It helps you get through a late payment, a quiet month or a surprise bill without borrowing.", target=gbp(4 * weekly)))

        st.subheader(T("Heads up"))
        alerts = heads_up(snap, monthly, full, xmas)
        if not alerts:
            card("ok", T("Nothing urgent right now"))
        for kind, msg in alerts:
            card(kind, msg)
        st.markdown("**" + T("Your safety-net path:") + "** " + T("first 1 week, then 4 weeks, then 12 weeks of your usual spending covered by savings."))
        st.progress(min(p["buffer"] / (4 * weekly), 1.0), text=T("{have} saved of {target} (a 4-week safety net)", have=gbp(p["buffer"]), target=gbp(4 * weekly)))

    # ---- THIS MONTH
    with tabs[1]:
        st.subheader(T("{month} - {d} days left", month=T(pd.Timestamp(TODAY).strftime("%B")), d=snap["days_left"]))
        m = st.columns(3)
        per_week = max(snap["free"], 0) / max(math.ceil(snap["days_left"] / 7), 1)
        m[0].metric(T("After bills & goal"), gbp(snap["left_after_bills"]))
        m[1].metric(T("Truly free"), gbp(snap["free"]))
        m[2].metric(T("Per week"), gbp(per_week))
        parts = [(T("Already spent"), snap["spent_so_far"]), (T("Bills still due"), snap["upcoming"]), (T("Savings goal"), snap["savings_goal"]),
                 (T("Everyday costs to come"), snap["var_to_come"]), (T("Shortfall") if snap["free"] < 0 else T("Free to spend"), abs(snap["free"]))]
        cols_ = ["#9A8FA3", DARK, "#7B3FA8", "#B58BD6", cr if snap["free"] < 0 else "#E6D5F2"]
        tot = sum(v for _, v in parts) or 1.0
        segs = "".join(f"<div title='{n}: {gbp(v, 2)}' style='width:{v / tot * 100:.2f}%;background:{c}'></div>" for (n, v), c in zip(parts, cols_) if v > 0)
        legend = "".join(f"<div><i style='background:{c}'></i><span>{n}: <b>{gbp(v, 2)}</b></span></div>" for (n, v), c in zip(parts, cols_))
        st.markdown(f"<div class='seg' role='img' aria-label='{T('How this month splits up')}'>{segs}</div><div class='leg'>{legend}</div>", unsafe_allow_html=True)
        st.markdown("**" + T("Week by week") + "**")
        wl = max(math.ceil(snap["days_left"] / 7), 1)
        wk = pd.DataFrame({T("Week"): [T("Week {i} (from {d})", i=i + 1, d=f"{(TODAY + timedelta(days=7 * i)).day} {T((TODAY + timedelta(days=7 * i)).strftime('%B'))}") for i in range(wl)],
                           T("Free to spend"): [gbp(max(snap["free"], 0) / wl)] * wl})
        st.table(wk.set_index(T("Week")))
        with st.expander(T("Why this number? (see the working)")):
            st.table(pd.DataFrame({T("Item"): [T("Expected income"), T("Spent so far"), T("Bills still due"), T("Savings goal"), T("= Left after bills & goal"), T("Everyday costs still to come"), T("= Truly free")],
                                   "£": [gbp(snap["expected_income"]), gbp(-snap["spent_so_far"]), gbp(-snap["upcoming"]), gbp(-snap["savings_goal"]),
                                         gbp(snap["left_after_bills"]), gbp(-snap["var_to_come"]), gbp(snap["free"])]}).set_index(T("Item")))
            st.caption(T("Expected income = higher of what has arrived ({a}) and your cautious past level ({b}), so we don't over-promise when income is irregular.",
                         a=gbp(snap["income_so_far"]), b=gbp(snap["conservative"])))
        with st.expander(T("This looks wrong - let me correct it")):
            st.checkbox(T("Use my corrections"), key="ov_on")
            st.number_input(T("Income I really expect this month (£)"), value=float(round(snap["expected_income"])), step=10.0, key="ov_income")
            st.number_input(T("Bills I know are still due (£)"), value=float(round(snap["upcoming"])), step=10.0, key="ov_bills")
            if ss.get("ov_on"):
                st.info(T("Using your numbers. You know your life better than any model."))

    # ---- FUTURE
    with tabs[2]:
        st.subheader(T("Two futures: do nothing vs act on the fixes"))
        a = st.columns(3)
        capture = a[0].slider(T("Extra costs you cut (%)"), 0, 100, 60, key="f_cap") / 100
        rate = a[1].slider(T("Savings interest (% a year)"), 0.0, 6.0, 3.0, key="f_rate") / 100
        years = a[2].slider(T("Years ahead"), 1, 10, 5, key="f_years")
        b = st.columns(3)
        drop = b[0].slider(T("What if income drops (%)"), 0, 50, 0, key="f_drop") / 100
        xmas_in = b[1].number_input(T("Christmas extra spend (£)"), value=150.0, step=25.0, key="xmas")
        rent_rise = b[2].number_input(T("Rent rise from year 2 (£/month)"), value=0.0, step=10.0, key="f_rent")
        sim = simulate(monthly, p["buffer"], years, capture, total_prem, rate, drop, xmas_in, rent_rise)
        M = years * 12
        d = pd.DataFrame({"m": range(M + 1), "a10": sim["nothing"][0], "a50": sim["nothing"][1], "a90": sim["nothing"][2],
                          "b10": sim["act"][0], "b50": sim["act"][1], "b90": sim["act"][2]})
        x = alt.X("m:Q", title=T("Months from now"))
        base = alt.Chart(d)
        layers = [
            base.mark_area(opacity=0.10, color=cr).encode(x=x, y="a10:Q", y2="a90:Q"),
            base.mark_area(opacity=0.10, color=cp).encode(x=x, y="b10:Q", y2="b90:Q"),
            base.mark_area(opacity=0.28, color=cp).encode(x=x, y=alt.Y("a50:Q", title=T("Money position (£)")), y2="b50:Q"),
            base.mark_line(color=cr, strokeWidth=3, strokeDash=[7, 4]).encode(x=x, y="a50:Q"),
            base.mark_line(color=cp, strokeWidth=3).encode(x=x, y="b50:Q"),
        ]
        last = d.iloc[[-1]].assign(la=lambda z: z.a50.map(gbp), lb=lambda z: z.b50.map(gbp))
        layers += [alt.Chart(last).mark_text(align="right", dy=-10, color=cr, fontSize=14, font=font_name(), fontWeight="bold").encode(x="m:Q", y="a50:Q", text="la:N"),
                   alt.Chart(last).mark_text(align="right", dy=-10, color=cp, fontSize=14, font=font_name(), fontWeight="bold").encode(x="m:Q", y="b50:Q", text="lb:N")]
        ev = [{"m": ((12 - TODAY.month) % 12) or 12, "label": T("Christmas")}]
        if rent_rise > 0 and M > 12:
            ev.append({"m": 12, "label": T("Rent rise")})
        evd = pd.DataFrame(ev)
        layers += [alt.Chart(evd).mark_rule(strokeDash=[4, 3], color="#888").encode(x="m:Q"),
                   alt.Chart(evd).mark_text(angle=270, align="left", dx=-6, dy=-4, color="#555", font=font_name()).encode(x="m:Q", text="label:N", y=alt.value(0))]
        st.altair_chart(style_chart(alt.layer(*layers), 340))
        st.markdown(f"<span style='color:{cr}'><b>{wr}</b></span> {T('= do nothing')} &nbsp; <span style='color:{cp}'><b>{wp}</b></span> {T('= act on the fixes')} &nbsp; "
                    + T("(shaded band = likely range, 10th to 90th percentile)"), unsafe_allow_html=True)
        e, f_ = sim["nothing"][:, -1], sim["act"][:, -1]
        st.metric(T("Difference after {y} years (most likely)", y=years), gbp(f_[1] - e[1]))
        st.caption(T("Do nothing: most likely {a} (range {a1} to {a2}). Act: {b} (range {b1} to {b2}). Built by reshuffling your own {n} past months - so it's a small sample, not a market prediction. Ignores inflation.",
                     a=gbp(e[1]), a1=gbp(e[0]), a2=gbp(e[2]), b=gbp(f_[1]), b1=gbp(f_[0]), b2=gbp(f_[2]), n=len(monthly)))
        st.markdown("**" + T("Your safety-net path (social mobility)") + "**")
        pm = pd.DataFrame({"Month": range(13), "w": [(p["buffer"] + p["goal"] * k) / weekly for k in range(13)]})
        ln = alt.Chart(pm).mark_line(color=cp, strokeWidth=3).encode(x=alt.X("Month:Q", title=T("Months from now")), y=alt.Y("w:Q", title=T("Weeks of your spending covered")))
        rule = alt.Chart(pd.DataFrame({"y": [4]})).mark_rule(color=cr, strokeDash=[5, 4]).encode(y="y:Q")
        st.altair_chart(style_chart(ln + rule, 200))
        st.caption((T("Orange line = 4 weeks of spending covered by savings.") if cb else T("Red line = 4 weeks of spending covered by savings.")) + " " + safety_tip(p, weekly, weeks))

    # ---- CAN I BUY IT
    with tabs[3]:
        st.subheader(T("Can I afford this right now?"))
        item = st.text_input(T("What do you want to buy?"), placeholder=T("e.g. trainers"), key="buy_item")
        price = st.number_input(T("Price (£)"), min_value=0.0, step=1.0, key="buy_price")
        essential = st.checkbox(T("It's an essential (food, medicine, school, travel to work)"), key="buy_ess")
        if st.button(T("Check it"), type="primary", key="buy_go") and price > 0:
            kind, head, reasons = purchase_verdict(price, essential, snap)
            card(kind, head, "<br>".join(reasons))
            if kind != "ok":
                st.caption(T("Cheaper or later options often help: try a lower price, or check again after payday."))
            st.caption(T("Uses the Budget numbers in 'This month'. This is guidance - you decide."))

    # ---- EXTRA COSTS
    with tabs[4]:
        st.subheader(T("Your personalised hidden costs"))
        st.caption(T("Things that cost you extra because of your situation"))
        if prem_all.empty:
            card("ok", T("No extra-cost patterns found"))
        else:
            st.metric(T("Estimated extra cost per year"), gbp(total_prem))
            show = pd.DataFrame({T("Category"): prem_all.Category.map(T), T("Spend per year"): prem_all["Spend per year"].map(gbp),
                                 T("Extra cost per year"): prem_all["Extra cost per year"].map(gbp), T("Basis"): prem_all.Basis.map(basis_txt),
                                 T("Excluded"): prem_all.Category.map(lambda c: T("deliberate") if ss.get(f"delib_{c}") else "")})
            st.table(show.set_index(T("Category")))
            st.caption(T("Fees are actual charges. Markups are assumptions - check real quotes before acting."))
            recent = full[full.category.isin(FEE_CATS) & (full.date >= pd.Timestamp(TODAY) - pd.Timedelta(days=60))]
            crisis = len(recent) >= 3
            for _, row in prem_all.iterrows():
                cfg = PREMIUMS[row.Category]
                with st.expander(T("{cat} - about {amt} a year", cat=T(row.Category), amt=gbp(row["Extra cost per year"]))):
                    st.markdown("**" + T("Why it happens:") + "** " + T(cfg["why"]))
                    st.markdown("**" + T("1. Free fix:") + "** " + T(cfg["free"]))
                    st.markdown("**" + T("2. Outside help you may be entitled to:") + "** " + T(cfg["ext"]) + " *(" + T("check current eligibility - we can't promise you qualify") + ")*")
                    st.markdown("**" + T("Message you can send:") + "**")
                    st.code(T(cfg["msg"]), language=None)
                    st.checkbox(T("This one is deliberate - don't count it"), key=f"delib_{row.Category}")
                    if row.Category == "Insurance (monthly)":
                        st.markdown("**" + T("3. Lower-cost borrowing - only if it passes the safety checks:") + "**")
                        yearly = row["Spend per year"] / (1 + cfg["rate"])
                        cc = st.columns(2)
                        apr = cc[0].slider(T("Loan APR (%)"), 0.0, 40.0, 12.0, key="apr_ins")
                        term = cc[1].slider(T("Repay over (months)"), 3, 24, 12, key="term_ins")
                        pay, interest = loan_interest(yearly, apr, term)
                        saving = row["Extra cost per year"]
                        st.write(T("Pay {a} upfront to save about {b}. Loan costs {c} interest ({d}/month).", a=gbp(yearly), b=gbp(saving), c=gbp(interest), d=gbp(pay)))
                        if crisis:
                            card("stop", T("Blocked"), T("You've had several fees recently. Please get free debt advice before borrowing."))
                        elif avg_net <= 0 or pay > avg_net * 0.5:
                            card("stop", T("Blocked"), T("Repayments would take too much of your typical monthly leftover."))
                        elif interest >= saving:
                            card("stop", T("Not worth it"), T("Interest ({a}) is more than you'd save ({b}).", a=gbp(interest), b=gbp(saving)))
                        else:
                            card("ok", T("Passes the break-even test"), T("Saves about {a} a year after interest. Demo only - a real offer comes from a regulated lender.", a=gbp(saving - interest)))
                    else:
                        st.caption(T("We don't suggest a loan for this one. If it keeps happening, free debt advice is the best next step."))

    # ---- STRESS TEST
    with tabs[5]:
        st.subheader(T("What if something unexpected hits?"))
        ss.setdefault("custom_shocks", {})
        shocks = {"Boiler breaks": ("lump", 450.0), "Car repair": ("lump", 300.0), "Job loss": ("weeks", 8), "Benefit payment delayed": ("weeks", 5)}
        shocks.update(ss.custom_shocks)
        OWN = "+ Add my own..."
        name = st.selectbox(T("Choose a shock"), list(shocks) + [OWN], format_func=lambda k: T("+ Add my own...") if k == OWN else T(k), key="shock_pick")
        own_label = ""
        if name == OWN:
            own_label = st.text_input(T("What is the unexpected cost?"), placeholder=T("e.g. Laptop repair, vet bill, school trip"), key="shock_name").strip()
            ktype = st.radio(T("What kind is it?"), ["one", "weeks"], horizontal=True, key="shock_type",
                             format_func=lambda k: T("One-off cost (£)") if k == "one" else T("Lost income (weeks)"))
            kind, val = ("lump", 100.0) if ktype == "one" else ("weeks", 6)
        else:
            kind, val = shocks[name]
        cc = st.columns(3)
        wks = 0
        if kind == "lump":
            amount = cc[0].number_input(T("Cost (£)"), value=float(val), min_value=0.0, step=10.0, key=f"amt_{name}")
        else:
            wks = cc[0].slider(T("Weeks without that income"), 1, 16, int(val), key=f"wks_{name}")
            amount = max(wks * weekly, 0)
        if name == OWN and own_label:
            if st.button(T("Save to my list"), key="save_shock"):
                ss.custom_shocks[own_label] = (kind, amount if kind == "lump" else wks)
                st.rerun()
        apr = cc[1].slider(T("Borrowing cost (APR %)"), 0.0, 60.0, 35.0, key="st_apr")
        term = cc[2].slider(T("Months to repay"), 1, 24, 6, key="st_term")
        borrow1 = max(amount - p["buffer"], 0)
        _, extra0 = loan_interest(amount, apr, term)
        _, extra1 = loan_interest(borrow1, apr, term)
        l, r = st.columns(2)
        l.metric(T("Total cost if you had no savings"), gbp(amount + extra0), delta=T("+{x} extra for borrowing", x=gbp(extra0)), delta_color="inverse")
        r.metric(T("Total cost with your savings ({buf})", buf=gbp(p["buffer"])), gbp(amount + extra1), delta=T("+{x} extra for borrowing", x=gbp(extra1)), delta_color="inverse")
        if kind == "weeks":
            st.write(T("Your usual spending is about {w} a week, so your savings cover {c}; this shock lasts {n} weeks.", w=gbp(weekly), c=cover_phrase(weeks), n=wks))
        if amount > 0:
            if borrow1 == 0:
                head, body, ck = T("Your savings cover it"), T("You could pay the whole {amt} from your own savings and would not need to borrow at all. Without savings you would pay about {x} in extra interest.", amt=gbp(amount), x=gbp(extra0)), "ok"
            elif p["buffer"] <= 0:
                head, body, ck = T("Even a small cushion would help"), T("With no savings you would have to borrow {b0} and pay about {i0} in interest. Every pound saved first is a pound you don't borrow.", b0=gbp(amount), i0=gbp(extra0)), "care"
            else:
                head, body, ck = T("What your savings would change"), T("Without savings you would borrow {b0} and pay {i0} in interest. With your {buf} saved you would borrow only {b1} and pay {i1} in interest - about {d} less.",
                                                                           b0=gbp(amount), i0=gbp(extra0), buf=gbp(p["buffer"]), b1=gbp(borrow1), i1=gbp(extra1), d=gbp(extra0 - extra1)), "care"
            card(ck, head, body)
        else:
            head, body = "", ""
        worst = monthly["Net"].idxmin()
        wn = monthly.loc[worst, "Net"]
        wm = month_label(worst.to_timestamp())
        st.markdown("**" + T("Replay your own worst month") + "**")
        if wn < 0:
            worst_txt = T("In {m} you ended {a} short. A buffer of at least {a} would have avoided overdraft or credit costs that month.", m=wm, a=gbp(-wn))
        else:
            worst_txt = T("Even your worst month finished above zero - a small buffer still protects you from surprises.")
        st.write(worst_txt)

    # ---- ASK
    with tabs[6]:
        st.subheader(T("Ask your money coach"))
        st.markdown("**" + T("Hear from our NWC AI Chatbox on any general queries you might have!") + "**")

        def _ask():
            q_ = ss.get("ask_pick")
            if q_:
                ss.chat.append({"q": q_})
            ss.ask_pick = None

        st.selectbox(T("ai.nwc"), PRESET_QS, index=None, placeholder=T("Ask your question"), format_func=T, key="ask_pick", on_change=_ask)
        ctx = dict(snap=snap, monthly=monthly, prem=prem, p=p, weeks=weeks, weekly=weekly)
        for it in reversed(ss.chat):
            st.caption(T("You asked: {q}", q=T(it["q"])))
            card("ok", T("ai.nwc"), fmt(preset_answer(it["q"], ctx)))
            st.caption(T("Answered by ai.nwc"))
        if ss.chat and st.button(T("Clear conversation"), key="clr_chat"):
            ss.chat = []
            st.rerun()


sidebar_common()
inject_css()
if ss.stage != "app" or ss.df is None:
    if not ss.intro1:
        intro(greeting(), T("Welcome to NWC - NatWest Count"))
        ss.intro1 = True
    onboarding()
else:
    main_app()
