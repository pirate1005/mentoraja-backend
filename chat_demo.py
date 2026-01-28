import os
from dotenv import load_dotenv
from supabase import create_client
from groq import Groq

# 1. Setup Koneksi
load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def get_mentor_response(user_question):
    print(f"\n🤖 Sedang berpikir mencari jawaban untuk: '{user_question}'...")

    # A. AMBIL PROFIL MENTOR (ID = 1 karena tadi kita masukkan data ke ID 1)
    mentor_data = supabase.table("mentors").select("*").eq("id", 1).single().execute()
    mentor = mentor_data.data
    
    # B. AMBIL ILMU MENTOR (Retrieval)
    # Catatan: Karena data masih sedikit, kita ambil semua ilmunya dulu. 
    # Nanti kalau sudah ribuan halaman, baru pakai Vector Search.
    docs_data = supabase.table("mentor_docs").select("content").eq("mentor_id", 1).execute()
    
    # Gabungkan semua potongan ilmu jadi satu teks panjang
    knowledge_base = "\n\n".join([item['content'] for item in docs_data.data])

    # C. RAKIT SYSTEM PROMPT (Instruksi Otak)
    system_prompt = f"""
    PERAN: {mentor['personality']}
    
    TUGAS: Jawab pertanyaan user berdasarkan KONTEKS ILMU di bawah ini.
    
    KONTEKS ILMU (JANGAN NGARANG, GUNAKAN INI):
    {knowledge_base}
    
    ATURAN JAWABAN:
    - Jawab dengan gaya bahasa mentor (sesuai peran di atas).
    - Jika jawaban tidak ada di Konteks Ilmu, katakan "Itu di luar materi saya."
    - Jangan terlalu panjang, langsung ke poin penting (IF-THEN atau Step-by-Step).
    """

    # D. KIRIM KE AI (Groq - Llama 3)
    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        model="llama-3.3-70b-versatile", # Model cerdas & gratis
        temperature=0.5, # Agar jawaban konsisten/tidak terlalu kreatif (ngarang)
    )

    return chat_completion.choices[0].message.content

# --- AREA TESTING (SIMULASI CHAT) ---
if __name__ == "__main__":
    print("--- 💬 TES CHAT DENGAN COACH F&B ---")
    
    # Pertanyaan 1: Tentang langkah awal
    tanya1 = "Saya mau buka bisnis ayam goreng, langkah hari pertama apa ya?"
    jawab1 = get_mentor_response(tanya1)
    print(f"\n🗣️ COACH BUDI: {jawab1}\n")
    
    print("-" * 30)

    # Pertanyaan 2: Tentang logika IF-THEN (Kapan Pivot)
    tanya2 = "Bisnis saya sudah jalan 3 bulan tapi belum balik modal (BEP), saya harus gimana?"
    jawab2 = get_mentor_response(tanya2)
    print(f"\n🗣️ COACH BUDI: {jawab2}\n")

    print("-" * 30)
    
    # Pertanyaan 3: Tentang hal di luar materi (Tes Guardrail)
    tanya3 = "Bagaimana cara coding website?"
    jawab3 = get_mentor_response(tanya3)
    print(f"\n🗣️ COACH BUDI: {jawab3}\n")