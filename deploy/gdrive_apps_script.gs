/**
 * Kimiabill Studio — Google Drive + Auto-Upload YouTube Shorts + Email Notifier
 * (Google Apps Script — 100% Gratis, Tanpa Google Cloud Console)
 *
 * Cara pasang (3 menit):
 * 1. Buka https://script.google.com (login dengan akun Google yang sama dengan YouTube Anda)
 * 2. Klik "New project", hapus semua isi bawaan, tempel seluruh kode ini, lalu simpan (Ctrl+S)
 * 3. Aktifkan YouTube di sidebar kiri:
 *    Klik "Services (+)" -> cari "YouTube Data API v3" -> klik "Add"
 * 4. Klik "Deploy" -> "New deployment" -> ikon gir pilih "Web app"
 *    - Execute as: Me (akun Google Anda)
 *    - Who has access: Anyone
 * 5. Klik "Deploy" -> izinkan akses Google Drive, YouTube & Gmail -> salin Web app URL (/exec)
 * 6. Simpan URL tersebut di GitHub Secrets dengan nama: GDRIVE_WEBHOOK_URL
 */

const DEFAULT_NOTIFY_EMAIL = "researchfalsyabillah@gmail.com";
const DEFAULT_ROOT_FOLDER = "Kimiabill_Studio";
// Opsional: jika ingin tambah keamanan ekstra, samakan dengan secret GDRIVE_WEBHOOK_TOKEN di GitHub
const OPTIONAL_SECRET_TOKEN = "";

function _jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function _getOrCreateFolder(name, parentFolder) {
  const iter = parentFolder
    ? parentFolder.getFoldersByName(name)
    : DriveApp.getFoldersByName(name);
  if (iter.hasNext()) {
    return iter.next();
  }
  return parentFolder ? parentFolder.createFolder(name) : DriveApp.createFolder(name);
}

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents || "{}");

    if (OPTIONAL_SECRET_TOKEN && payload.token !== OPTIONAL_SECRET_TOKEN) {
      return _jsonResponse({ ok: false, error: "Unauthorized token" });
    }

    const action = payload.action || "";

    if (action === "init_episode") {
      const rootName = payload.root_folder || DEFAULT_ROOT_FOLDER;
      const episodeName = payload.episode_name || ("ep_" + new Date().getTime());
      const rootFolder = _getOrCreateFolder(rootName, null);
      const epFolder = _getOrCreateFolder(episodeName, rootFolder);
      return _jsonResponse({
        ok: true,
        folder_id: epFolder.getId(),
        folder_url: epFolder.getUrl(),
      });
    }

    if (action === "upload_file") {
      const folder = DriveApp.getFolderById(payload.folder_id);
      const bytes = Utilities.base64Decode(payload.content_base64);
      const blob = Utilities.newBlob(
        bytes,
        payload.mime_type || "application/octet-stream",
        payload.filename || "file.bin"
      );
      const existing = folder.getFilesByName(blob.getName());
      while (existing.hasNext()) {
        existing.next().setTrashed(true);
      }
      const file = folder.createFile(blob);
      return _jsonResponse({
        ok: true,
        file_id: file.getId(),
        file_url: file.getUrl(),
        filename: file.getName(),
      });
    }

    if (action === "upload_youtube") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({
          ok: false,
          error: "Service 'YouTube Data API v3' belum ditambahkan di Apps Script (klik Services + -> YouTube Data API v3 -> Add).",
        });
      }
      const videoFile = DriveApp.getFileById(payload.video_file_id);
      const videoBlob = videoFile.getBlob();
      const privacy = payload.privacy === "public" ? "public" : "unlisted";
      const resource = {
        snippet: {
          title: String(payload.title || "Video Sains Kimiabill").slice(0, 95),
          description: String(payload.description || ""),
          tags: Array.isArray(payload.tags) ? payload.tags.slice(0, 15) : ["sains", "kimiabill", "shorts"],
          categoryId: "27", // Education
          defaultLanguage: "id",
        },
        status: {
          privacyStatus: privacy,
          selfDeclaredMadeForKids: false,
        },
      };
      const inserted = YouTube.Videos.insert(resource, "snippet,status", videoBlob);
      const videoId = inserted.id;

      // Upload thumbnail kustom (opsional; dilewati otomatis jika channel belum verifikasi nomor HP)
      let thumbnailSet = false;
      if (payload.thumbnail_file_id) {
        try {
          const thumbBlob = DriveApp.getFileById(payload.thumbnail_file_id).getBlob();
          YouTube.Thumbnails.set(videoId, thumbBlob);
          thumbnailSet = true;
        } catch (thumbErr) {
          thumbnailSet = false;
        }
      }

      return _jsonResponse({
        ok: true,
        video_id: videoId,
        youtube_url: "https://youtube.com/shorts/" + videoId,
        privacy: privacy,
        thumbnail_set: thumbnailSet,
      });
    }

    if (action === "notify_email") {
      const to = payload.to || DEFAULT_NOTIFY_EMAIL;
      const title = payload.title || payload.episode_name || "Video Kimiabill Baru";
      const subject = "[Kimiabill] Video Baru Siap: " + title;
      const bodyLines = [
        "Halo! Video otomatis Kimiabill terbaru sudah selesai dirender.",
        "",
        "Judul       : " + title,
        "Episode ID  : " + (payload.episode_name || "-"),
        "Folder Drive: " + (payload.folder_url || "-"),
      ];
      if (payload.youtube_url) {
        bodyLines.push("YouTube     : " + payload.youtube_url + " (" + (payload.youtube_privacy || "unlisted") + ")");
      } else if (payload.youtube_note) {
        bodyLines.push("YouTube     : " + payload.youtube_note);
      }
      if (payload.video_url) {
        bodyLines.push("Link Video  : " + payload.video_url);
      }
      if (payload.thumbnail_url) {
        bodyLines.push("Thumbnail   : " + payload.thumbnail_url);
      }
      if (payload.captions_preview) {
        bodyLines.push("");
        bodyLines.push("=== CAPTION & HASHTAG ===");
        bodyLines.push(payload.captions_preview);
      }
      bodyLines.push("");
      bodyLines.push("-- Dikirim otomatis oleh Kimiabill Studio");

      MailApp.sendEmail({
        to: to,
        subject: subject,
        body: bodyLines.join("\n"),
      });
      return _jsonResponse({ ok: true, emailed_to: to });
    }

    return _jsonResponse({ ok: false, error: "Unknown action: " + action });
  } catch (err) {
    return _jsonResponse({ ok: false, error: String(err) });
  }
}

function doGet() {
  return _jsonResponse({ ok: true, service: "Kimiabill Drive, YouTube & Email Webhook Active" });
}
