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

function _tgHtmlOk() {
  return HtmlService.createHtmlOutput("OK");
}

function _replyTg(botToken, chatId, replyText) {
  try {
    const tgResp = UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/sendMessage", {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({ chat_id: String(chatId), text: replyText }),
      muteHttpExceptions: true,
    });
    const code = tgResp.getResponseCode();
    const body = tgResp.getContentText().slice(0, 200);
    console.log("[tg_webhook] sendMessage HTTP " + code + ": " + body);
    return { ok: code >= 200 && code < 300, code: code, body: body };
  } catch (e) {
    console.log("[tg_webhook] sendMessage exception: " + String(e));
    return { ok: false, code: 0, body: String(e) };
  }
}

function _answerCallbackQuery(botToken, callbackQueryId, text) {
  try {
    UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/answerCallbackQuery", {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({ callback_query_id: String(callbackQueryId), text: String(text || ""), show_alert: false }),
      muteHttpExceptions: true,
    });
  } catch (e) {}
}

function _editMessageReplyMarkup(botToken, chatId, messageId, replyMarkup) {
  try {
    UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/editMessageReplyMarkup", {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({
        chat_id: String(chatId),
        message_id: Number(messageId),
        reply_markup: replyMarkup || { inline_keyboard: [] },
      }),
      muteHttpExceptions: true,
    });
  } catch (e) {}
}

function _dispatchGithubEvent(githubRepo, githubPat, eventType, clientPayload) {
  return UrlFetchApp.fetch(
    "https://api.github.com/repos/" + githubRepo + "/dispatches",
    {
      method: "post",
      contentType: "application/json",
      headers: {
        Authorization: "Bearer " + githubPat,
        Accept: "application/vnd.github+json",
      },
      payload: JSON.stringify({
        event_type: eventType,
        client_payload: clientPayload || {},
      }),
      muteHttpExceptions: true,
    }
  );
}

function _processCallbackQuery(cb, botToken, allowedChatId, githubRepo, githubPat, props) {
  const fromChat = cb.message && cb.message.chat ? String(cb.message.chat.id) : (cb.from ? String(cb.from.id) : "");
  const messageId = cb.message ? Number(cb.message.message_id || 0) : 0;
  const data = String(cb.data || "").trim();
  if (fromChat !== allowedChatId || !data) {
    _answerCallbackQuery(botToken, cb.id, "Akses ditolak.");
    return { ok: true, ignored: true };
  }

  const colonIdx = data.indexOf(":");
  const action = colonIdx >= 0 ? data.slice(0, colonIdx) : data;
  const epId = colonIdx >= 0 ? data.slice(colonIdx + 1).trim() : "";

  if (action === "revise" && messageId) {
    _answerCallbackQuery(botToken, cb.id, "Pilih fokus revisi atau balas teks!");
    props.setProperty("PENDING_REVISE_EPISODE_ID", epId);
    _editMessageReplyMarkup(botToken, allowedChatId, messageId, {
      inline_keyboard: [
        [
          { text: "🎥 Revisi B-Roll", callback_data: "revise_broll:" + epId },
          { text: "📊 Revisi Diagram", callback_data: "revise_diagram:" + epId },
        ],
        [
          { text: "🗣️ Revisi Naskah/Audio", callback_data: "revise_script:" + epId },
          { text: "⬅️ Kembali", callback_data: "revise_back:" + epId },
        ],
      ],
    });
    _replyTg(
      botToken,
      allowedChatId,
      "✏️ Pilih tombol fokus revisi di atas untuk `" + epId + "`, ATAU balas pesan teks singkat (catatan revisi khusus) sekarang."
    );
    return { ok: true, action: "revise_menu" };
  }

  if (action === "revise_back" && messageId) {
    props.deleteProperty("PENDING_REVISE_EPISODE_ID");
    _answerCallbackQuery(botToken, cb.id, "Kembali ke menu audit.");
    _editMessageReplyMarkup(botToken, allowedChatId, messageId, {
      inline_keyboard: [
        [{ text: "✅ Upload / Jadwalkan", callback_data: "approve:" + epId }],
        [{ text: "🔄 Revisi Video Ini", callback_data: "revise:" + epId }],
        [{ text: "❌ Tolak & Buat Topik Baru", callback_data: "reject:" + epId }],
        [{ text: "🤖 Serahkan ke AI", callback_data: "ai_audit:" + epId }],
      ],
    });
    return { ok: true, action: "revise_back" };
  }

  // Anti double-click: strip inline buttons IMMEDIATELY before dispatching workflow
  if (messageId) {
    _editMessageReplyMarkup(botToken, allowedChatId, messageId, { inline_keyboard: [] });
  }
  props.deleteProperty("PENDING_REVISE_EPISODE_ID");

  if (!githubPat) {
    _answerCallbackQuery(botToken, cb.id, "GITHUB_PAT belum diset di Apps Script!");
    _replyTg(botToken, allowedChatId, "⚠️ GITHUB_PAT belum diisi di Apps Script Properties.");
    return { ok: false, error: "GITHUB_PAT missing" };
  }

  let eventType = "";
  let payload = { episode_id: epId };
  let confirmMsg = "";
  if (action === "approve") {
    eventType = "telegram_approve";
    confirmMsg = "✅ Disetujui! Memproses penjadwalan/publish YouTube untuk `" + epId + "`...";
  } else if (action === "revise_broll") {
    eventType = "telegram_rerender";
    payload.feedback = "Ganti B-roll agar lebih relevan dengan narasi, full-screen 9:16 bersih, tanpa bumper asing.";
    confirmMsg = "🔄 Siap! Memulai revisi B-Roll untuk `" + epId + "`...";
  } else if (action === "revise_diagram") {
    eventType = "telegram_rerender";
    payload.feedback = "Perbaiki diagram mekanistik agar cocok akurat dengan konsep sains dan bebas clipping/placeholder.";
    confirmMsg = "🔄 Siap! Memulai revisi Diagram untuk `" + epId + "`...";
  } else if (action === "revise_script") {
    eventType = "telegram_rerender";
    payload.feedback = "Perbaiki naskah/pelafalan audio agar padat 30-48 detik, tanpa typo, dan CTA Subscribe/loop.";
    confirmMsg = "🔄 Siap! Memulai revisi Naskah/Audio untuk `" + epId + "`...";
  } else if (action === "reject") {
    eventType = "telegram_reject_next";
    confirmMsg = "❌ Topik `" + epId + "` ditolak & masuk blacklist. Memproduksi topik baru sekarang...";
  } else if (action === "ai_audit") {
    eventType = "telegram_ai_audit";
    confirmMsg = "🤖 Menyerahkan `" + epId + "` ke AI Chief Editor untuk audit video+audio native MP4...";
  } else {
    _answerCallbackQuery(botToken, cb.id, "Aksi tidak dikenal.");
    return { ok: true, ignored: true };
  }

  _answerCallbackQuery(botToken, cb.id, "Diproses!");
  const dispResp = _dispatchGithubEvent(githubRepo, githubPat, eventType, payload);
  const code = dispResp.getResponseCode();
  if (code >= 200 && code < 300) {
    _replyTg(botToken, allowedChatId, confirmMsg);
  } else {
    _replyTg(botToken, allowedChatId, "⚠️ Gagal memicu GitHub Actions (`" + eventType + "`, HTTP " + code + ").");
  }
  return { ok: code >= 200 && code < 300, event_type: eventType, status: code };
}

