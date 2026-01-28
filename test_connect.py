import os
from dotenv import load_dotenv
from supabase import create_client
from groq import Groq

# 1. Load data rahasia
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")

print("--- MEMULAI TES KONEKSI (SUPABASE + GROQ) ---")

# 2. Tes Koneksi Supabase
try:
    print(f"1. Menghubungi Database Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    response = supabase.table("users").select("*").execute()
    print("   ✅ Sukses! Supabase terhubung.")
except Exception as e:
    print(f"   ❌ Gagal connect Supabase. Cek URL/Key Anda.")
    print(f"   Error: {e}")

# 3. Tes Koneksi AI (Groq - Llama 3)
try:
    print(f"\n2. Menghubungi Otak AI (Groq - Llama 3)...")
    client = Groq(api_key=GROQ_KEY)
    
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "user",
                "content": "Jawab satu kata saja: Apakah kamu siap?",
            }
        ],
        model="llama-3.3-70b-versatile", # Model Llama 3 yang gratis & cepat
    )
    
    jawaban = chat_completion.choices[0].message.content
    print(f"   ✅ Sukses! Groq menjawab: {jawaban}")

except Exception as e:
    print(f"   ❌ Gagal connect Groq.")
    print(f"   Error: {e}")

print("\n--- TES SELESAI ---")