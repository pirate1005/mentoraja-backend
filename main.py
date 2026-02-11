import os
import json
import re
import uuid
import base64
import requests
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
from groq import Groq
import midtransclient
import pypdf
import edge_tts

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
async def generate_edge_tts_audio(text: str) -> bytes:
    try:
        # Pembersihan teks dari simbol Markdown agar TTS lancar
        clean_text = text.replace("*", "").replace("#", "").replace("_", "").replace("`", "")
        clean_text = clean_text.replace('\n', ' ').strip()
        clean_text = clean_text[:800] # Limit teks

        voice = "id-ID-ArdiNeural" 
        communicate = edge_tts.Communicate(clean_text, voice)
        
        audio_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
                
        return bytes(audio_data) if audio_data else None
    except Exception as e:
        print(f"❌ Error Edge-TTS: {e}")
        return None

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

class DeleteChatRequest(BaseModel):
    user_id: str
    mentor_id: int

class FavoriteRequest(BaseModel):
    user_id: str
    mentor_id: int

class PayoutRequestModel(BaseModel):
    mentor_id: int
    amount: int
    bank_info: str 

class DeleteDocsRequest(BaseModel):
    mentor_id: int

# ==========================================
# 5. LOGIC & STATE MANAGEMENT (THE BRAIN)
# ==========================================

def analyze_chat_phase(history: List[dict]) -> dict:
    """
    Menganalisa history untuk menentukan User ada di Fase apa.
    Mengembalikan dict berisi instruksi khusus untuk AI.
    """
    user_messages = [m['message'] for m in history if m['sender'] == 'user']
    
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
# 4. API ENDPOINTS
# ==========================================

@app.get("/")
def home():
    return {"status": "AI Mentor Backend V34 Active"}

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
    history_count = supabase.table("chat_history").select("id", count="exact")\
        .eq("user_id", request.user_id).eq("mentor_id", request.mentor_id).eq("sender", "user").execute()
    user_chat_count = history_count.count if history_count.count else 0
    
    if not is_subscribed and user_chat_count >= 5:
        return {"reply": "LIMIT_REACHED", "mentor": "System", "usage": user_chat_count}

    # 3. Data Mentor & KB
    mentor_data = supabase.table("mentors").select("*").eq("id", request.mentor_id).single().execute()
    mentor = mentor_data.data if mentor_data.data else {"name": "Mentor", "expertise": "General", "avatar_url": None}
    
    # Ambil video bicara (talking video) dari database
    # Kolom ini harus Anda buat di Supabase tabel mentors
    talking_video_url = mentor.get('talking_video_url', None)

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
    
    chat_state = analyze_chat_phase(past_chats_raw)
    
    messages_payload = []
    for chat in past_chats_raw:
        role = "user" if chat['sender'] == "user" else "assistant"
        if chat['message'] != request.message: 
            messages_payload.append({"role": role, "content": chat['message']})

    # SYSTEM PROMPT V35
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

    # =======================================================
    # AUDIO ENGINE (No more Colab)
    # =======================================================
    audio_base64 = None
    if len(ai_reply) > 2:
        try:
            # Generate Suara dari Edge-TTS
            audio_bytes = await generate_edge_tts_audio(ai_reply)
            if audio_bytes:
                audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        except Exception as e: 
            print(f"❌ ERROR AUDIO ENGINE: {e}")

    # Kembalikan audio dan URL video loop bicara
    return {
        "mentor": mentor['name'], 
        "reply": ai_reply, 
        "talking_video_url": talking_video_url, # Berikan URL video loop bicara
        "audio": f"data:audio/mp3;base64,{audio_base64}" if audio_base64 else None
    }

# --- API SISANYA TETAP SAMA ---
# (Search, Reviews, Payment, midtrans, etc tetap di bawah ini)

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
async def toggle_favorite(req: FavoriteRequest, authorization: str = Header(None)):
    # 1. Cek apakah ada token dikirim?
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Token")

    # 2. Ambil token dari string "Bearer eyJhbGci..."
    token = authorization.split(" ")[1]

    # 3. Buat Client Supabase Khusus User Ini (Scoped Client)
    # Kita gunakan URL & KEY standar (bisa Anon Key), TAPI kita timpa auth-nya pakai token user
    user_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    user_supabase.postgrest.auth(token) # <--- INI KUNCINYA (Login sebagai User)

    # 4. Gunakan 'user_supabase' (bukan global 'supabase') untuk query
    try:
        # Cek apakah sudah ada?
        existing = user_supabase.table("favorites").select("*")\
            .eq("user_id", req.user_id).eq("mentor_id", req.mentor_id).execute()
        
        if existing.data:
            # Hapus (Unlike)
            user_supabase.table("favorites").delete()\
                .eq("user_id", req.user_id).eq("mentor_id", req.mentor_id).execute()
            return {"status": "removed"}
        else:
            # Tambah (Like)
            user_supabase.table("favorites").insert({
                "user_id": req.user_id, 
                "mentor_id": req.mentor_id
            }).execute()
            return {"status": "added"}
            
    except Exception as e:
        print(f"Error Toggle Favorite: {e}")
        # Jika error 42501 (RLS), berarti token salah atau user tidak berhak
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/favorites/{user_id}")
async def get_user_favorites(user_id: str, authorization: str = Header(None)):
    # 1. Cek Token
    if not authorization:
        # Jika tidak ada token, kembalikan kosong atau error
        # Untuk keamanan, lebih baik return kosong saja atau raise 401
        return []

    try:
        token = authorization.split(" ")[1]

        # 2. Buat Client Supabase Khusus User Ini
        user_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        user_supabase.postgrest.auth(token) # Login sebagai User

        # 3. Ambil data favorites menggunakan client user
        # Syntax select: favorites(*, mentors(*))
        data = user_supabase.table("favorites").select("mentor_id, mentors(*)")\
            .eq("user_id", user_id).execute()
            
        # 4. Rapikan format return agar hanya list mentor
        # Pastikan item['mentors'] tidak None sebelum dimasukkan
        result = [item['mentors'] for item in data.data if item.get('mentors')]
        return result

    except Exception as e:
        print(f"Error Fetch Favorites: {e}")
        return []