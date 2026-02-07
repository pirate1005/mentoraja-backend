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
    title="AI Mentor SaaS Platform - V34 (Strict Step Enforcer)",
    description="Backend AI Mentor V34. Enforces strict step-by-step progression. Prevents jumping ahead."
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
    print("✅ System Ready: V34 (Strict Step Enforcer)")
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
    user_first_name: str = "" 
    business_snapshot: str = "Belum ada data"

class PaymentRequest(BaseModel):
    user_id: str
    mentor_id: int
    duration_hours: int = 1  # Default 1 jam
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

class FavoriteRequest(BaseModel):
    user_id: str
    mentor_id: int

# ==========================================
# 4. API ENDPOINTS
# ==========================================

@app.get("/")
def home():
    return {"status": "AI Mentor Backend V34 Active"}

# --- API CHAT UTAMA (V34 STRICT ENFORCER) ---
@app.post("/chat")
async def chat_with_mentor(request: ChatRequest):
    # 1. Cek Subscription
    now_str = datetime.now().isoformat()
    
    # Cari langganan yang status='settlement' DAN expires_at-nya masih di masa depan
    sub_check = supabase.table("subscriptions").select("*")\
        .eq("user_id", request.user_id)\
        .eq("mentor_id", request.mentor_id)\
        .eq("status", "settlement")\
        .gt("expires_at", now_str)\
        .execute()
        
    is_subscribed = len(sub_check.data) > 0
    
    # 2. Cek Limit
    history_count = supabase.table("chat_history").select("id", count="exact")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id).eq("sender", "user").execute()
    user_chat_count = history_count.count if history_count.count else 0
    
    if not is_subscribed and user_chat_count >= 5:
        return {"reply": "LIMIT_REACHED", "mentor": "System", "usage": user_chat_count}

    # 3. Data Mentor & KB
    mentor_data = supabase.table("mentors").select("*").eq("id", request.mentor_id).single().execute()
    mentor = mentor_data.data if mentor_data.data else {"name": "Mentor", "personality": "Expert Advisor", "expertise": "General", "avatar_url": None}
    
    docs = supabase.table("mentor_docs").select("content").eq("mentor_id", request.mentor_id).execute()
    knowledge_base = "\n\n".join([d['content'] for d in docs.data])
    
    if not knowledge_base:
        knowledge_base = "Guidelines: 1. Ask Problem & Goal. 2. Teach steps sequentially."

    # 4. Simpan Chat User
    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "user", "message": request.message
    }).execute()

    # 5. Fetch History
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
    # SYSTEM PROMPT V34 (THE STRICT STEP ENFORCER)
    # ==============================================================================
    
    user_name_instruction = ""
    if request.user_first_name:
        user_name_instruction = f"Address the user as '{request.user_first_name}'."
    else:
        user_name_instruction = "Address the user directly. Do NOT use 'Teman' or 'Kawan'."

    system_prompt = f"""
YOU ARE {mentor['name']}, AN EXPERT IN {mentor.get('expertise', 'Fields')}.
YOUR PERSONALITY IS: {mentor.get('personality', 'Strict & Professional Consultant')}.

SOURCE OF TRUTH (KNOWLEDGE BASE):
{knowledge_base}

==================================================
!!! CRITICAL EXECUTION RULES !!!

### PROTOCOL 1: THE IRON GATEKEEPER (MANDATORY START)
Check history. Has the user answered these 2 questions?
1. "Masalah spesifik apa yang mau dibahas?"
2. "Goal/Harapan kamu apa?"

IF "NO" -> STOP TEACHING. ASK THE 2 QUESTIONS IMMEDIATELY.
IF "YES" -> Proceed to Protocol 2.

### PROTOCOL 2: THE ELECTRIC FENCE (ANTI-JUMPING LOGIC)
You must TRACK the "Current Step" based on conversation history.
- If we are talking about "Langkah 1" (Ide/Riset), you MUST NOT discuss "Langkah 13" (Promosi), "Langkah 4" (Harga), or "Langkah 17" (Investor).
- **IF USER ASKS TO JUMP (e.g., "Gimana cara promosi?" while at Step 1):**
  - **REJECT THE REQUEST.**
  - **SAY:** "Pertanyaan bagus tentang promosi. Tapi itu ada di Langkah 13. Saat ini kita masih di Langkah 1. Kita harus selesaikan ini dulu agar fondasi bisnis kuat. Mari kembali ke topik..."
  - **REDIRECT** back to the Current Step immediately.

**EXCEPTION:** Only allow jumping if the user EXPLICITLY says "Saya sudah selesai langkah 1-12" (Providing context). If they just ask a random question, BLOCK IT.

### PROTOCOL 3: STEP-SPECIFIC FORMATS
- **Langkah 7 (Bio):** Output MUST be <15 sentences AND include a CTA.
- **Langkah 17 (Pitch Deck):** Output ONLY Slides 1-5. Then STOP and ask: "Mau lanjut ke Slide 6-10?".

### PROTOCOL 4: CONSULTANT DIAGNOSIS
- Explain step -> Ask if user has data.
- Do not lecture endlessly. Make it a conversation.

ADDRESSING:
- {user_name_instruction}

CURRENT USER MESSAGE:
"{request.message}"
"""
    
    final_messages = [{"role": "system", "content": system_prompt}] + messages_payload
    final_messages.append({"role": "user", "content": request.message})
    
    ai_reply = ""
    try:
        completion = client.chat.completions.create(
            messages=final_messages,
            model="llama-3.3-70b-versatile",
            temperature=0.0, 
            max_tokens=1000, 
        )
        ai_reply = completion.choices[0].message.content
    except Exception as e:
        print(f"Error AI: {e}")
        ai_reply = "Maaf, sistem sedang sibuk. Mohon coba lagi."

    # Simpan Balasan AI
    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "ai", "message": ai_reply
    }).execute()

    # Video Engine Trigger
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

