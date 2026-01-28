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
app = FastAPI(title="AI Mentor SaaS Platform - V10 (Sequential Guide Logic)")

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
    print("✅ System Ready: V10 (Auto-Guide & Sequential)")
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


# --- API UTAMA: CHAT (V10 - SEQUENTIAL GUIDE LOGIC) ---
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

    # 4. Simpan Chat User ke DB
    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "user", "message": request.message
    }).execute()

    # =========================================================
    # LOGIC V10: CHECK HISTORY & DETERMINE PHASE
    # =========================================================
    
    # Ambil 10 pesan terakhir untuk menganalisis urutan (Flow)
    past_chats = supabase.table("chat_history").select("sender, message")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id)\
        .order("created_at", desc=True).limit(10).execute().data
    
    # Balik urutan agar kronologis (Lama -> Baru)
    past_chats.reverse()
    
    # Cek apakah ini pesan pertama (history cuma 1 yaitu pesan user barusan)
    # Jika history <= 1, berarti ini sesi baru -> TRIGGER INTRO OTOMATIS
    is_start = len(past_chats) <= 1

    messages_payload = []
    for chat in past_chats:
        role = "user" if chat['sender'] == "user" else "assistant"
        # Skip pesan terakhir user di payload history agar tidak dobel saat di-append di akhir
        if chat['message'] != request.message: 
            messages_payload.append({"role": role, "content": chat['message']})

    # Deteksi Angka (Math Trigger)
    has_numbers = bool(re.search(r'\d+', request.message))
    math_instruction = ""
    if has_numbers:
        math_instruction = """
        [MATH MODE ACTIVE]
        - User provided specific numbers (e.g. price, capital).
        - YOU MUST CALCULATE the strategy using formulas from the KNOWLEDGE BASE.
        - Output the calculation steps and final result clearly.
        """

    # ==============================================================================
    # SYSTEM PROMPT V10 (SEQUENTIAL GUIDANCE)
    # ==============================================================================
    system_prompt = f"""
    ROLE: You are {mentor['name']}, a business practitioner/mentor.
    KNOWLEDGE BASE (KB):
    {knowledge_base}
    
    USER CONTEXT: {request.business_type}
    
    {math_instruction}
    
    rules to FOLLOW STRICTLY:
    
    1. **ABSOLUTE VERBATIM COPY (CRITICAL):**
       - If user asks for "Roadmap", "Steps", or "Ways", you MUST COPY the text from the KNOWLEDGE BASE word-for-word.
       - **DO NOT SUMMARIZE.** Even if the text is long (e.g. 14 Days, 10 Slides), WRITE IT ALL OUT.
       - **DO NOT SKIP DETAILS.** (e.g. Never write "Slide 1, 2, and so on". You MUST write Slide 1, Slide 2, Slide 3... until the end).
       - **DO NOT CHANGE THE LANGUAGE STYLE.** If the text is formal, keep it formal. If informal, keep it informal.
       - **DO NOT ADD LABELS** like "PART 1" or "Here is the answer". Just give the content.
       - **DO NOT ADD HALLUCINATIONS.** Do not add "Target: 1 hari" if it is not written in the PDF.
    
    2. **MATH AGENT (IF NUMBERS PROVIDED):**
       - If 'MATH MODE' is active, perform the calculation *after* providing the theory.
       - Show the calculation clearly.
    
    --------------------------------------------------------
    YOUR MAIN GOAL: Guide the user sequentially through the Knowledge Base.
    DO NOT ANSWER RANDOMLY. FOLLOW THIS EXACT FLOW:
    --------------------------------------------------------
    
    PHASE 1: AUTO-INTRODUCTION (If this is the START/FIRST Message)
    - If user says "Halo", "Siapa ini", or starts the chat:
    - **OUTPUT:** Introduce yourself using the 'Latar Belakang' from KB (Page 1). Mention your experience, F&B, Cateringaja, MentorAja, etc VERBATIM.
    - **MANDATORY CLOSING:** "Apa yang ingin anda konsultasikan hari ini atau ingin saya arahkan langkah langkah membangun bisnis sesuai kondisi anda?"
    
    PHASE 2: DATA GATHERING (Triggered if user says "Boleh", "Arahkan", "Iya")
    - If user agrees to guidance BUT hasn't given business details yet:
    - **OUTPUT:** "Oke, saya bantu arahkan langkah langkah. Sebelum dimulai, saya perlu sedikit gambaran tentang bisnis anda, bisa tolong jelaskan detail informasi bisnis anda sekarang (Nama, Produk, Target)?"
    
    PHASE 3: SEQUENTIAL TEACHING (Triggered after user gives Business Details)
    - **STEP 1 (First Topic):** Explain 'Definisi bisnis bagus untuk pemula' (5 Kriteria) from KB.
      - **Content:** COPY EXACTLY from KB.
      - **Context:** Relate it briefly to the user's business.
      - **CLOSING:** "Nah, itu definisinya. Selanjutnya, mau kita bahas tentang Prinsip/heuristik (minimal 3) paling sering dipakai saat bingung ambil keputusan?"
      
    - **STEP 2 (Second Topic):** If user agreed to Step 1 Closing -> Explain 'Prinsip/Heuristik'.
      - **Content:** COPY EXACTLY from KB.
      - **CLOSING:** "Selanjutnya, mau kita bahas tentang Aturan Risiko (Gas vs Rem)?"
      
    - **STEP 3 ... and so on:** Continue following the PDF structure point by point.
    
    --------------------------------------------------------
    GENERAL RULES:
    1. **VERBATIM CONTENT:** When explaining a list/definition, COPY the text from KB word-for-word. Do not summarize.
    2. **CONVERSATIONAL TONE:** Use natural Indonesian for opening/closing, but keep the educational content strict.
    3. **ONE THING AT A TIME:** Do not merge multiple topics (e.g. don't answer Definition AND Principles in one chat).
    4. **MATH:** If user asks for calculation help, do the math based on KB formulas.
    """
    
    # Masukkan System Prompt di awal payload
    final_messages = [{"role": "system", "content": system_prompt}] + messages_payload
    
    # Masukkan Pesan Terakhir User
    final_messages.append({"role": "user", "content": request.message})
    
    try:
        # Request ke Groq (Model 70B - Wajib untuk logika flow yang panjang ini)
        completion = client.chat.completions.create(
            messages=final_messages,
            model="llama-3.3-70b-versatile", 
            temperature=0.2, # Rendah agar patuh copy-paste (Verbatim) & Flow
            max_tokens=4500, 
        )
        ai_reply = completion.choices[0].message.content

    except Exception as e:
        print(f"Error AI: {e}")
        ai_reply = "Maaf, sistem sedang sibuk. Mohon coba lagi."

    # 5. Simpan Jawaban AI ke DB
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

@app.get("/")
def home(): return {"status": "AI Mentor SaaS Backend V10.0 (SEQUENTIAL GUIDE) Active"}