function _processTelegramUpdate(payload) {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  const allowedChatId = String(props.getProperty("TELEGRAM_CHAT_ID") || "").trim();
  const githubPat = String(props.getProperty("GITHUB_PAT") || "").trim();
  const githubRepo = String(props.getProperty("GITHUB_REPO") || "falsyabillah-gemini-cpu/Kimiabill_Project").trim();

  if (!botToken || !allowedChatId) {
    console.log("[tg_webhook] ignored: Script Properties belum lengkap");
    return { ok: true, ignored: true, reason: "missing_script_properties" };
  }

  // Deduplicate update_id across retries / webhook+poll overlap
  if (payload.update_id && typeof CacheService !== "undefined") {
    const cache = CacheService.getScriptCache();
    const dedupeKey = "tg_upd_" + String(payload.update_id);
    if (cache.get(dedupeKey)) {
      console.log("[tg_webhook] duplicate update_id skipped: " + payload.update_id);
      return { ok: true, duplicate: true };
    }
    cache.put(dedupeKey, "1", 600);
  }

  if (payload.callback_query) {
    return _processCallbackQuery(payload.callback_query, botToken, allowedChatId, githubRepo, githubPat, props);
  }

  const msg = payload.message || payload.edited_message || {};
  const chatId = msg.chat && msg.chat.id !== undefined ? String(msg.chat.id) : "";
  const text = String(msg.text || msg.caption || "").trim();

  if (chatId !== allowedChatId || !text) {
    console.log("[tg_webhook] ignored: chatId mismatch or empty text (incoming=" + chatId + ", expected=" + allowedChatId + ")");
    return { ok: true, ignored: true, reason: "chat_mismatch_or_empty" };
  }

  const low = text.toLowerCase();
  if (low === "/start" || low === "/help") {
    _replyTg(
      botToken,
      allowedChatId,
      "🤖 Kimiabill Bot Aktif (Audit Manual + Pola B).\n" +
      "• Tekan tombol audit inline untuk Approve/Jadwalkan, Revisi, Tolak Topik, atau Serahkan ke AI.\n" +
      "• Kirim pesan apa saja = otomatis dikompilasi untuk VIDEO BERIKUTNYA.\n" +
      "• Kirim `/rerender <catatan>` = langsung revisi & render ulang episode aktif.\n" +
      "• Kirim `/rules` = cek aturan aktif | `/clear_rules` = reset aturan tambahan."
    );
    return { ok: true };
  }

  if (!githubPat) {
    _replyTg(
      botToken,
      allowedChatId,
      "⚠️ Feedback terbaca, tetapi GITHUB_PAT belum diisi di Apps Script Properties."
    );
    return { ok: true, warning: "GITHUB_PAT missing" };
  }

  const pendingReviseEp = String(props.getProperty("PENDING_REVISE_EPISODE_ID") || "").trim();
  const isRulesCmd = low.indexOf("/rules") === 0 || low === "/aturan";
  const isClearCmd = low.indexOf("/clear_rules") === 0 || low.indexOf("/clear") === 0;
  const isCeoAuditCmd = low.indexOf("/audit_ceo") === 0 || low.indexOf("/audit_all") === 0;
  const isRerender = low.indexOf("/rerender") === 0 || (Boolean(pendingReviseEp) && low.indexOf("/") !== 0);

  let cleanNote = text;
  let targetEpId = "";
  if (low.indexOf("/rerender") === 0) {
    cleanNote = text.slice(9).trim();
  } else if (Boolean(pendingReviseEp) && low.indexOf("/") !== 0) {
    targetEpId = pendingReviseEp;
    props.deleteProperty("PENDING_REVISE_EPISODE_ID");
  } else if (isRulesCmd || isClearCmd || isCeoAuditCmd) {
    cleanNote = text;
  } else if (low.indexOf("/feedback") === 0) {
    cleanNote = text.slice(9).trim();
  }

  const eventType = isCeoAuditCmd
    ? "telegram_ceo_audit"
    : (isRerender ? "telegram_rerender" : "telegram_feedback");
  const dispatchPayload = {
    text: cleanNote,
    feedback: cleanNote,
    mode: isRerender ? "rerender" : "next_video",
  };
  if (targetEpId) {
    dispatchPayload.episode_id = targetEpId;
  }

  const dispatchResp = _dispatchGithubEvent(githubRepo, githubPat, eventType, dispatchPayload);
  const code = dispatchResp.getResponseCode();
  console.log("[tg_webhook] GitHub dispatch HTTP " + code);
  if (code >= 200 && code < 300) {
    if (isCeoAuditCmd) {
      _replyTg(botToken, allowedChatId, "📊 Menjalankan Audit Visual Asisten CEO lintas seluruh akun (tunggu ~30 detik)...");
    } else if (isRulesCmd) {
      _replyTg(botToken, allowedChatId, "🔎 Mengecek daftar aturan aktif di semua Agent (tunggu ~10 detik)...");
    } else if (isClearCmd) {
      _replyTg(botToken, allowedChatId, "🧹 Mengirim perintah reset aturan tambahan ke semua Agent...");
    } else {
      _replyTg(
        botToken,
        allowedChatId,
        isRerender
          ? "🔄 Siap! Mulai revisi video " + (targetEpId || "terbaru") + " dengan catatan:\n" + (cleanNote || "(tanpa catatan tambahan)")
          : "⏳ Masukan diterima! Sedang dikompilasi otomatis ke Agent 1-8 + Engine (~10 detik)..."
      );
    }
  } else {
    _replyTg(botToken, allowedChatId, "⚠️ Gagal menghubungi GitHub Actions (HTTP " + code + ").");
  }
  return { ok: code >= 200 && code < 300, status: code };
}