# --- API LAINNYA (SAMA SEPERTI SEBELUMNYA) ---
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
    # 1. Ambil harga per jam mentor dari database
    mentor = supabase.table("mentors").select("price_per_month").eq("id", req.mentor_id).single().execute()
    
    # Asumsi: kolom 'price_per_month' di DB sekarang dianggap sebagai 'price_per_hour'
    price_per_hour = mentor.data['price_per_month'] 
    
    # 2. Hitung Total Bayar (Harga x Jam)
    gross_amount = price_per_hour * req.duration_hours
    
    # 3. Hitung Fee Platform (10%)
    fee = int(gross_amount * 0.1)
    net_amount = gross_amount - fee
    
    # 4. Buat Order ID Unik (Tambahkan durasi di metadata jika perlu)
    # Format: SUB-{UserID}-{Time}-{Duration}
    order_id = f"SUB-{req.user_id[:4]}-{datetime.now().strftime('%d%H%M%S')}-{req.duration_hours}"

    # 5. Request ke Midtrans
    transaction_params = {
        "transaction_details": {
            "order_id": order_id, 
            "gross_amount": gross_amount
        },
        "customer_details": {
            "first_name": req.first_name, 
            "email": req.email
        },
        # PENTING: Kirim durasi via custom_field agar webhook tahu berapa jam yg dibeli
        "custom_field1": str(req.duration_hours), 
        "custom_field2": req.user_id,
        "custom_field3": str(req.mentor_id)
    }
    
    transaction = snap.create_transaction(transaction_params)
    
    # 6. Simpan ke Database (Status: Pending)
    # Kita simpan dulu durasi/hours yg dibeli, tapi start_date & expires_at nanti pas settlement
    supabase.table("subscriptions").insert({
        "user_id": req.user_id, 
        "mentor_id": req.mentor_id, 
        "midtrans_order_id": order_id, 
        "amount": gross_amount, 
        "net_amount": net_amount, 
        "platform_fee_amount": fee, 
        "status": "pending"
        # Note: start_date & expires_at masih NULL
    }).execute()
    
    return {"token": transaction['token'], "redirect_url": transaction['redirect_url']}

@app.post("/midtrans-notification")
async def midtrans_notification(n: dict):
    transaction_status = n.get('transaction_status')
    order_id = n.get('order_id')
    
    if transaction_status in ['capture', 'settlement']:
        # 1. Tentukan Waktu Mulai (Sekarang)
        start_time = datetime.now()
        
        # 2. Ambil Durasi Pembelian
        # Cara A: Parsing dari Order ID (jika formatnya SUB-USER-TIME-DURATION)
        # Cara B: Ambil default 1 jam (jika logic parsing ribet)
        try:
            parts = order_id.split('-')
            duration_hours = int(parts[-1]) # Mengambil angka terakhir dari Order ID
        except:
            duration_hours = 1 # Default fallback
            
        # 3. Hitung Waktu Habis (Start + Duration)
        expiry_time = start_time + timedelta(hours=duration_hours)
        
        # 4. Update Database
        supabase.table("subscriptions").update({
            "status": "settlement",
            "start_date": start_time.isoformat(),
            "expires_at": expiry_time.isoformat() # <--- PENTING!
        }).eq("midtrans_order_id", order_id).execute()
        
    elif transaction_status in ['deny', 'cancel', 'expire']:
        supabase.table("subscriptions").update({
            "status": "failed"
        }).eq("midtrans_order_id", order_id).execute()
        
    return {"status": "ok"}

@app.post("/user/update-profile")
async def update_profile(user_id: str, full_name: str = None, avatar_url: str = None):
    data = {k: v for k, v in {"full_name": full_name, "avatar_url": avatar_url}.items() if v}
    if data: supabase.table("profiles").update(data).eq("id", user_id).execute()
    return {"status": "ok"}

@app.post("/favorites/toggle")
async def toggle_favorite(req: FavoriteRequest):
    # Cek apakah sudah ada?
    existing = supabase.table("favorites").select("*")\
        .eq("user_id", req.user_id).eq("mentor_id", req.mentor_id).execute()
    
    if existing.data:
        # Jika ada -> Hapus (Unlike)
        supabase.table("favorites").delete()\
            .eq("user_id", req.user_id).eq("mentor_id", req.mentor_id).execute()
        return {"status": "removed"}
    else:
        # Jika belum -> Tambah (Like)
        supabase.table("favorites").insert({
            "user_id": req.user_id, 
            "mentor_id": req.mentor_id
        }).execute()
        return {"status": "added"}

@app.get("/favorites/{user_id}")
async def get_user_favorites(user_id: str):
    # Ambil data favorites sekaligus data mentor-nya (Join)
    # Syntax select: favorites(*, mentors(*))
    data = supabase.table("favorites").select("mentor_id, mentors(*)")\
        .eq("user_id", user_id).execute()
        
    # Rapikan format return agar hanya list mentor
    result = [item['mentors'] for item in data.data if item['mentors']]
    return result