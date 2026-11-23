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

      const youtubeUrl = "https://youtube.com/shorts/" + videoId;
      let commentPosted = false;
      if (privacy === "public" && payload.first_comment) {
        try {
          YouTube.CommentThreads.insert(
            {
              snippet: {
                videoId: videoId,
                topLevelComment: {
                  snippet: { textOriginal: String(payload.first_comment).slice(0, 800) },
                },
              },
            },
            "snippet"
          );
          commentPosted = true;
        } catch (fcErr) {
          commentPosted = false;
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
        comment_posted: commentPosted,
        replied_requester: repliedRequester,
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
      });
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