function _handleTelegramWebhook(payload) {
  _processTelegramUpdate(payload);
  // HtmlService menghindari HTTP 302 redirect milik ContentService yang sering ditolak webhook Telegram
  return _tgHtmlOk();
}

/**
 * Jalankan manual dari editor Apps Script (Run -> testTelegramBot) untuk:
 * 1) Memicu izin UrlFetchApp (akses jaringan eksternal),
 * 2) Mengecek status webhook Telegram (last_error_message / pending_update_count),
 * 3) Mengirim pesan tes langsung ke Telegram Anda.
 */
function testTelegramBot() {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  const allowedChatId = String(props.getProperty("TELEGRAM_CHAT_ID") || "").trim();
  const githubPat = String(props.getProperty("GITHUB_PAT") || "").trim();
  console.log("Script Properties -> botTokenLen=" + botToken.length + ", chatId=" + allowedChatId + ", githubPatSet=" + Boolean(githubPat));
  if (!botToken || !allowedChatId) {
    throw new Error("TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum lengkap di Script Properties.");
  }

  const infoResp = UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/getWebhookInfo", { muteHttpExceptions: true });
  const infoText = infoResp.getContentText();
  console.log("getWebhookInfo -> " + infoText);

  let webhookSummary = "";
  try {
    const parsed = JSON.parse(infoText);
    const res = parsed.result || {};
    webhookSummary =
      "\n• Webhook URL: " + (res.url || "(kosong / mode polling)") +
      "\n• Pending updates: " + (res.pending_update_count || 0) +
      "\n• Last error: " + (res.last_error_message || "(tidak ada error)");
  } catch (e) {
    webhookSummary = "\n• Info webhook: " + infoText.slice(0, 150);
  }

  const sendRes = _replyTg(
    botToken,
    allowedChatId,
    "🧪 Tes langsung dari Google Apps Script berhasil!" +
      webhookSummary +
      "\n\nJika Last error masih menyebut '302', jalankan fungsi enableTelegramPollingMode di editor Apps Script agar bot kebal 302."
  );
  console.log("sendMessage result -> " + JSON.stringify(sendRes));
}

