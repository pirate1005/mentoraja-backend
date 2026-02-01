import os
import json
from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client
from groq import Groq
from fastapi.middleware.cors import CORSMiddleware
import midtransclient
from datetime import datetime, timedelta
import pypdf
import re

# ==========================================
# 1. SETUP SYSTEM
# ==========================================
load_dotenv()
app = FastAPI(title="AI Mentor SaaS Platform - V11 (Strict Single Step)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    snap = midtransclient.Snap(
        is_production=False, 
        server_key=os.getenv("MIDTRANS_SERVER_KEY"),
        client_key=os.getenv("MIDTRANS_CLIENT_KEY")
    )
    print("✅ System Ready: V11 (Single Step Enforced)")
except Exception as e:
    print(f"❌ Error Setup: {e}")


# ==========================================
# 2. DATA MODELS
# ==========================================
class ChatRequest(BaseModel):
    user_id: str
    mentor_id: int
    message: str
    business_type: str = "Umum"
    
class ChatHistoryRequest(BaseModel):
    user_id: str
    mentor_id: int

class PaymentRequest(BaseModel):
    user_id: str
    mentor_id: int
    amount: int
    email: str = "-" 
    first_name: str = "User"

class ReviewRequest(BaseModel):
    user_id: str
    mentor_id: int
    rating: int
    comment: str

class MentorSettingsRequest(BaseModel):
    mentor_id: int
    category: str
    bank_name: str
    bank_number: str
    account_holder: str
    price: int

class AdminUpdateUserRequest(BaseModel):
    full_name: str
    role: str

class PayoutRequestModel(BaseModel):
    mentor_id: int
    amount: int
    bank_info: str 

class DiscoveryInput(BaseModel):
    user_goal: str
    
    
class DeleteChatRequest(BaseModel):
    user_id: str
    mentor_id: int
    
class DeleteDocsRequest(BaseModel):
    mentor_id: int


# ==========================================
# 3. API ENDPOINTS
# ==========================================

# --- API AI DISCOVERY ---
@app.post("/discovery/generate-questions")
async def generate_discovery_questions(data: DiscoveryInput):
    try:
        system_prompt = "You are a backend system. Output ONLY JSON."
        user_prompt = f"""
        User Goal: "{data.user_goal}".
        Task: Create 3 follow-up multiple choice questions in INDONESIAN.
        Output JSON: [{{"id": 1, "question": "...", "icon": "emoji", "options": ["A", "B"]}}]
        """
        chat_completion = client.chat.completions.create(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            model="llama-3.1-8b-instant", 
            temperature=0.3,
        )
        clean_json = chat_completion.choices[0].message.content.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        print(f"Error Discovery: {e}")
        return [
            {"id": 1, "question": "Fokus bisnis?", "icon": "🎯", "options": ["Marketing", "Operasional", "Keuangan"]},
            {"id": 2, "question": "Skala saat ini?", "icon": "📈", "options": ["Ide", "Rintisan", "Stabil"]},
            {"id": 3, "question": "Kendala utama?", "icon": "🚧", "options": ["Modal", "Strategi", "Tim"]}
        ]


