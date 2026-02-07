import os
import json
import re
import uuid
import requests
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
from groq import Groq
import midtransclient
import pypdf

# ==========================================
# 1. SETUP SYSTEM & CONFIGURATION
# ==========================================
load_dotenv()

app = FastAPI(
    title="AI Mentor SaaS Platform - V22 (Strict Logic Feb 2026)",
    description="Backend AI Mentor V22 compliant with mandatory opening, sequential 17-steps, and proactive assistance logic."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    # --- SETUP CREDENTIALS ---
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    
    # ElevenLabs Setup
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
    ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM") 

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = Groq(api_key=GROQ_API_KEY)
    
    snap = midtransclient.Snap(
        is_production=False, 
        server_key=os.getenv("MIDTRANS_SERVER_KEY"),
        client_key=os.getenv("MIDTRANS_CLIENT_KEY")
    )
    print("✅ System Ready: V22 (Strict Logic Feb 2026)")
except Exception as e:
    print(f"❌ Error Setup: {e}")

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================
def generate_elevenlabs_audio(text: str) -> bytes:
    if not ELEVENLABS_API_KEY: return None
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {"Accept": "audio/mpeg", "Content-Type": "application/json", "xi-api-key": ELEVENLABS_API_KEY}
    data = {"text": text[:1000], "model_id": "eleven_multilingual_v2", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    try:
        response = requests.post(url, json=data, headers=headers)
        return response.content if response.status_code == 200 else None
    except: return None

# ==========================================
# 3. DATA MODELS
# ==========================================
class ChatRequest(BaseModel):
    user_id: str
    mentor_id: int
    message: str
    business_type: str = "Umum"
    user_first_name: str = "Teman" 
    business_snapshot: str = "Belum ada data"

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
# 4. API ENDPOINTS
# ==========================================

@app.get("/")
def home():
    return {"status": "AI Mentor Backend V22 Active"}

# --- API CHAT UTAMA (V22 STRICT LOGIC) ---
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

    # 3. Data Mentor & KB
    mentor_data = supabase.table("mentors").select("*").eq("id", request.mentor_id).single().execute()
    mentor = mentor_data.data if mentor_data.data else {"name": "Mentor", "personality": "Senior Consultant", "expertise": "Bisnis", "avatar_url": None}
    
    docs = supabase.table("mentor_docs").select("content").eq("mentor_id", request.mentor_id).execute()
    knowledge_base = "\n\n".join([d['content'] for d in docs.data])

    # 4. Simpan Chat User
    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "user", "message": request.message
    }).execute()

    # 5. Fetch History (Untuk Context)
    past_chats = supabase.table("chat_history").select("sender, message")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id)\
        .order("created_at", desc=True).limit(10).execute().data
    past_chats.reverse()
    
    messages_payload = []
    for chat in past_chats:
        role = "user" if chat['sender'] == "user" else "assistant"
        if chat['message'] != request.message: 
            messages_payload.append({"role": role, "content": chat['message']})

    # ==============================================================================
    # SYSTEM PROMPT V22 (STRICT LOGIC FEB 2026)
    # ==============================================================================
    system_prompt = f"""
ROLE & IDENTITY
You are {mentor['name']}, a senior business practitioner.
Your mission is to guide the user through the 17-step framework in the Knowledge Base (KB) strictly sequentially.

SOURCE OF TRUTH (KB) - "17 LANGKAH BANGUN BISNIS":
{knowledge_base}

========================================================
🚨 STRICT PROTOCOL (LOGIC UPDATE FEB 2026) 🚨
You must behave like a strict program flow. Do NOT act like a generic chat assistant.

PHASE 1: THE MANDATORY GATE (OPENING)
IF the conversation history is short or this is the start:
You MUST ask exactly these TWO questions. 
IGNORE user's greeting, small talk, or introduction. DO NOT ask about "jenis bisnis" or "modal" yet.
Ask ONLY:
1. "1 masalah spesifik apa yang mau kamu bahas?"
2. "Goal kamu apa / kamu berharap hasilnya apa?"
[cite_start][cite: 1, 3, 4, 5]

STOP. Do not teach anything until these two are answered clearly. [cite_start]If user answers only one, ask for the missing one. [cite: 5]

PHASE 2: SEQUENTIAL TEACHING (ONE STEP AT A TIME)
ONLY AFTER user answers the 2 opening questions:
1. [cite_start]Acknowledge briefly ("Baik, jadi saya akan mulai ajarin kamu langkah-langkah."). [cite: 6, 7]
2. [cite_start]Explain STEP 1 from the KB. [cite: 6]
3. STOP. Do not explain Step 2 yet.

PHASE 3: INTERACTIVE DATA & "DO IT FOR YOU"
[cite_start]At the end of every step explanation, you MUST ask for user data to make it contextual. [cite: 8]

**Condition A: User has data**
- [cite_start]If user provides data (e.g., "HPP saya Rp10.000"), record it mentally. [cite: 16]

**Condition B: User has NO data / Needs Help**
- [cite_start]If the step requires output (calculation, list, copywriting), OFFER TO DO IT. [cite: 9]
- [cite_start]Example: "Untuk langkah ini, berapa harga modal kamu? Atau kamu mau saya bantu hitung harga jualnya?" [cite: 14]
- [cite_start]If user accepts help: Calculate/Create it immediately using formulas/rules in KB. [cite: 18]
- [cite_start]Example Output: "HPP Rp10.000 + margin 50% (Rp5.000) = harga jual minimal Rp15.000." [cite: 19]

**Condition C: No Data Needed**
- [cite_start]If the step is purely concept, close with: "Apakah kamu memiliki pertanyaan?" [cite: 22, 28]

PHASE 4: DEFLECTION (FOCUS KEEPER)
If user asks about something unrelated to the current step (e.g. asking about Ads when discussing Product):
- [cite_start]BLOCK IT politely. [cite: 29]
- [cite_start]Say: "Sabar ya, pertanyaan kamu itu penting dan akan kita bahas nanti. Tapi biar hasilnya akurat, kita bahas satu per satu dulu." [cite: 31]
- [cite_start]Redirect back to the current step. [cite: 30]

PHASE 5: CLOSING
[cite_start]Only when all steps are done, ask: "Oke, kita sudah selesai membahas semua langkahnya. Menurut kamu, masalah utama kamu sudah terjawab? Atau masih ada pertanyaan lain?" [cite: 32, 35, 36]

========================================================
NEGATIVE CONSTRAINTS (DO NOT DO THIS):
- DO NOT start with "Halo, saya senang..." followed by a wall of text.
- DO NOT ask "Apa bisnis kamu?" in the first turn. ASK THE 2 MANDATORY QUESTIONS INSTEAD.
- DO NOT teach all steps at once.
- DO NOT summarize unless asked.

CONTEXT:
User Name: {request.user_first_name}
User Message: "{request.message}"
"""
    
    final_messages = [{"role": "system", "content": system_prompt}] + messages_payload
    final_messages.append({"role": "user", "content": request.message})
    
    ai_reply = ""
    try:
        completion = client.chat.completions.create(
            messages=final_messages,
            model="llama-3.3-70b-versatile", # Model yang lebih patuh instruksi
            temperature=0.1, # Suhu rendah agar sangat patuh & tidak halu
            max_tokens=1500, 
        )
        ai_reply = completion.choices[0].message.content
    except Exception as e:
        print(f"Error AI: {e}")
        ai_reply = "Maaf, sistem sedang sibuk. Mohon coba lagi."

    # Simpan Balasan AI
    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "ai", "message": ai_reply
    }).execute()

    # Video Engine Trigger (Optional)
    job_id = None
    if len(ai_reply) > 2 and mentor.get('avatar_url'):
        try:
            audio_bytes = generate_elevenlabs_audio(ai_reply)
            if audio_bytes:
                filename = f"audio/{uuid.uuid4()}.mp3"
                supabase.storage.from_("avatars").upload(filename, audio_bytes, {"content-type": "audio/mpeg"})
                audio_url = supabase.storage.from_("avatars").get_public_url(filename)
                res = supabase.table("avatar_jobs").insert({"status": "pending", "image_url": mentor['avatar_url'], "audio_url": audio_url}).execute()
                if res.data: job_id = res.data[0]['id']
        except Exception: pass

    return {"mentor": mentor['name'], "reply": ai_reply, "job_id": job_id}

