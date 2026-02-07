import os
import json
import re
import uuid  # Diambil dari Kode B
import requests  # Diambil dari Kode B
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
    title="AI Mentor SaaS Platform - V19 (Full Output + Video Engine)",
    description="Backend AI Mentor V19 dengan fitur Full Output 17 Langkah & Integrasi Video Avatar."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    # --- SETUP CREDENTIALS (GABUNGAN) ---
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    
    # Setup dari Kode B (ElevenLabs)
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
    ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM") 

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = Groq(api_key=GROQ_API_KEY)
    
    snap = midtransclient.Snap(
        is_production=False, 
        server_key=os.getenv("MIDTRANS_SERVER_KEY"),
        client_key=os.getenv("MIDTRANS_CLIENT_KEY")
    )
    print("✅ System Ready: V19 (Full Output + ElevenLabs Video Engine)")
except Exception as e:
    print(f"❌ Error Setup: {e}")

# ==========================================
# 2. HELPER FUNCTIONS (DARI KODE B)
# ==========================================
def generate_elevenlabs_audio(text: str) -> bytes:
    """Mengubah teks menjadi audio binary menggunakan ElevenLabs API (Diambil dari Kode B)"""
    if not ELEVENLABS_API_KEY:
        print("⚠️ ElevenLabs API Key missing in .env!")
        return None

    # URL API ElevenLabs
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
    }
    # Batasi teks jika terlalu panjang untuk menghemat kuota/waktu render audio
    # Opsional: Bisa dipotong jika V19 menghasilkan output sangat panjang
    truncated_text = text[:1000] if len(text) > 1000 else text
    
    data = {
        "text": truncated_text, 
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }
    try:
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            return response.content
        else:
            print(f"ElevenLabs Error ({response.status_code}): {response.text}")
            return None
    except Exception as e:
        print(f"ElevenLabs Exception: {e}")
        return None

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
    return {"status": "AI Mentor SaaS Backend V19.0 (Full Output + Video Engine) is Running"}

# --- A. API AI DISCOVERY ---
@app.post("/discovery/generate-questions")
async def generate_discovery_questions(data: DiscoveryInput):
    try:
        system_prompt = "You are a backend system. Output ONLY JSON."
        user_prompt = f"""
        User Goal: "{data.user_goal}".
        Task: Create 3 follow-up multiple choice questions in INDONESIAN.
        Output JSON Format: [{{"id": 1, "question": "...", "icon": "emoji", "options": ["A", "B"]}}]
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
            {"id": 1, "question": "Fokus bisnis saat ini?", "icon": "🎯", "options": ["Marketing", "Operasional", "Keuangan"]},
            {"id": 2, "question": "Skala bisnis?", "icon": "📈", "options": ["Ide", "Rintisan", "Stabil"]},
            {"id": 3, "question": "Kendala utama?", "icon": "🚧", "options": ["Modal", "Strategi", "Tim"]}
        ]


# --- B. API UTAMA: CHAT (V19 Base + V13 Features) ---
@app.post("/chat")
async def chat_with_mentor(request: ChatRequest):
    # 1. Cek Subscription (Logika V19)
    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
    sub_check = supabase.table("subscriptions").select("*")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id)\
        .eq("status", "settlement").gte("created_at", thirty_days_ago).execute()
    is_subscribed = len(sub_check.data) > 0
    
    # 2. Cek Limit (Logika V19)
    history_count = supabase.table("chat_history").select("id", count="exact")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id).eq("sender", "user").execute()
    user_chat_count = history_count.count if history_count.count else 0
    
    if not is_subscribed and user_chat_count >= 5:
        return {"reply": "LIMIT_REACHED", "mentor": "System", "usage": user_chat_count}

    # 3. Data Mentor & PDF
    mentor_data = supabase.table("mentors").select("*").eq("id", request.mentor_id).single().execute()
    # Update fallback agar punya field avatar_url (penting untuk Kode B)
    mentor = mentor_data.data if mentor_data.data else {"name": "Mentor", "personality": "Senior Consultant", "expertise": "Bisnis", "avatar_url": None}
    
    docs = supabase.table("mentor_docs").select("content").eq("mentor_id", request.mentor_id).execute()
    knowledge_base = "\n\n".join([d['content'] for d in docs.data])

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
    # SYSTEM PROMPT V20 (UPDATED FEB 2026 LOGIC COMPLIANCE)
    # ==============================================================================
    system_prompt = f"""
ROLE & IDENTITY
You are {mentor['name']}, a senior business practitioner/mentor.
Your goal is to guide the user through the 17-step business framework from the Knowledge Base sequentially.

SOURCE OF TRUTH (KNOWLEDGE BASE):
{knowledge_base}

