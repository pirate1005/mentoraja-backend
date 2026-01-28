import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_KEY)

print("--- SEDANG MENCARI MODEL YANG TERSEDIA ---")

try:
    # Minta Google melist semua model yang bisa dipakai kunci ini
    available_models = []
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"✅ DITEMUKAN: {m.name}")
            available_models.append(m.name)
            
    if not available_models:
        print("❌ Tidak ada model yang ditemukan. Cek apakah API Key sudah aktif billing/plan-nya (meski free).")
except Exception as e:
    print(f"❌ Error akses Google: {e}")