# --- API LAINNYA ---
@app.get("/mentors/search")
async def search_mentors(keyword: str = None):
    query = supabase.table("mentors").select("*").eq("is_active", True)
    if keyword: query = query.or_(f"name.ilike.%{keyword}%,expertise.ilike.%{keyword}%,category.ilike.%{keyword}%")
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
    for chunk in chunks: supabase.table("mentor_docs").insert({"mentor_id": mentor_id, "content": chunk}).execute()    
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
        return {"gross_revenue": sum(t['amount'] for t in tx), "net_earnings": sum(t['net_amount'] for t in tx), "students": len(set(t['user_id'] for t in tx)), "chart_data": [{"name":k,"total":v} for k,v in sorted(chart.items())]}
    except: return {"students": 0, "gross_revenue": 0}

@app.delete("/chat/reset")
async def reset_chat_history(req: DeleteChatRequest):
    supabase.table("chat_history").delete().eq("user_id", req.user_id).eq("mentor_id", req.mentor_id).execute()
    return {"message": "Chat history deleted successfully"}

@app.delete("/educator/reset-docs")
async def reset_mentor_docs(req: DeleteDocsRequest):
    supabase.table("mentor_docs").delete().eq("mentor_id", req.mentor_id).execute()
    return {"status": "ok"}

@app.post("/payment/create")
async def create_payment(req: PaymentRequest):
    fee = req.amount * 0.1
    order_id = f"SUB-{req.user_id[:4]}-{datetime.now().strftime('%d%H%M%S')}"
    transaction = snap.create_transaction({"transaction_details": {"order_id": order_id, "gross_amount": req.amount}, "customer_details": {"user_id": req.user_id, "email": req.email, "first_name": req.first_name}})
    supabase.table("subscriptions").insert({"user_id": req.user_id, "mentor_id": req.mentor_id, "midtrans_order_id": order_id, "amount": req.amount, "net_amount": req.amount-fee, "platform_fee_amount": fee, "status": "pending"}).execute()
    return {"token": transaction['token'], "redirect_url": transaction['redirect_url']}

@app.post("/midtrans-notification")
async def midtrans_notification(n: dict):
    status = 'settlement' if n['transaction_status'] in ['capture', 'settlement'] else 'failed'
    supabase.table("subscriptions").update({"status": status}).eq("midtrans_order_id", n['order_id']).execute()
    return {"status": "ok"}

@app.post("/user/update-profile")
async def update_profile(user_id: str, full_name: str = None, avatar_url: str = None):
    data = {k: v for k, v in {"full_name": full_name, "avatar_url": avatar_url}.items() if v}
    if data: supabase.table("profiles").update(data).eq("id", user_id).execute()
    return {"status": "ok"}