/**
 * Jalankan 1x dari editor Apps Script (Run -> authorizeAllPermissions) agar Google meminta
 * seluruh izin yang dibutuhkan sekaligus: Drive, Sheets (read_spreadsheet), UrlFetchApp (Telegram),
 * dan YouTube force-ssl (untuk auto pinned-comment YouTube Shorts).
 */
function authorizeAllPermissions() {
  DriveApp.getRootFolder().getName();
  console.log("Drive OK");
  if (typeof YouTube !== "undefined") {
    const chResp = YouTube.Channels.list("id,snippet", { mine: true });
    const ch = (chResp.items && chResp.items[0]) || null;
    console.log("YouTube Channel OK: " + (ch ? ch.snippet.title : "none"));
    if (ch && ch.id) {
      YouTube.CommentThreads.list("snippet", { allThreadsRelatedToChannelId: ch.id, maxResults: 1 });
      console.log("YouTube CommentThreads (force-ssl) OK");
    }
  }
  const dummy = SpreadsheetApp.getActiveSpreadsheet();
  console.log("SpreadsheetApp scope OK (active=" + Boolean(dummy) + ")");
  testTelegramBot();
}

function pollTelegram() {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  if (!botToken) return;

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    let offset = Number(props.getProperty("TG_OFFSET") || "0");
    const resp = UrlFetchApp.fetch(
      "https://api.telegram.org/bot" + botToken + "/getUpdates?offset=" + offset + "&timeout=0&allowed_updates=%5B%22message%22%2C%22edited_message%22%2C%22callback_query%22%5D",
      { method: "get", muteHttpExceptions: true }
    );
    const data = JSON.parse(resp.getContentText() || "{}");
    if (!data.ok || !Array.isArray(data.result) || data.result.length === 0) {
      return;
    }
    data.result.forEach(function (upd) {
      try {
        _processTelegramUpdate(upd);
      } catch (innerErr) {
        console.log("[pollTelegram] error on update " + upd.update_id + ": " + String(innerErr));
      }
      if (upd.update_id && upd.update_id + 1 > offset) {
        offset = upd.update_id + 1;
      }
    });
    props.setProperty("TG_OFFSET", String(offset));
  } finally {
    lock.releaseLock();
  }
}

/**
 * Alternatif 100% bebas error 302: hapus webhook dan cek Telegram tiap 1 menit lewat Apps Script trigger.
 */
