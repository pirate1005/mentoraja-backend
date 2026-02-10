import os
import json
import re
import uuid
import requests
import base64
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Header, Form
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
    title="AI Mentor SaaS Platform - V36 (Dynamic Voice Cloning)",
    description="Backend AI Mentor V36. Supports individual voice cloning per mentor."
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
    # Default Voice ID (jika mentor belum punya suara sendiri)
    DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM") 

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = Groq(api_key=GROQ_API_KEY)
    
    snap = midtransclient.Snap(
        is_production=False, 
        server_key=os.getenv("MIDTRANS_SERVER_KEY"),
        client_key=os.getenv("MIDTRANS_CLIENT_KEY")
    )
    print("✅ System Ready: V36 (Dynamic Voice Cloning)")
except Exception as e:
    print(f"❌ Error Setup: {e}")

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

# [MODIFIED] Menerima voice_id dinamis
def generate_elevenlabs_audio(text: str, voice_id: str = None) -> bytes:
    if not ELEVENLABS_API_KEY: 
        print("❌ ElevenLabs API Key missing")
        return None
    
    # Gunakan voice_id mentor jika ada, kalau tidak pakai default
    target_voice_id = voice_id if voice_id else DEFAULT_VOICE_ID
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{target_voice_id}"
    
    headers = {
        "Accept": "audio/mpeg", 
        "Content-Type": "application/json", 
        "xi-api-key": ELEVENLABS_API_KEY
    }
    
    data = {
        "text": text[:1000], # Batasi karakter biar hemat kuota
        "model_id": "eleven_multilingual_v2", 
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }
    
    try:
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            return response.content
        else:
            print(f"❌ ElevenLabs Error ({target_voice_id}): {response.text}")
            return None
    except Exception as e: 
        print(f"❌ ElevenLabs Exception: {e}")
        return None

# [NEW] Fungsi untuk clone suara via API ElevenLabs
def clone_voice_elevenlabs(name: str, file_path: str) -> str:
    if not ELEVENLABS_API_KEY: return None
    
    url = "https://api.elevenlabs.io/v1/voices/add"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    
    # Multipart form data
    try:
        with open(file_path, "rb") as f:
            files = {
                "files": (os.path.basename(file_path), f, "audio/mpeg")
            }
            data = {
                "name": f"Mentor-{name}-{uuid.uuid4().hex[:6]}", # Nama unik
                "description": f"Voice clone for mentor {name}"
            }
            response = requests.post(url, headers=headers, data=data, files=files)
            
            if response.status_code == 200:
                return response.json().get("voice_id")
            else:
                print(f"❌ Clone Error: {response.text}")
                return None
    except Exception as e:
        print(f"❌ Clone Exception: {e}")
        return None

def analyze_chat_phase(history: List[dict]) -> dict:
    """
    Menganalisa history untuk menentukan User ada di Fase apa.
    Mengembalikan dict berisi instruksi khusus untuk AI.
    """
    user_messages = [m['message'] for m in history if m['sender'] == 'user']
    ai_messages = [m['message'] for m in history if m['sender'] == 'ai']
    
    # --- FASE 1: OPENING WAJIB (Logic PDF Poin 1) ---
    if len(user_messages) < 2:
        return {
            "phase": "OPENING",
            "instruction": """
            STATUS: FASE OPENING (Awal Sesi).
            TUGAS UTAMA: Kamu WAJIB mendapatkan jawaban untuk 2 pertanyaan ini sebelum lanjut:
            1. "1 masalah spesifik apa yang mau kamu bahas?"
            2. "Goal kamu apa / kamu berharap hasilnya apa?"
            
            JANGAN MENGAJAR APAPUN DULU. Fokus hanya pada mendapatkan 2 jawaban ini.
            Jika user baru menjawab satu, minta yang satunya lagi.
            """
        }
    
    # --- FASE 2: MENTORING (Logic PDF Poin 2, 3, 4, 5) ---
    else:
        context_summary = f"User Problem: {user_messages[0] if user_messages else 'Unknown'}. User Goal: {user_messages[1] if len(user_messages)>1 else 'Unknown'}."
        
        return {
            "phase": "MENTORING",
            "instruction": f"""
            STATUS: FASE MENTORING (Step-by-Step Teaching).
            CONTEXT: {context_summary}
            
            ATURAN FATAL (JANGAN DILANGGAR):
            1. **DILARANG BERTANYA LAGI** "Apa masalahmu?" atau "Apa goalmu?". User SUDAH menjawabnya. Anggap kamu sudah tahu.
            2. Mulailah mengajar langkah demi langkah (Step 1, lalu Step 2, dst) sesuai KNOWLEDGE BASE.
            3. **JANGAN SKIP LANGKAH.** Ajarkan SATU langkah, lalu minta data/konfirmasi user, baru lanjut ke langkah berikutnya.
            4. Jika user bertanya hal di luar langkah saat ini, TOLAK dengan sopan: "Sabar ya, pertanyaan kamu itu penting dan akan kita bahas nanti. Tapi biar hasilnya akurat, kita bahas satu per satu dulu." (Sesuai Logic Poin 5).
            5. Di setiap langkah, minta DATA dari user. Jika user tidak punya data, tawarkan bantuan hitung (Sesuai Logic Poin 3).
            """
        }

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