# --- API UTAMA: CHAT (V11 - ENFORCED SINGLE STEP) ---
@app.post("/chat")
async def chat_with_mentor(request: ChatRequest):
    # 1. Cek Subscription
    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
    sub_check = supabase.table("subscriptions").select("*")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id)\
        .eq("status", "settlement").gte("created_at", thirty_days_ago).execute()
    is_subscribed = len(sub_check.data) > 0
    
    # 2. Cek Limit
    history_count = supabase.table("chat_history").select("id", count="exact")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id).eq("sender", "user").execute()
    user_chat_count = history_count.count if history_count.count else 0
    
    if not is_subscribed and user_chat_count >= 5:
        return {"reply": "LIMIT_REACHED", "mentor": "System", "usage": user_chat_count}

    # 3. Data Mentor & Knowledge Base
    mentor_data = supabase.table("mentors").select("*").eq("id", request.mentor_id).single().execute()
    mentor = mentor_data.data if mentor_data.data else {"name": "Mentor", "personality": "Profesional", "expertise": "Bisnis"}
    
    docs = supabase.table("mentor_docs").select("content").eq("mentor_id", request.mentor_id).execute()
    knowledge_base = "\n\n".join([d['content'] for d in docs.data])

    # 4. Simpan Chat User
    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "user", "message": request.message
    }).execute()

    # =========================================================
    # LOGIC V11: FETCH HISTORY
    # =========================================================
    past_chats = supabase.table("chat_history").select("sender, message")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id)\
        .order("created_at", desc=True).limit(10).execute().data
    past_chats.reverse()
    
    messages_payload = []
    for chat in past_chats:
        role = "user" if chat['sender'] == "user" else "assistant"
        if chat['message'] != request.message: 
            messages_payload.append({"role": role, "content": chat['message']})

    has_numbers = bool(re.search(r'\d+', request.message))
    math_instruction = ""
    if has_numbers:
        math_instruction = """
        [MATH MODE ACTIVE]
        - The user provided numbers. YOU MUST CALCULATE the strategy using formulas from the KNOWLEDGE BASE.
        - Output the result directly (e.g., "Jual di harga Rp X").
        """

    # ==============================================================================
    # SYSTEM PROMPT V11 (SINGLE STEP ENFORCER)
    # ==============================================================================
    system_prompt = f"""
ROLE
You are {mentor['name']}, a business practitioner/mentor. You speak like a real mentor: direct, warm, practical.
You are NOT a generic assistant. Your job is to help the user THINK and DECIDE, then (if asked) EXECUTE together.

SOURCE OF TRUTH (MENTOR KNOWLEDGE)
The only allowed business doctrine, rules, heuristics, playbooks, formulas, thresholds, and examples come from:
{knowledge_base}

USER CONTEXT (DYNAMIC)
- User first name (if known): {request.user_first_name}
- Business snapshot (if known): {request.business_snapshot}
- Conversation memory: decisions made, metrics shared, constraints, progress, doubts.

========================================================
MENTORAJA CORE PHILOSOPHY (ANTI-CHATGPT)
1) Not a Q&A machine. You mentor.
2) Mild professional ego: you can disagree, set boundaries, and state tradeoffs.
3) You lead the session: you choose the next best question or next step.
4) You use pauses/thinking moments naturally (short, not theatrical).
5) Primary goal: help user think and take decisions, not just dump answers.

HUMAN REALISM LAYER
- Start with a clear frame: what we’re solving today.
- Refer to prior context naturally (decisions, metrics, earlier doubts).
- Reflective language: “Yang saya tangkap dari cerita kamu…”
- Avoid excessive bullet spam. Prefer short paragraphs; if needed, max 3–5 short numbered items.
- Avoid: generic neutrality, “tergantung” as a habit, over-politeness, academic tone.

FORBIDDEN PATTERNS (STRICT)
- No generic motivational fluff.
- No giving 10 tips at once.
- No repeating user’s question verbatim as filler.
- No “tergantung” without immediately narrowing with a specific question.
- No overpromising results (no guaranteed revenue, “pasti berhasil”, etc).
- No commanding tone (“kamu harus…”) without rationale/tradeoff; prefer “kalau X, saya sarankan Y karena…”.

========================================================
GLOBAL STATE MACHINE (FSM)
You must choose a state each turn based on user message + stored context.

STATES
A) Exploration Mode: user belum jelas masalahnya.
B) Clarification Mode: kamu menggali konteks/minimum inputs.
C) Teaching Mode: kamu menjelaskan 1 konsep/aturan mentor (ONE topic only).
D) Execution Mode: kamu menerapkan ke bisnis user (hitung/rumus/strategi/plan).
E) Validation Mode: kamu menilai keputusan user vs aturan mentor (tegas + alasan).
F) Reflection Mode: kamu ajak evaluasi hasil/lesson.
G) Continuation Mode: kamu arahkan langkah berikutnya (next session direction).

STATE TRANSITION (HIGH LEVEL)
- If no business context yet -> Clarification Mode.
- If user asks “arahkan langkah-langkah”, “dari nol”, “step by step” -> Clarification Mode (then Teaching/Execution sequentially).
- If user asks a direct business question -> Clarification Mode if inputs missing; else Teaching Mode (1 topic) then offer Execution.
- If user says “bantu hitung”, “bantu terapkan”, “sesuaikan” -> Execution Mode.
- If user proposes a decision -> Validation Mode.
- If user reports results -> Reflection Mode.
- Always end with a guided next direction -> Continuation Mode cue in closing line.

========================================================
OPENING (FIRST TURN ONLY)
Backend will supply mentor background (from KB) in the intro. After intro you must ask ONE specific opener:
- If {request.user_first_name} known: address them by first name.
- Ask user’s business basics (minimum): brand name, what they sell, target customer, current stage, and the immediate goal/problem today.
Do NOT teach yet. This is to populate memory.

Example closing question (one only):
“{request.user_first_name}, bisnis kamu sekarang jual apa, targetnya siapa, dan hari ini kamu lagi mau beresin apa dulu?”

========================================================
THE TWO USER MODES (MUST SUPPORT BOTH)
MODE 1 — User asks a direct “core question”
- If required data is missing, ask ONLY what’s needed (1–3 questions max).
- Then teach ONE relevant mentor rule/framework (Teaching Mode).
- Immediately offer to apply it (Execution Offer, handled by logic below).

MODE 2 — User wants step-by-step guidance from zero
- You must follow the mentor’s playbook/sequence found in {knowledge_base}.
- One step/topic per message (ONE topic only).
- After each step/topic, offer Execution (apply it to the user’s business).

========================================================
EXECUTION OFFER + DATA GATING (CRITICAL)
After you teach ANY mentor rule/framework/formula, you MUST move toward application.

EXECUTION OFFER (ALWAYS)
- You do NOT need to write “saya bisa bantu” in a long scripted way.
- End with a short, natural offer that invites application NOW.

DATA GATING (NEVER GUESS)
If execution/adaptation requires variables and you don’t have them:
- Do NOT proceed with assumptions.
- Ask for the missing inputs only.
- Once the user provides them, execute immediately.

Examples:
- If KB says “diskon 10% dari harga jual” but you don’t have price -> ask for price.
- If KB says pricing uses cost + margin -> ask for cost, target margin, channel constraints.

========================================================
NAME USAGE (NATURAL HUMAN TOUCH)
Use the user’s first name naturally at least once per assistant message:
- Prefer opening or closing.
- Don’t overdo it (avoid sounding robotic).

If user name is unknown:
Ask once early: “Boleh saya panggil kamu siapa?” then store it.

========================================================
TOPIC DISCIPLINE (ONE TOPIC ONLY)
In Teaching Mode, you MUST:
1) Select the single most relevant rule/framework from KB.
2) Explain it in mentor voice (can paraphrase; preserve meaning).
3) Add 1 sentence linking it to user’s business context.
4) Close with: a) tiny execution offer OR b) one specific next direction question (not empty).

========================================================
WHEN USER IS PASSIVE / ANSWERS SHORT
Backend may trigger follow-up events. If you receive a “user_passive” or “short_reply” signal:
- Ask a single, specific probing question.
- Or reframe the last point in simpler words (one short paragraph), then ask one specific question.

========================================================
VIDEO MENTORING OUTPUT (IF ENABLED)
If channel==video:
- Speak with stable tempo.
- Insert short natural pauses after key points (use backend-supported pause tags if available).
- Confirm verbally, not as text checklist.
- If user silence event arrives, follow the silence decision tree.

========================================================
SAFETY / QUALITY BAR
- Be clear about tradeoffs/risks.
- No guaranteed outcomes.
- Keep answers practical and decision-oriented.
- Always remain consistent with {knowledge_base}.

END OF SYSTEM PROMPT
"""
    
    final_messages = [{"role": "system", "content": system_prompt}] + messages_payload
    final_messages.append({"role": "user", "content": request.message})
    
    try:
        completion = client.chat.completions.create(
            messages=final_messages,
            model="llama-3.3-70b-versatile", 
            temperature=0.1, # SANGAT RENDAH agar patuh
            max_tokens=4500, 
        )
        ai_reply = completion.choices[0].message.content

    except Exception as e:
        print(f"Error AI: {e}")
        ai_reply = "Maaf, sistem sedang sibuk. Mohon coba lagi."

    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "ai", "message": ai_reply
    }).execute()

    return {"mentor": mentor['name'], "reply": ai_reply}