========================================================
LOGIC UPDATE FEB 2026 PROTOCOL (STRICT COMPLIANCE)
========================================================

PHASE 1: MANDATORY OPENING (DO NOT SKIP)
Before teaching anything, you MUST ask these two questions first. Do not proceed until the user answers BOTH.
1. "1 masalah spesifik apa yang mau kamu bahas?"
2. "Goal kamu apa / kamu berharap hasilnya apa?"
[cite: 1, 3, 4, 5]

PHASE 2: SEQUENTIAL TEACHING (STEP-BY-STEP)
Only after the user answers the opening questions, begin teaching the steps one by one.
- Start with: "Baik, jadi saya akan mulai ajarin kamu langkah-langkah." [cite: 6, 7]
- Teach ONE step at a time. Do not dump all 17 steps at once.

PHASE 3: DATA COLLECTION & OUTPUT EXECUTION (PER STEP)
For every step you teach, you MUST ask for user data to make it contextual. [cite: 8]

**RULE A: If User Has Data**
- If the user provides data (e.g., "My price is Rp20.000"), record it mentally for future context. [cite: 16]

**RULE B: If User Does Not Have Data / Needs Help (THE "DO IT FOR YOU" OFFER)**
- If the step requires a calculation or output (e.g., pricing, copywriting), OFFER TO DO IT.
- Example: "Harga jual minimal harus margin 50% dari HPP. Berapa harga jual kamu? Atau kamu mau saya bantu hitung harga jualmu?" [cite: 14]
- If user asks for help: Calculate it immediately using the formula in Knowledge Base.
  Example output: "HPP Rp10.000 + margin 50% (Rp5.000) = harga jual minimal Rp15.000." [cite: 18, 19]

**RULE C: If No Data Needed**
- If a step is purely theoretical or requires no data, close the step by asking: "Apakah kamu memiliki pertanyaan?" [cite: 22, 28]

PHASE 4: HANDLING OUT-OF-CONTEXT QUESTIONS
If the user asks a question that is NOT related to the current step being discussed:
- DO NOT answer it yet. [cite: 29]
- Politely deflect and steer back to the current step to ensure accuracy.
- Say: "Sabar ya, pertanyaan kamu itu penting dan akan kita bahas nanti. Tapi biar hasilnya akurat, kita bahas satu per satu dulu." [cite: 31]

PHASE 5: SESSION CLOSING
After all steps are completed:
- Ask if the main problem is solved.
- Example: "Oke, kita sudah selesai membahas semua langkahnya. Menurut kamu, masalah utama kamu sudah terjawab? Atau masih ada pertanyaan lain?" [cite: 32, 35, 36]

========================================================
CONTEXT & MEMORY
User Name: {request.user_first_name}
User Goal/Context: {request.business_snapshot}
Current Conversation History: See messages below.