class PayoutRequestModel(BaseModel):
    mentor_id: int
    amount: int
    bank_info: str 

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
    return {"status": "AI Mentor Backend V36 Active"}

# --- API CHAT UTAMA ---
@app.post("/chat")
async def chat_with_mentor(request: ChatRequest):
    # 1. Cek Subscription
    now_str = datetime.now().isoformat()
    sub_check = supabase.table("subscriptions").select("*")\
        .eq("user_id", request.user_id)\
        .eq("mentor_id", request.mentor_id)\
        .eq("status", "settlement")\
        .gt("expires_at", now_str)\
        .execute()
    is_subscribed = len(sub_check.data) > 0
    
    # 2. Cek Limit
    # history_count = supabase.table("chat_history").select("id", count="exact")\
    #    .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id).eq("sender", "user").execute()
    # user_chat_count = history_count.count if history_count.count else 0
    # if not is_subscribed and user_chat_count >= 5:
    #    return {"reply": "LIMIT_REACHED", "mentor": "System", "usage": user_chat_count}

    # 3. Data Mentor & KB
    mentor_data = supabase.table("mentors").select("*").eq("id", request.mentor_id).single().execute()
    # [MODIFIED] Ambil juga field 'elevenlabs_voice_id'
    mentor = mentor_data.data if mentor_data.data else {"name": "Mentor", "expertise": "General", "avatar_url": None, "elevenlabs_voice_id": None}
    
    docs = supabase.table("mentor_docs").select("content").eq("mentor_id", request.mentor_id).execute()
    knowledge_base = "\n\n".join([d['content'] for d in docs.data])

    # 4. Simpan Chat User
    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "user", "message": request.message
    }).execute()

    # 5. Fetch History
    past_chats_raw = supabase.table("chat_history").select("sender, message")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id)\
        .order("created_at", desc=True).limit(20).execute().data 
    past_chats_raw.reverse()
    
    # --- LOGIC PENGENDALI ---
    chat_state = analyze_chat_phase(past_chats_raw)
    
    messages_payload = []
    for chat in past_chats_raw:
        role = "user" if chat['sender'] == "user" else "assistant"
        if chat['message'] != request.message: 
            messages_payload.append({"role": role, "content": chat['message']})

    # PROMPT LOGIC
    user_name_instruction = f"Panggil user dengan nama '{request.user_first_name}'." if request.user_first_name else "Panggil user dengan sopan."

    system_prompt = f"""
    ANDA ADALAH {mentor['name']}, AHLI DI BIDANG {mentor.get('expertise', 'Bisnis')}.
    KARAKTER: {mentor.get('personality', 'Profesional, Tegas namun Membantu')}.
    BAHASA: Indonesia (Natural & Conversational).

    [MATERI MENTORING / KNOWLEDGE BASE]
    {knowledge_base[:20000]} 
    
    ==================================================
    [INSTRUKSI KHUSUS BERDASARKAN STATUS CHAT SAAT INI]
    {chat_state['instruction']}
    
    ==================================================
    [ATURAN UMUM]
    1. Jawablah dengan singkat, padat, dan "punchy". Jangan bertele-tele.
    2. Fokus SATU langkah per satu waktu. Jangan menumpuk informasi.
    3. Jika masuk tahap meminta data, gunakan format tanya yang jelas.
    
    {user_name_instruction}
    """
    
    final_messages = [{"role": "system", "content": system_prompt}] + messages_payload
    final_messages.append({"role": "user", "content": request.message})
    
    ai_reply = ""
    try:
        completion = client.chat.completions.create(
            messages=final_messages,
            model="llama-3.3-70b-versatile",
            temperature=0.2,
            max_tokens=800, 
        )
        ai_reply = completion.choices[0].message.content
    except Exception as e:
        print(f"Error AI: {e}")
        ai_reply = "Maaf, saya sedang memproses data Anda. Bisa diulangi?"

    # Simpan Balasan AI
    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "ai", "message": ai_reply
    }).execute()

    # ==============================================================================
    # 🚀 VIDEO & VOICE ENGINE (DYNAMIC VOICE PER MENTOR)
    # ==============================================================================
    audio_base64 = None
    video_base64 = None
    
    try:
        # [MODIFIED] Gunakan Voice ID khusus mentor jika ada
        mentor_voice_id = mentor.get('elevenlabs_voice_id')
        
        # Panggil fungsi generate dengan voice_id spesifik
        audio_bytes = generate_elevenlabs_audio(ai_reply, voice_id=mentor_voice_id) 
        
        if audio_bytes:
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
    except Exception as e:
        print(f"Audio Generation Error: {e}")

    # 2. GENERATE VIDEO (Kirim ke Colab)
    # GANTI URL INI DENGAN URL BARU DARI COLAB ANDA
    COLAB_API_URL = "https://0c7b-35-185-184-241.ngrok-free.app" 
    
    if audio_base64 and COLAB_API_URL and mentor.get('avatar_url'):
        try:
            # print("⏳ Mengirim request ke Colab...")
            colab_payload = {
                "audio_base64": audio_base64,
                "image_url": mentor['avatar_url']
            }
            
            response = requests.post(
                f"{COLAB_API_URL}/generate_video", 
                json=colab_payload, 
                timeout=120 
            )
            
            if response.status_code == 200:
                colab_data = response.json()
                if colab_data.get("status") == "success":
                    video_base64 = colab_data.get("video_base64")
                    # print("✅ Video berhasil dibuat oleh Colab!")
                else:
                    print(f"❌ Colab Error Message: {colab_data.get('message')}")
            else:
                print(f"❌ Gagal connect Colab: Status {response.status_code}")
                
        except Exception as e:
            print(f"❌ Error request ke Colab: {e}")

    return {
        "mentor": mentor['name'], 
        "reply": ai_reply, 
        "audio": audio_base64, 
        "video": video_base64
    }

