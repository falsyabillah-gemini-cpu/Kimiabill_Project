# Kimiabill_videos — 6-Agent Pure Science Short Video Automation

Project otomasi 6 agent untuk akun **Kimiabill** yang membahas fenomena **sains murni** (**Kimia, Fisika, Matematika, Medisinal, Biologi**) secara mudah, *real footage*, subtitle dinamis, badge rumus minimal, dan dubbing laki-laki ceria (`id-ID-ArdiNeural`).

## Alur Kerja 6 Agent (Viral-First + Fallback Database Ide)
1. **Pre-Scout Tren (0 Token):** Memindai berita/fenomena sains murni terbaru (Google News Sains ID, ScienceDaily, Phys.org, Quanta Magazine). Jika tidak ada yang viral, otomatis fallback ke 30 ide di `data/idea_bank.json` secara seimbang antar 5 bidang.
2. **Agent 1 (Ideation):** Menghasilkan 5 kandidat ide + *hook* 3 detik.
3. **Agent 2 (Editorial Auditor):** Menilai 5 ide, memilih 1 pemenang, dan memberi maksimal 4 catatan perbaikan.
4. **Agent 3 (Script & Shot-List Reviser):** Menulis narasi Bahasa Indonesia yang ceria & jelas + query *real footage* + badge rumus seperlunya.
5. **Agent 6 (Asset Scout):** Mengunduh *real video footage* (Pexels API / Wikimedia Commons) + memakai cache SFX lokal.
6. **Agent 4 (Video Executor) + Gate 0-Token:** Membuat dubbing `id-ID-ArdiNeural`, subtitle per frasa, render draft **480p proxy**, dan cek teknis FFmpeg otomatis.
7. **Agent 5 (Video QA Auditor):** Mengaudit hasil video (maksimal 2× revisi ke Agent 4 / Agent 6), lalu memicu render **1080p HD** dan upload ke **Google Drive**.

## Cara Menjalankan Otomatis Saat Laptop Mati (24/7)
Otomasi berjalan dari **kode project ini** (bukan dari sesi chat):

### Opsi A — GitHub Actions (Gratis, Tanpa VPS — Direkomendasikan)
Workflow `.github/workflows/daily_kimiabill.yml` sudah aktif dan akan otomatis berjalan setiap hari jam **08:00 WIB** (dan bisa dijalankan manual kapan saja lewat tab **Actions → Kimiabill Daily Auto-Video → Run workflow**).
1. Buka **Settings → Secrets and variables → Actions → New repository secret** di repo GitHub ini.
2. Tambahkan minimal **1 API key LLM**:
   - `GEMINI_API_KEY` *(disarankan)*, **atau** `ANTHROPIC_API_KEY`, **atau** `OPENROUTER_API_KEY`.
3. Opsional (sangat disarankan untuk footage lebih variatif): tambahkan `PEXELS_API_KEY`.
4. Opsional (untuk auto-publish media sosial): tambahkan token YouTube/TikTok/Instagram sesuai `.env.example`, lalu atur `publish_mode` (`dry_run` / `draft` / `public`).

### Opsi B — VPS / Cloud Server (Docker)
1. Salin `.env.example` menjadi `.env` lalu isi minimal 1 API key LLM (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, atau `OPENROUTER_API_KEY`), serta opsional `PEXELS_API_KEY`.
2. `config/agents.yaml` sudah memakai `runtime_mode: "production"` dan `provider: "auto"` (otomatis mendeteksi API key yang Anda isi).
3. Jalankan sekali:
   ```bash
   docker compose up -d --build
   ```
   Scheduler (`src/scheduler.py`) akan otomatis memproduksi video setiap hari sesuai `SCHEDULE_TIMES_WIB`.