# --- API LAINNYA (TETAP SAMA) ---

@app.get("/mentors/search")
async def search_mentors(keyword: str = None):
    query = supabase.table("mentors").select("*").eq("is_active", True)
    if keyword:
        query = query.or_(f"name.ilike.%{keyword}%,expertise.ilike.%{keyword}%,category.ilike.%{keyword}%")
    return query.execute().data

@app.post("/reviews")
async def submit_review(req: ReviewRequest):
    supabase.table("reviews").insert(req.dict()).execute()
    return {"status": "ok"}

@app.get("/chat/history")
async def get_chat_history(user_id: str, mentor_id: int):
    return supabase.table("chat_history").select("*").eq("user_id", user_id).eq("mentor_id", mentor_id).order("created_at").execute().data

@app.post("/educator/settings")
async def update_settings(req: MentorSettingsRequest):
    bank = {"bank": req.bank_name, "number": req.bank_number, "name": req.account_holder}
    supabase.table("mentors").update({"category": req.category, "price_per_month": req.price, "bank_details": bank}).eq("id", req.mentor_id).execute()
    return {"status": "ok"}

@app.post("/educator/upload")
async def upload_material(mentor_id: int, file: UploadFile = File(...)):
    text = "".join([page.extract_text() for page in pypdf.PdfReader(file.file).pages])
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        supabase.table("mentor_docs").insert({"mentor_id": mentor_id, "content": chunk}).execute()
    return {"status": "ok"}

@app.post("/educator/payout")
async def request_payout(req: PayoutRequestModel):
    supabase.table("payouts").insert({"mentor_id": req.mentor_id, "amount": req.amount, "status": "pending", "bank_info": req.bank_info, "created_at": "now()"}).execute()
    return {"status": "ok"}

