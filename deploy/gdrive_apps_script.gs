/**
 * Kimiabill Studio — Google Drive Uploader + Email Notifier (Google Apps Script)
 *
 * Cara pasang (2 menit):
 * 1. Buka https://script.google.com -> klik "New project"
 * 2. Hapus semua isi bawaan, tempel seluruh kode ini, lalu simpan (Ctrl+S)
 * 3. Klik "Deploy" -> "New deployment" -> ikon gear pilih "Web app"
 *    - Execute as: Me (akun Google Anda)
 *    - Who has access: Anyone
 * 4. Klik "Deploy" -> izinkan akses Google Drive & Gmail -> salin Web app URL
 * 5. Simpan URL tersebut di GitHub Secrets dengan nama: GDRIVE_WEBHOOK_URL
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
      // Timpa file dengan nama yang sama jika sudah ada
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

    if (action === "notify_email") {
      const to = payload.to || DEFAULT_NOTIFY_EMAIL;
      const title = payload.title || payload.episode_name || "Video Kimiabill Baru";
      const subject = "[Kimiabill] Video Baru Siap: " + title;
      const bodyLines = [
        "Halo! Video otomatis Kimiabill terbaru sudah selesai dirender dan tersimpan di Google Drive.",
        "",
        "Judul       : " + title,
        "Episode ID  : " + (payload.episode_name || "-"),
        "Folder Drive: " + (payload.folder_url || "-"),
      ];
      if (payload.video_url) {
        bodyLines.push("Link Video  : " + payload.video_url);
      }
      if (payload.thumbnail_url) {
        bodyLines.push("Thumbnail   : " + payload.thumbnail_url);
      }
      if (payload.captions_preview) {
        bodyLines.push("");
        bodyLines.push("=== CAPTION & HASHTAG SIAP COPY ===");
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
  return _jsonResponse({ ok: true, service: "Kimiabill Drive & Email Webhook Active" });
}
