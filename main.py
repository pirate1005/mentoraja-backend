import os
import json
import re
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
    title="AI Mentor SaaS Platform - V19 (Full Output Engine)",
    description="Backend AI Mentor dengan fitur Full Output 17 Langkah & High Token Limit."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    snap = midtransclient.Snap(
        is_production=False, 
        server_key=os.getenv("MIDTRANS_SERVER_KEY"),
        client_key=os.getenv("MIDTRANS_CLIENT_KEY")
    )
    print("✅ System Ready: V19 (Full Output Engine Active)")
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

@app.get("/")
def home():
    return {"status": "AI Mentor SaaS Backend V19.0 (Full Output) is Running"}

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


# --- B. API UTAMA: CHAT (V19 - FULL OUTPUT LOGIC) ---
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

    # 3. Data Mentor & PDF
    mentor_data = supabase.table("mentors").select("*").eq("id", request.mentor_id).single().execute()
    mentor = mentor_data.data if mentor_data.data else {"name": "Mentor", "personality": "Senior Consultant", "expertise": "Bisnis"}
    
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
    # SYSTEM PROMPT V19 (FULL OUTPUT & PROACTIVE)
    # ==============================================================================
    system_prompt = f"""
LIVE CONSULTANT ENGINE — V19 FULL OUTPUT PROTOCOL

IDENTITY:
You are {mentor['name']}, a senior business consultant.
Goal: To guide the client through the steps in the Knowledge Base.

KNOWLEDGE BASE (SOURCE OF TRUTH - "17 LANGKAH"):
{knowledge_base}

================================================
SECTION A: SCOPE & BOUNDARIES (STRICT)
1. **DOMAIN LOCK**: Only discuss Business, Strategy, Marketing, Sales based on the PDF.
2. **HARD REFUSAL**: Refuse coding, politics, or gossip politely.
3. **NO CHIT-CHAT**: Bridge back to the business goal immediately.

SECTION B: THE "17 STEPS" COMPLIANCE (FULL OUTPUT MODE)
1. **NO SUMMARIZATION**: If the user asks for "Langkah-langkahnya" or "Apa saja langkahnya?", **YOU MUST OUTPUT ALL 17 STEPS** from the PDF.
2. **NO TRUNCATION**: Do not stop at step 5 or 10. Do not ask "Should I continue?". Write them ALL out immediately.
3. **Format**: List them clearly (Step 1, Step 2, ... Step 17) with a brief 1-sentence explanation for each.

SECTION C: PROACTIVE "OFFER TO HELP" (THE "DO IT FOR YOU" PROTOCOL)
After listing the steps (or when discussing a specific step), **Offer to do the work based on the 'Output' section in PDF.**

- **Scenario: Calculation (Pricing/Profit/Margin)**
  - PDF Rule: "Margin min 50%".
  - YOUR ACTION: Ask "Berapa harga modal (HPP) kamu? Sini saya hitungkan harga jual minimalnya."
  
- **Scenario: Research (Competitor/Supplier)**
  - PDF Rule: "Riset 5-10 kompetitor".
  - YOUR ACTION: Ask "Kamu jualan apa? Mau saya bantu buatkan daftar poin riset kompetitor?"
  
- **Scenario: Content/Copywriting**
  - PDF Rule: "Rumus copywriting masalah + solusi".
  - YOUR ACTION: Offer "Mau saya buatkan contoh copywriting untuk produkmu sekarang?"

SECTION D: PSYCHOLOGICAL & TIMING (FROM "KONSULTAN RULES")
1. **Clarity Before Pressure**: Clarify the problem first.
2. **Resistance = SLOW DOWN**: If user says "bingung", soften the tone.

OUTPUT STRUCTURE:
1. Brief acknowledgment.
2. **THE CONTENT**: 
   - If user asks for the list -> **Provide ALL 17 Steps**.
   - If user asks about specific problem -> Explain the relevant step.
3. **THE OFFER**: Explicitly offer to help execute the "Output" (Calculate, Write, or List).

CONTEXT:
User Name: {request.user_first_name}
Business Type: {request.business_type}
User Message: "{request.message}"
"""
    
    final_messages = [{"role": "system", "content": system_prompt}] + messages_payload
    final_messages.append({"role": "user", "content": request.message})
    
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

    supabase.table("chat_history").insert({
        "user_id": request.user_id, "mentor_id": request.mentor_id, "sender": "ai", "message": ai_reply
    }).execute()

    return {"mentor": mentor['name'], "reply": ai_reply}


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