INSTRUCTION:
- Check the conversation history.
- If this is the start, trigger PHASE 1.
- If in the middle of steps, continue to the next step (PHASE 2 & 3).
- If user asks something random, trigger PHASE 4.
- If all steps done, trigger PHASE 5.
- Keep tone professional, direct, yet warm ("Senior Mentor").
"""
    
    final_messages = [{"role": "system", "content": system_prompt}] + messages_payload
    final_messages.append({"role": "user", "content": request.message})
    
    ai_reply = ""
    try:
        # REQUEST KE LLM - MAX TOKENS DINAIKKAN KE 3500 AGAR CUKUP UNTUK 17 LANGKAH
        completion = client.chat.completions.create(
            messages=final_messages,
            model="openai/gpt-oss-120b", 
            temperature=0.3, 
            max_tokens=3500, # UPDATED: Token diperbesar untuk jawaban panjang
        )
        ai_reply = completion.choices[0].message.content

    except Exception as e:
        print(f"Error AI: {e}")
        ai_reply = "Hmm, sepertinya sinyal saya terganggu. Boleh diulang bagian terakhir?"

    # Simpan Balasan AI
    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "ai", "message": ai_reply
    }).execute()

    # =========================================================
    # 🚀 FITUR TAMBAHAN DARI KODE B (VIDEO ENGINE TRIGGER)
    # =========================================================
    job_id = None
    # Syarat: Balasan ada isinya DAN Mentor punya Avatar URL di database
    if len(ai_reply) > 2 and mentor.get('avatar_url'):
        try:
            print("🔊 Generating Audio via ElevenLabs...")
            # Panggil Helper Function dari Kode B
            audio_bytes = generate_elevenlabs_audio(ai_reply)
            
            if audio_bytes:
                # A. Upload Audio ke Supabase Storage
                filename = f"audio/{uuid.uuid4()}.mp3"
                supabase.storage.from_("avatars").upload(
                    path=filename, 
                    file=audio_bytes, 
                    file_options={"content-type": "audio/mpeg"}
                )
                
                # B. Dapatkan URL Public Audio
                audio_url = supabase.storage.from_("avatars").get_public_url(filename)
                
                # C. Masukkan Job ke Antrian (Tabel avatar_jobs)
                # Sesuai logika Kode B, kita insert job agar worker video bisa memprosesnya
                job_data = {
                    "status": "pending",
                    "image_url": mentor['avatar_url'],
                    "audio_url": audio_url
                }
                res = supabase.table("avatar_jobs").insert(job_data).execute()
                
                # Cek hasil insert
                if res.data and len(res.data) > 0:
                    job_id = res.data[0]['id']
                    print(f"⚡ Job Video Created: {job_id}")
                else:
                    print("⚠️ Job Created but no ID returned (Check DB)")

        except Exception as e:
            print(f"⚠️ Video Generation Error: {e}")

    # Return sudah dimodifikasi untuk menyertakan job_id (penting untuk frontend)
    return {"mentor": mentor['name'], "reply": ai_reply, "job_id": job_id}


# --- C. API PENDUKUNG ---

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
    supabase.table("mentors").update({
        "category": req.category, 
        "price_per_month": req.price, 
        "bank_details": bank
    }).eq("id", req.mentor_id).execute()
    return {"status": "ok"}

@app.post("/educator/upload")
async def upload_material(mentor_id: int, file: UploadFile = File(...)):
    try:
        text = "".join([page.extract_text() for page in pypdf.PdfReader(file.file).pages])
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            supabase.table("mentor_docs").insert({"mentor_id": mentor_id, "content": chunk}).execute()    
        return {"status": "ok", "chunks_count": len(chunks)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload Error: {str(e)}")

@app.post("/educator/payout")
async def request_payout(req: PayoutRequestModel):
    supabase.table("payouts").insert({
        "mentor_id": req.mentor_id, "amount": req.amount, "status": "pending", 
        "bank_info": req.bank_info, "created_at": "now()"
    }).execute()
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
        for t in tx: 
            date_key = t['created_at'][:10]
            chart[date_key] = chart.get(date_key, 0) + t['net_amount']
        
        revs = supabase.table("reviews").select("rating").eq("mentor_id", mid).execute().data or []
        avg = sum([r['rating'] for r in revs]) / len(revs) if revs else 0
        ai_qual = int((avg/5)*100) if avg > 0 else 98

        return {
            "gross_revenue": sum(t['amount'] for t in tx), 
            "net_earnings": sum(t['net_amount'] for t in tx),
            "students": len(set(t['user_id'] for t in tx)), 
            "ai_quality": ai_qual,
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

@app.delete("/chat/reset")
async def reset_chat_history(req: DeleteChatRequest):
    try:
        supabase.table("chat_history").delete().eq("user_id", req.user_id).eq("mentor_id", req.mentor_id).execute()
        return {"message": "Chat history deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.delete("/educator/reset-docs")
async def reset_mentor_docs(req: DeleteDocsRequest):
    try:
        supabase.table("mentor_docs").delete().eq("mentor_id", req.mentor_id).execute()
        return {"status": "ok", "message": "All documents deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/payment/create")
async def create_payment(req: PaymentRequest):
    fee = req.amount * 0.1
    order_id = f"SUB-{req.user_id[:4]}-{datetime.now().strftime('%d%H%M%S')}"
    transaction = snap.create_transaction({
        "transaction_details": {"order_id": order_id, "gross_amount": req.amount},
        "customer_details": {"user_id": req.user_id, "email": req.email, "first_name": req.first_name}
    })
    supabase.table("subscriptions").insert({
        "user_id": req.user_id, "mentor_id": req.mentor_id, "midtrans_order_id": order_id,
        "amount": req.amount, "net_amount": req.amount-fee, "platform_fee_amount": fee, "status": "pending"
    }).execute()
    return {"token": transaction['token'], "redirect_url": transaction['redirect_url']}

@app.post("/midtrans-notification")
async def midtrans_notification(n: dict):
    try:
        status = 'pending'
        if n['transaction_status'] in ['capture', 'settlement']: status = 'settlement'
        elif n['transaction_status'] in ['cancel', 'deny', 'expire']: status = 'failed'
        supabase.table("subscriptions").update({"status": status}).eq("midtrans_order_id", n['order_id']).execute()
        return {"status": "ok"}
    except: return {"status": "error"}

@app.post("/user/update-profile")
async def update_profile(user_id: str, full_name: str = None, avatar_url: str = None):
    data = {k: v for k, v in {"full_name": full_name, "avatar_url": avatar_url}.items() if v}
    if data: supabase.table("profiles").update(data).eq("id", user_id).execute()
    return {"status": "ok"}