@app.get("/educator/payout/history/{mentor_id}")
async def payout_history(mentor_id: int):
    return supabase.table("payouts").select("*").eq("mentor_id", mentor_id).order("created_at", desc=True).execute().data

@app.get("/educator/dashboard/{user_id}")
async def dashboard(user_id: str):
    try:
        m = supabase.table("mentors").select("id").eq("educator_profile_id", user_id).single().execute()
        if not m.data: return {"error": "not found"}
        mid = m.data['id']
        tx = supabase.table("subscriptions").select("*").eq("mentor_id", mid).eq("status", "settlement").execute().data or []
        chart = {}
        for t in tx: chart[t['created_at'][:10]] = chart.get(t['created_at'][:10], 0) + t['net_amount']
        
        revs = supabase.table("reviews").select("rating").eq("mentor_id", mid).execute().data or []
        avg = sum([r['rating'] for r in revs]) / len(revs) if revs else 0
        ai_qual = int((avg/5)*100) if avg > 0 else 98

        return {
            "gross_revenue": sum(t['amount'] for t in tx), "net_earnings": sum(t['net_amount'] for t in tx),
            "students": len(set(t['user_id'] for t in tx)), "ai_quality": ai_qual,
            "chart_data": [{"name":k,"total":v} for k,v in sorted(chart.items())]
        }
    except: return {"students": 0, "gross_revenue": 0}

@app.get("/admin/stats")
async def admin_stats():
    users = supabase.table("profiles").select("id", count="exact").eq("role", "user").execute().count
    mentors = supabase.table("mentors").select("id", count="exact").execute().count
    rev = sum(s['platform_fee_amount'] for s in supabase.table("subscriptions").select("platform_fee_amount").eq("status", "settlement").execute().data)
    return {"active_users": users, "active_mentors": mentors, "platform_revenue": rev}

@app.put("/admin/users/{user_id}")
async def admin_update_user(user_id: str, req: AdminUpdateUserRequest):
    supabase.table("profiles").update(req.dict()).eq("id", user_id).execute()
    supabase.table("activity_logs").insert({"action": "Admin Edit", "details": f"Edit user {user_id}"}).execute()
    return {"status": "ok"}

@app.get("/admin/logs")
async def logs():
    return supabase.table("activity_logs").select("*").order("created_at", desc=True).limit(20).execute().data

@app.post("/payment/create")
async def create_payment(req: PaymentRequest):
    fee = req.amount * 0.1
    oid = f"SUB-{req.user_id[:4]}-{datetime.now().strftime('%d%H%M%S')}"
    trx = snap.create_transaction({
        "transaction_details": {"order_id": oid, "gross_amount": req.amount},
        "customer_details": {"user_id": req.user_id, "email": req.email, "first_name": req.first_name}
    })
    supabase.table("subscriptions").insert({
        "user_id": req.user_id, "mentor_id": req.mentor_id, "midtrans_order_id": oid,
        "amount": req.amount, "net_amount": req.amount-fee, "platform_fee_amount": fee, "status": "pending"
    }).execute()
    return {"token": trx['token'], "redirect_url": trx['redirect_url']}

@app.post("/midtrans-notification")
async def midtrans_notification(n: dict):
    try:
        s = 'pending'
        if n['transaction_status'] in ['capture', 'settlement']: s = 'settlement'
        elif n['transaction_status'] in ['cancel', 'deny', 'expire']: s = 'failed'
        supabase.table("subscriptions").update({"status": s}).eq("midtrans_order_id", n['order_id']).execute()
        return {"status": "ok"}
    except: return {"status": "error"}

@app.post("/user/update-profile")
async def update_profile(user_id: str, full_name: str = None, avatar_url: str = None):
    data = {k: v for k, v in {"full_name": full_name, "avatar_url": avatar_url}.items() if v}
    if data: supabase.table("profiles").update(data).eq("id", user_id).execute()
    return {"status": "ok"}


@app.delete("/chat/reset")
async def reset_chat_history(req: DeleteChatRequest):
    try:
        # Hapus semua pesan antara user dan mentor ini
        response = supabase.table("chat_history").delete()\
            .eq("user_id", req.user_id)\
            .eq("mentor_id", req.mentor_id)\
            .execute()
            
        return {"message": "Chat history deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.delete("/educator/reset-docs")
async def reset_mentor_docs(req: DeleteDocsRequest):
    try:
        # Hapus semua dokumen milik mentor ini
        response = supabase.table("mentor_docs").delete().eq("mentor_id", req.mentor_id).execute()
        return {"status": "ok", "message": "All documents deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def home(): return {"status": "AI Mentor SaaS Backend V11.0 (Single Step Enforced) Active"}