# ==========================================
# 🚀 NEW ENDPOINT: UPLOAD VOICE SAMPLE (CLONING)
# ==========================================
@app.post("/educator/upload-voice")
async def upload_voice_sample(mentor_id: int, file: UploadFile = File(...)):
    """
    Mentor mengupload file audio (MP3/WAV).
    Sistem mengirimnya ke ElevenLabs untuk di-clone.
    Voice ID baru disimpan ke tabel mentors.
    """
    try:
        # 1. Validasi file
        if not file.filename.endswith(('.mp3', '.wav', '.m4a')):
             raise HTTPException(status_code=400, detail="Format audio harus MP3, WAV, atau M4A")

        # 2. Ambil nama mentor untuk label voice
        mentor_data = supabase.table("mentors").select("name").eq("id", mentor_id).single().execute()
        if not mentor_data.data:
             raise HTTPException(status_code=404, detail="Mentor tidak ditemukan")
        mentor_name = mentor_data.data['name']

        # 3. Simpan file sementara di server
        temp_filename = f"temp_voice_{uuid.uuid4()}.mp3"
        with open(temp_filename, "wb") as buffer:
            buffer.write(await file.read())

        # 4. Kirim ke ElevenLabs untuk Cloning
        print(f"⏳ Cloning voice for {mentor_name}...")
        new_voice_id = clone_voice_elevenlabs(mentor_name, temp_filename)
        
        # Hapus file temp
        os.remove(temp_filename)

        if not new_voice_id:
             raise HTTPException(status_code=500, detail="Gagal meng-clone suara di ElevenLabs")

        # 5. Update Database Mentor dengan Voice ID baru
        # Pastikan kolom 'elevenlabs_voice_id' sudah ada di tabel mentors!
        supabase.table("mentors").update({
            "elevenlabs_voice_id": new_voice_id,
            "voice_sample_url": "Uploaded to ElevenLabs" # Opsional: Tandai status
        }).eq("id", mentor_id).execute()

        print(f"✅ Voice Cloned Successfully! ID: {new_voice_id}")
        return {"status": "success", "voice_id": new_voice_id, "message": "Suara berhasil dikloning dan siap digunakan."}

    except Exception as e:
        print(f"❌ Upload Voice Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- API LAINNYA (TIDAK BERUBAH) ---
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
    mentor = supabase.table("mentors").select("price_per_month").eq("id", req.mentor_id).single().execute()
    price_per_hour = mentor.data['price_per_month'] 
    gross_amount = price_per_hour * req.duration_hours
    fee = int(gross_amount * 0.1)
    net_amount = gross_amount - fee
    order_id = f"SUB-{req.user_id[:4]}-{datetime.now().strftime('%d%H%M%S')}-{req.duration_hours}"

    transaction_params = {
        "transaction_details": {"order_id": order_id, "gross_amount": gross_amount},
        "customer_details": {"first_name": req.first_name, "email": req.email},
        "custom_field1": str(req.duration_hours), 
        "custom_field2": req.user_id,
        "custom_field3": str(req.mentor_id)
    }
    
    transaction = snap.create_transaction(transaction_params)
    
    supabase.table("subscriptions").insert({
        "user_id": req.user_id, 
        "mentor_id": req.mentor_id, 
        "midtrans_order_id": order_id, 
        "amount": gross_amount, 
        "net_amount": net_amount, 
        "platform_fee_amount": fee, 
        "status": "pending"
    }).execute()
    
    return {"token": transaction['token'], "redirect_url": transaction['redirect_url']}

@app.post("/midtrans-notification")
async def midtrans_notification(n: dict):
    transaction_status = n.get('transaction_status')
    order_id = n.get('order_id')
    
    if transaction_status in ['capture', 'settlement']:
        start_time = datetime.now()
        try:
            parts = order_id.split('-')
            duration_hours = int(parts[-1])
        except:
            duration_hours = 1
            
        expiry_time = start_time + timedelta(hours=duration_hours)
        
        supabase.table("subscriptions").update({
            "status": "settlement",
            "start_date": start_time.isoformat(),
            "expires_at": expiry_time.isoformat()
        }).eq("midtrans_order_id", order_id).execute()
        
    elif transaction_status in ['deny', 'cancel', 'expire']:
        supabase.table("subscriptions").update({"status": "failed"}).eq("midtrans_order_id", order_id).execute()
        
    return {"status": "ok"}

@app.post("/user/update-profile")
async def update_profile(user_id: str, full_name: str = None, avatar_url: str = None):
    data = {k: v for k, v in {"full_name": full_name, "avatar_url": avatar_url}.items() if v}
    if data: supabase.table("profiles").update(data).eq("id", user_id).execute()
    return {"status": "ok"}

@app.post("/favorites/toggle")
async def toggle_favorite(req: FavoriteRequest, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401, detail="Missing Token")
    token = authorization.split(" ")[1]
    user_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    user_supabase.postgrest.auth(token) 

    try:
        existing = user_supabase.table("favorites").select("*").eq("user_id", req.user_id).eq("mentor_id", req.mentor_id).execute()
        if existing.data:
            user_supabase.table("favorites").delete().eq("user_id", req.user_id).eq("mentor_id", req.mentor_id).execute()
            return {"status": "removed"}
        else:
            user_supabase.table("favorites").insert({"user_id": req.user_id, "mentor_id": req.mentor_id}).execute()
            return {"status": "added"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/favorites/{user_id}")
async def get_user_favorites(user_id: str, authorization: str = Header(None)):
    if not authorization: return []
    try:
        token = authorization.split(" ")[1]
        user_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        user_supabase.postgrest.auth(token)
        data = user_supabase.table("favorites").select("mentor_id, mentors(*)").eq("user_id", user_id).execute()
        result = [item['mentors'] for item in data.data if item.get('mentors')]
        return result
    except Exception as e:
        print(f"Error Fetch Favorites: {e}")
        return []