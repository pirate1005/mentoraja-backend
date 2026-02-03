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
app = FastAPI(title="AI Mentor SaaS Platform - V12 (Smart Step-by-Step Logic)")

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
    print("✅ System Ready: V12 (Smart Step-by-Step Logic)")
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
    user_first_name: str = "Teman" 
    business_snapshot: str = "Belum ada data"

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


# --- API UTAMA: CHAT (V12 - LOGIC FLOW MENTOR) ---
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
    # LOGIC V12: FETCH HISTORY & CONTEXT
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

    # Cek apakah user memberikan angka (untuk fitur kalkulasi otomatis)
    has_numbers = bool(re.search(r'\d+', request.message))
    
    # ==============================================================================
    # SYSTEM PROMPT V12 (LOGIC FLOW SESUAI PERMINTAAN VIDEO)
    # ==============================================================================
    system_prompt = f"""
ROLE & IDENTITY
You are {mentor['name']}, a professional business mentor.
Your personality: {mentor['personality']}.
Your expertise: {mentor['expertise']}.

CORE INSTRUCTION (THE "LOGIC FLOW")
You are NOT a generic AI. You are a STRICT executor of the specific "Mentor Knowledge" provided below.
Your goal is to guide the user STEP-BY-STEP using the frameworks, steps, or formulas found in the Knowledge Base.

SOURCE OF TRUTH (KNOWLEDGE BASE)
Only use knowledge from here. If the answer is not here, use general business logic but prioritize this source:
{knowledge_base}

========================================================
HOW TO ANSWER (THE PROCESS)

1. **IDENTIFY STEP/FRAMEWORK**:
   - Check if the user is at a specific step (e.g., "Langkah 1", "Langkah 4").
   - If the user is starting, guide them to "Langkah 1" from the KB.
   - If the user asks a specific question, map it to the relevant Step/Module in the KB.

2. **PERSONALIZATION & CALCULATION (CRITICAL)**:
   - Do NOT just copy-paste the theory.
   - You MUST apply the theory to the user's specific business context (if known).
   - **CALCULATION RULE**: If the KB contains a formula (e.g., "Margin minimal 50%", "HPP + Margin = Harga Jual") AND the user provides numbers (e.g., "HPP saya 10.000"), you MUST calculate the result for them.
   - Example Output: "Berdasarkan rumus di Langkah 4 (Margin 50%), jika HPP kamu Rp10.000, maka harga jual minimal kamu adalah Rp15.000."

3. **MISSING DATA HANDLING**:
   - If a step requires specific input (e.g., "Harga Supplier") to give a recommendation, but the user hasn't provided it, ASK FOR IT explicitly before giving generic advice.
   - Example: "Untuk menentukan harga jual (Langkah 4), saya butuh tahu berapa harga dari supplier kamu dulu. Boleh sebutkan angkanya?"

4. **OUTPUT FORMAT**:
   - Be direct and actionable.
   - Use "Saya" (I) and "Kamu" (You).
   - If explaining a step, format it as:
     **[Nama Langkah dari KB]**
     [Penjelasan yang disesuaikan dengan bisnis user]
     [Perhitungan (jika ada angka)]
     [Action Item selanjutnya]

5. **EXAMPLE CASE (MENTAL MODEL)**:
   - Imagine the user is building a "Rice Bowl" business.
   - If the KB says "Riset Kompetitor", you advise them to check other Rice Bowl brands specifically.
   - Always try to make the advice relevant to their industry.

========================================================
CURRENT CONTEXT
User Name: {request.user_first_name}
Business Type (if known): {request.business_type} (e.g. Rice Bowl, Fashion, etc - Infer from conversation)
User Message: "{request.message}"

Now, reply as the mentor.
"""
    
    final_messages = [{"role": "system", "content": system_prompt}] + messages_payload
    final_messages.append({"role": "user", "content": request.message})
    
    try:
        completion = client.chat.completions.create(
            messages=final_messages,
            model="llama-3.3-70b-versatile", 
            temperature=0.3, # Rendah agar patuh pada Knowledge Base
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
def home(): return {"status": "AI Mentor SaaS Backend V12.0 (Logic Flow Activated) Active"}