function enableTelegramPollingMode() {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  const allowedChatId = String(props.getProperty("TELEGRAM_CHAT_ID") || "").trim();
  if (!botToken || !allowedChatId) {
    throw new Error("TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum lengkap di Script Properties.");
  }

  const delResp = UrlFetchApp.fetch(
    "https://api.telegram.org/bot" + botToken + "/deleteWebhook?drop_pending_updates=false",
    { method: "get", muteHttpExceptions: true }
  );
  console.log("deleteWebhook -> " + delResp.getContentText());

  ScriptApp.getProjectTriggers().forEach(function (tr) {
    if (tr.getHandlerFunction() === "pollTelegram") {
      ScriptApp.deleteTrigger(tr);
    }
  });
  ScriptApp.newTrigger("pollTelegram").timeBased().everyMinutes(1).create();
  pollTelegram();
  _replyTg(botToken, allowedChatId, "✅ Mode Polling 1-menit Apps Script aktif (pesan & tombol audit inline aktif)!");
}

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents || "{}");

    if (payload.update_id && (payload.message || payload.edited_message || payload.callback_query)) {
      return _handleTelegramWebhook(payload);
    }

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
        const oldFile = existing.next();
        try {
          oldFile.setTrashed(true);
        } catch (trErr) {
          try {
            folder.removeFile(oldFile);
          } catch (rmErr) {}
        }
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
      const privacy = payload.privacy === "public" ? "public" : (payload.privacy === "private" ? "private" : "unlisted");
      const statusObj = {
        privacyStatus: privacy,
        selfDeclaredMadeForKids: false,
      };
      if (privacy === "private" && payload.publish_at) {
        statusObj.publishAt = String(payload.publish_at);
      }
      const resource = {
        snippet: {
          title: String(payload.title || "Video Sains Kimiabill").slice(0, 95),
          description: String(payload.description || ""),
          tags: Array.isArray(payload.tags) ? payload.tags.slice(0, 15) : ["sains", "kimiabill", "shorts"],
          categoryId: "27", // Education
          defaultLanguage: "id",
        },
        status: statusObj,
      };
      const inserted = YouTube.Videos.insert(resource, "snippet,status", videoBlob);
      const videoId = inserted.id;

      // Upload thumbnail kustom setelah menunggu processing YouTube sejenak + catat error jika gagal
      let thumbnailSet = false;
      let thumbnailError = "";
      if (payload.thumbnail_file_id) {
        try {
          Utilities.sleep(3000);
          const thumbBlob = DriveApp.getFileById(payload.thumbnail_file_id).getBlob();
          YouTube.Thumbnails.set(videoId, thumbBlob);
          thumbnailSet = true;
        } catch (thumbErr) {
          thumbnailSet = false;
          thumbnailError = String(thumbErr);
        }
      }

      const youtubeUrl = "https://youtube.com/shorts/" + videoId;
      const channelId = (inserted.snippet && inserted.snippet.channelId) || "";
      let commentPosted = false;
      let commentError = "";
      if (privacy === "public" && payload.first_comment) {
        const cBody = {
          snippet: {
            videoId: videoId,
            topLevelComment: {
              snippet: { textOriginal: String(payload.first_comment).slice(0, 800) },
            },
          },
        };
        if (channelId) cBody.snippet.channelId = channelId;
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            Utilities.sleep(attempt === 0 ? 4000 : 5000);
            YouTube.CommentThreads.insert(cBody, "snippet");
            commentPosted = true;
            commentError = "";
            break;
          } catch (fcErr) {
            commentPosted = false;
            commentError = String(fcErr);
          }
        }
      }

      let repliedRequester = false;
      if (privacy === "public" && payload.reply_to_comment_id && payload.reply_comment_text) {
        try {
          const replyMsg = String(payload.reply_comment_text).replace("{{YOUTUBE_URL}}", youtubeUrl);
          YouTube.Comments.insert(
            {
              snippet: {
                parentId: String(payload.reply_to_comment_id),
                textOriginal: replyMsg.slice(0, 800),
              },
            },
            "snippet"
          );
          repliedRequester = true;
        } catch (rcErr) {
          repliedRequester = false;
        }
      }

      return _jsonResponse({
        ok: true,
        video_id: videoId,
        youtube_url: youtubeUrl,
        privacy: privacy,
        thumbnail_set: thumbnailSet,
        thumbnail_error: thumbnailError || null,
        comment_posted: commentPosted,
        comment_error: commentError || null,
        replied_requester: repliedRequester,
      });
    }

    if (action === "update_youtube_status") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube Data API v3 belum ditambahkan di Services (+)." });
      }
      const vid = String(payload.video_id || "").trim();
      const priv = payload.privacy_status === "public" ? "public" : (payload.privacy_status === "private" ? "private" : "unlisted");
      if (!vid) {
        return _jsonResponse({ ok: false, error: "video_id wajib diisi." });
      }
      const updStatus = { privacyStatus: priv, selfDeclaredMadeForKids: false };
      if (priv === "private" && payload.publish_at) {
        updStatus.publishAt = String(payload.publish_at);
      }
      const updated = YouTube.Videos.update({ id: vid, status: updStatus }, "status");
      let commentPosted = false;
      if (priv === "public" && payload.first_comment) {
        try {
          YouTube.CommentThreads.insert({
            snippet: { videoId: vid, topLevelComment: { snippet: { textOriginal: String(payload.first_comment).slice(0, 800) } } }
          }, "snippet");
          commentPosted = true;
        } catch (fcErr) {}
      }
      return _jsonResponse({
        ok: true,
        video_id: vid,
        privacy_status: (updated.status && updated.status.privacyStatus) || priv,
        publish_at: (updated.status && updated.status.publishAt) || payload.publish_at || null,
        comment_posted: commentPosted,
      });
    }

    if (action === "delete_youtube_video") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube Data API v3 belum ditambahkan di Services (+)." });
      }
      const vid = String(payload.video_id || "").trim();
      if (!vid) {
        return _jsonResponse({ ok: false, error: "video_id wajib diisi." });
      }
      YouTube.Videos.remove(vid);
      return _jsonResponse({ ok: true, video_id: vid, deleted: true });
    }

    if (action === "set_thumbnail") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube Data API v3 belum ditambahkan di Services (+)." });
      }
      const vid = String(payload.video_id || "").trim();
      const thumbFileId = String(payload.thumbnail_file_id || "").trim();
      if (!vid || !thumbFileId) {
        return _jsonResponse({ ok: false, error: "video_id dan thumbnail_file_id wajib diisi." });
      }
      let procStatus = "unknown";
      try {
        const vCheck = YouTube.Videos.list("processingDetails", { id: vid });
        const item = (vCheck.items && vCheck.items[0]) || null;
        if (item && item.processingDetails && item.processingDetails.processingStatus) {
          procStatus = item.processingDetails.processingStatus;
        }
      } catch (pcErr) {}
      if (procStatus === "processing") {
        return _jsonResponse({ ok: false, processing_status: procStatus, thumbnail_set: false, error: "YouTube still processing video" });
      }
      try {
        const thumbBlob = DriveApp.getFileById(thumbFileId).getBlob();
        YouTube.Thumbnails.set(vid, thumbBlob);
        return _jsonResponse({ ok: true, video_id: vid, processing_status: procStatus, thumbnail_set: true });
      } catch (thErr) {
        return _jsonResponse({ ok: false, video_id: vid, processing_status: procStatus, thumbnail_set: false, error: String(thErr) });
      }
    }

    if (action === "post_comment") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube Data API v3 belum ditambahkan di Services (+)." });
      }
      const vid = String(payload.video_id || "").trim();
      const txt = String(payload.comment_text || "").trim().slice(0, 800);
      if (!vid || !txt) {
        return _jsonResponse({ ok: false, error: "video_id dan comment_text wajib diisi." });
      }
      const cBody = {
        snippet: {
          videoId: vid,
          topLevelComment: { snippet: { textOriginal: txt } },
        },
      };
      if (payload.channel_id) cBody.snippet.channelId = String(payload.channel_id);
      const inserted = YouTube.CommentThreads.insert(cBody, "snippet");
      return _jsonResponse({ ok: true, video_id: vid, comment_id: inserted.id || null });
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

      if (!payload.skip_email) {
        MailApp.sendEmail({
          to: to,
          subject: subject,
          body: bodyLines.join("\n"),
        });
      }

      let sheetUpdated = false;
      const sheetId = payload.sheet_id || "1HltIwSA5VK5ehVg_WLhNf_oFCah2A2aJed6VahKYlHc";
      if (sheetId && Array.isArray(payload.sheet_row) && payload.sheet_row.length > 0 && typeof SpreadsheetApp !== "undefined") {
        try {
          const ss = SpreadsheetApp.openById(sheetId);
          let sh = ss.getSheetByName("Upload Video");
          if (!sh) {
            sh = ss.insertSheet("Upload Video");
            sh.appendRow([
              "Waktu (WIB)", "Episode ID", "Section", "Judul Video", "Disiplin",
              "Durasi (dtk)", "Skor QA", "Mode Tayang", "Link YouTube",
              "Link Google Drive", "Model AI Dipakai", "Request Penonton",
              "Status Notifikasi", "Waktu Produksi (dtk)"
            ]);
          }
          sh.appendRow(payload.sheet_row);
          sheetUpdated = true;
        } catch (shErr) {
          sheetUpdated = false;
        }
      }

      return _jsonResponse({ ok: true, emailed_to: payload.skip_email ? null : to, email_skipped: !!payload.skip_email, sheet_updated: sheetUpdated });
    }

    if (action === "sync_audit_sheet") {
      const sheetId = payload.sheet_id || "1HltIwSA5VK5ehVg_WLhNf_oFCah2A2aJed6VahKYlHc";
      if (!sheetId || typeof SpreadsheetApp === "undefined") {
        return _jsonResponse({ ok: false, error: "SpreadsheetApp atau sheet_id tidak tersedia." });
      }
      const ss = SpreadsheetApp.openById(sheetId);
      if (Array.isArray(payload.audit_row) && payload.audit_row.length > 0) {
        let aSh = ss.getSheetByName("Audit Mingguan & YPP");
        if (!aSh) aSh = ss.insertSheet("Audit Mingguan & YPP");
        aSh.appendRow(payload.audit_row);
      }
      if (Array.isArray(payload.request_rows)) {
        let rSh = ss.getSheetByName("Request Penonton");
        if (!rSh) rSh = ss.insertSheet("Request Penonton");
        rSh.clearContents();
        rSh.appendRow([
          "Request ID", "Tanggal", "Penonton (@Username)", "Komentar Asli",
          "Topik Sains", "Disiplin", "Status", "Link Video Jadi"
        ]);
        payload.request_rows.forEach(function (rw) {
          if (Array.isArray(rw)) rSh.appendRow(rw);
        });
      }
      return _jsonResponse({ ok: true, sheet_id: sheetId });
    }

    if (action === "get_youtube_stats") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube Data API v3 belum ditambahkan di Services (+)." });
      }
      const chResp = YouTube.Channels.list("snippet,statistics,contentDetails", { mine: true });
      const ch = (chResp.items && chResp.items[0]) || null;
      if (!ch) {
        return _jsonResponse({ ok: false, error: "Channel YouTube tidak ditemukan pada akun Google ini." });
      }
      const channelStats = {
        channel_id: ch.id,
        title: ch.snippet ? ch.snippet.title : "",
        subscribers: Number((ch.statistics && ch.statistics.subscriberCount) || 0),
        total_views: Number((ch.statistics && ch.statistics.viewCount) || 0),
        video_count: Number((ch.statistics && ch.statistics.videoCount) || 0),
      };

      const maxResults = Math.min(Number(payload.max_results || 15), 25);
      const uploadsId = ch.contentDetails && ch.contentDetails.relatedPlaylists && ch.contentDetails.relatedPlaylists.uploads;
      const videoIds = [];
      if (uploadsId) {
        const pl = YouTube.PlaylistItems.list("contentDetails", { playlistId: uploadsId, maxResults: maxResults });
        (pl.items || []).forEach(function (it) {
          if (it.contentDetails && it.contentDetails.videoId) videoIds.push(it.contentDetails.videoId);
        });
      }

      // Fetch private YouTube Studio Analytics (retention %, avg view duration, shares, subs gained)
      const analyticsById = {};
      let analyticsEnabled = false;
      let countriesBreakdown = [];
      if (typeof YouTubeAnalytics !== "undefined" && videoIds.length > 0) {
        try {
          const endDt = new Date();
          const startDt = new Date(endDt.getTime() - 28 * 24 * 3600 * 1000);
          const fmt = function (d) { return Utilities.formatDate(d, "Asia/Jakarta", "yyyy-MM-dd"); };
          const rep = YouTubeAnalytics.Reports.query({
            ids: "channel==MINE",
            startDate: fmt(startDt),
            endDate: fmt(endDt),
            metrics: "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained",
            dimensions: "video",
            filters: "video==" + videoIds.join(","),
            maxResults: maxResults,
          });
          analyticsEnabled = true;
          (rep.rows || []).forEach(function (row) {
            analyticsById[row[0]] = {
              studio_views: Number(row[1] || 0),
              watch_minutes: Number(row[2] || 0),
              avg_view_duration_sec: Number(row[3] || 0),
              avg_view_percentage: Number(row[4] || 0),
              studio_likes: Number(row[5] || 0),
              studio_comments: Number(row[6] || 0),
              shares: Number(row[7] || 0),
              subscribers_gained: Number(row[8] || 0),
            };
          });
          try {
            const ctryRep = YouTubeAnalytics.Reports.query({
              ids: "channel==MINE",
              startDate: fmt(startDt),
              endDate: fmt(endDt),
              metrics: "views,estimatedMinutesWatched",
              dimensions: "country",
              sort: "-views",
              maxResults: 15,
            });
            (ctryRep.rows || []).forEach(function (row) {
              countriesBreakdown.push({
                country: String(row[0] || "-"),
                views: Number(row[1] || 0),
                watch_minutes: Number(row[2] || 0),
              });
            });
          } catch (ctryErr) {}
        } catch (anaErr) {
          analyticsEnabled = false;
        }
      }

      // Fetch channel-wide newest comments (with commenter handle/name & comment_id for request queue + mentions)
      const recentComments = [];
      try {
        const allC = YouTube.CommentThreads.list("snippet", {
          allThreadsRelatedToChannelId: ch.id,
          maxResults: 45,
          order: "time",
        });
        (allC.items || []).forEach(function (c) {
          const top = c.snippet && c.snippet.topLevelComment;
          const sn = top && top.snippet;
          if (!sn || !sn.textDisplay) return;
          recentComments.push({
            comment_id: top.id || c.id || "",
            video_id: sn.videoId || (c.snippet && c.snippet.videoId) || "",
            author: String(sn.authorDisplayName || "Penonton").slice(0, 60),
            text: String(sn.textDisplay).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 260),
            like_count: Number(sn.likeCount || 0),
            published_at: sn.publishedAt || "",
          });
        });
      } catch (allCErr) {}

      const videos = [];
      if (videoIds.length > 0) {
        const vResp = YouTube.Videos.list("snippet,statistics,contentDetails", { id: videoIds.join(",") });
        (vResp.items || []).forEach(function (v) {
          const st = v.statistics || {};
          const ana = analyticsById[v.id] || {};
          const topComments = [];
          const commentItems = [];
          try {
            if (Number(st.commentCount || 0) > 0) {
              const cResp = YouTube.CommentThreads.list("snippet", { videoId: v.id, maxResults: 10, order: "time" });
              (cResp.items || []).forEach(function (c) {
                const top = c.snippet && c.snippet.topLevelComment;
                const sn = top && top.snippet;
                if (sn && sn.textDisplay) {
                  const cleanTxt = String(sn.textDisplay).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 220);
                  const author = String(sn.authorDisplayName || "Penonton").slice(0, 60);
                  topComments.push(author + ": " + cleanTxt.slice(0, 140));
                  commentItems.push({
                    comment_id: top.id || c.id || "",
                    video_id: v.id,
                    author: author,
                    text: cleanTxt,
                    like_count: Number(sn.likeCount || 0),
                    published_at: sn.publishedAt || "",
                  });
                }
              });
            }
          } catch (cErr) {}

          videos.push({
            video_id: v.id,
            youtube_url: "https://youtube.com/shorts/" + v.id,
            title: v.snippet ? v.snippet.title : "",
            published_at: v.snippet ? v.snippet.publishedAt : "",
            views: Number(st.viewCount || ana.studio_views || 0),
            likes: Number(st.likeCount || ana.studio_likes || 0),
            comments: Number(st.commentCount || ana.studio_comments || 0),
            shares: ana.shares !== undefined ? ana.shares : null,
            avg_view_duration_sec: ana.avg_view_duration_sec !== undefined ? ana.avg_view_duration_sec : null,
            avg_view_percentage: ana.avg_view_percentage !== undefined ? ana.avg_view_percentage : null,
            subscribers_gained: ana.subscribers_gained !== undefined ? ana.subscribers_gained : null,
            top_comments: topComments,
            comment_items: commentItems,
          });
        });
      }

      return _jsonResponse({
        ok: true,
        analytics_enabled: analyticsEnabled,
        channel: channelStats,
        videos: videos,
        recent_comments: recentComments,
        countries: countriesBreakdown,
      });
    }

    if (action === "list_flow_clips") {
      const rootName = payload.root_folder || DEFAULT_ROOT_FOLDER;
      const bankName = payload.folder_name || "Flow_Clip_Bank";
      const bankFolder = payload.folder_id
        ? DriveApp.getFolderById(String(payload.folder_id))
        : _getOrCreateFolder(bankName, _getOrCreateFolder(rootName, null));
      const clips = [];
      const files = bankFolder.getFiles();
      while (files.hasNext()) {
        const f = files.next();
        const nameLow = String(f.getName() || "").toLowerCase();
        const mime = String(f.getMimeType() || "").toLowerCase();
        const isVid = nameLow.endsWith(".mp4") || nameLow.endsWith(".mov") || nameLow.endsWith(".webm") || mime.indexOf("video/") === 0;
        if (!isVid) continue;
        const sz = f.getSize();
        if (sz <= 10240 || sz > 36 * 1024 * 1024) continue;
        clips.push({
          file_id: f.getId(),
          filename: f.getName(),
          mime_type: f.getMimeType(),
          size_bytes: sz,
          modified_time: f.getLastUpdated().toISOString(),
          web_url: f.getUrl(),
        });
      }
      return _jsonResponse({
        ok: true,
        folder_id: bankFolder.getId(),
        folder_url: bankFolder.getUrl(),
        clips: clips,
      });
    }

    if (action === "download_drive_file") {
      const fid = String(payload.file_id || "").trim();
      if (!fid) {
        return _jsonResponse({ ok: false, error: "file_id wajib diisi." });
      }
      const f = DriveApp.getFileById(fid);
      const sz = f.getSize();
      if (sz > 36 * 1024 * 1024) {
        return _jsonResponse({ ok: false, error: "File melebihi batas 36 MB Apps Script (" + sz + " bytes)." });
      }
      const blob = f.getBlob();
      return _jsonResponse({
        ok: true,
        file_id: f.getId(),
        filename: f.getName(),
        mime_type: blob.getContentType() || f.getMimeType(),
        size_bytes: sz,
        content_base64: Utilities.base64Encode(blob.getBytes()),
      });
    }

    if (action === "read_spreadsheet") {
      const sid = String(payload.sheet_id || "").trim();
      if (!sid) {
        return _jsonResponse({ ok: false, error: "sheet_id wajib diisi." });
      }
      const ss = SpreadsheetApp.openById(sid);
      const wantTabs = Array.isArray(payload.tabs) ? payload.tabs : null;
      const maxRows = Number(payload.max_rows || 200);
      const sheetsOut = {};
      ss.getSheets().forEach(function (sh) {
        const sname = sh.getName();
        if (wantTabs && wantTabs.indexOf(sname) === -1) return;
        const lastR = Math.min(sh.getLastRow(), maxRows);
        const lastC = Math.min(sh.getLastColumn(), 20);
        if (lastR <= 0 || lastC <= 0) {
          sheetsOut[sname] = [];
          return;
        }
        sheetsOut[sname] = sh.getSheetValues(1, 1, lastR, lastC);
      });
      return _jsonResponse({ ok: true, sheet_id: sid, title: ss.getName(), sheets: sheetsOut });
    }

    if (action === "send_custom_email") {
      const to = payload.to || DEFAULT_NOTIFY_EMAIL;
      const subject = payload.subject || "[Kimiabill] Laporan Otomatis";
      const body = payload.body || "";
      MailApp.sendEmail({ to: to, subject: subject, body: body });
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
