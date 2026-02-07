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
    title="AI Mentor SaaS Platform - V31 (Strict Scope & Shield)",
    description="Backend AI Mentor V31. Adds Anti-OOT (Crypto/Politics) and Anti-Skip Logic."
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
    print("✅ System Ready: V31 (Strict Scope & Shield)")
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
    return {"status": "AI Mentor Backend V31 Active"}

# --- API CHAT UTAMA (V31 STRICT SCOPE) ---
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
    # SYSTEM PROMPT V31 (THE SNIPER SCOPE & SHIELD)
    # ==============================================================================
    
    user_name_instruction = ""
    if request.user_first_name:
        user_name_instruction = f"Address the user as '{request.user_first_name}'."
    else:
        user_name_instruction = "Address the user directly. Do NOT use 'Teman' or 'Kawan'."

    system_prompt = f"""
YOU ARE {mentor['name']}, AN EXPERT IN {mentor.get('expertise', 'Fields')}.
YOUR PERSONALITY IS: {mentor.get('personality', 'Professional Consultant')}.

SOURCE OF TRUTH (KNOWLEDGE BASE):
{knowledge_base}

==================================================
PROTOCOL 1: THE SHIELD (OUT OF DOMAIN BLOCKER)
You must STRICTLY STICK to the Knowledge Base (PDF).
IF the user asks about topics NOT in the Knowledge Base (e.g., Crypto, Politics, Health, Coding, Romance, General Chat):
   - REJECT the question politely.
   - SAY: "Maaf, keahlian saya spesifik membantu bisnis Anda sesuai panduan. Topik [topic] di luar kapasitas saya. Mari kembali ke langkah bisnis kita."
   - DO NOT answer the OOT question, even if you know the answer.

PROTOCOL 2: THE ANCHOR (ANTI-SKIP SEQUENCE)
You must detect "Future Steps".
IF the user asks about a step far ahead (e.g., asking about "Promosi/Langkah 13" while you are still at "Langkah 2"):
   - DETECT: "User is asking about Step X, but we are at Step Y."
   - BLOCK: "Pertanyaan bagus. Tapi itu materi Langkah X. Agar bisnisnya kuat, kita harus selesaikan Langkah Y dulu."
   - REDIRECT: Go back to the Current Step.

PROTOCOL 3: THE IRON GATEKEEPER
CHECK: Did the user ALREADY answer these 2 mandatory questions in history?
1. "1 masalah spesifik apa yang mau kamu bahas?"
2. "Goal kamu apa?"
IF "NO" -> STOP & ASK THEM. DO NOT TEACH YET.

PROTOCOL 4: CONSULTANT DIAGNOSIS
- When teaching a step, explain briefly -> THEN ASK if user has data.
- Example: "Langkah 1 adalah X. Apakah kamu sudah punya datanya, atau mau saya bantu?"

ADDRESSING:
- {user_name_instruction}

CURRENT CONTEXT ANALYSIS:
User Message: "{request.message}"
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
# ...

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