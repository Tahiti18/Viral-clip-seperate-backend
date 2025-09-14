<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>ClipGenius – Live Clip Preview</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 24px; background: #0b0c10; color: #e5e7eb; }
    .card { max-width: 900px; margin: 0 auto; background: #111318; border: 1px solid #1f2430; border-radius: 14px; padding: 20px; }
    h1 { margin: 0 0 12px; font-size: 28px; color: #7dd3fc; }
    .row { display: flex; gap: 8px; margin: 14px 0 8px; }
    input[type=text] { flex: 1; padding: 12px 14px; border-radius: 10px; border: 1px solid #2a3140; background: #0f1218; color: #e5e7eb; outline: none; }
    button { padding: 12px 14px; border-radius: 10px; border: 0; background: #22c55e; color: #061018; font-weight: 700; cursor: pointer; }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    .meta { opacity: 0.85; margin: 8px 0 14px; }
    iframe, video { width: 100%; aspect-ratio: 16/9; background: #000; border-radius: 12px; border: 1px solid #1f2430; }
    pre { background: #0f1218; border: 1px solid #1f2430; color: #cbd5e1; padding: 10px; border-radius: 10px; overflow: auto; }
    .error { color: #fda4af; }
  </style>
</head>
<body>
  <div class="card">
    <h1>ClipGenius — Live Clip Preview</h1>
    <div>Paste a YouTube link or a direct MP4 URL. We’ll analyze and preview the strongest segment.</div>

    <div class="row">
      <input id="videoUrl" type="text" placeholder="https://www.youtube.com/watch?v=..." />
      <button id="goBtn" onclick="loadClip()">Analyze & Preview</button>
    </div>

    <div id="status" class="meta"></div>
    <div id="preview"></div>
  </div>

  <script>
    // ---- CONFIG ----
    const BACKEND = "https://viral-clip-seperate-backend-production.up.railway.app";

    // ---- UTIL ----
    function hmsToSeconds(hms) {
      const [hh="0", mm="0", ss="0"] = String(hms || "0:0:0").split(":");
      return (+hh)*3600 + (+mm)*60 + (+ss);
    }
    function isYouTube(url) {
      return /(?:youtube\.com|youtu\.be)/i.test(url);
    }
    function getYouTubeId(url) {
      const m = url.match(/[?&]v=([^&#]+)/) || url.match(/youtu\.be\/([^?&#]+)/);
      return m ? m[1] : null;
    }

    // ---- MAIN ----
    async function loadClip() {
      const urlInput = document.getElementById('videoUrl');
      const goBtn = document.getElementById('goBtn');
      const statusEl = document.getElementById('status');
      const previewEl = document.getElementById('preview');
      const url = urlInput.value.trim();

      previewEl.innerHTML = "";
      statusEl.textContent = "";
      if (!url) { statusEl.innerHTML = '<span class="error">Paste a video URL.</span>'; return; }

      goBtn.disabled = true;
      statusEl.textContent = "Analyzing…";

      try {
        const resp = await fetch(`${BACKEND}/api/analyze-video`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            video_url: url,
            title: "ClipGenius Preview",
            description: "Live preview from index.html"
          })
        });
        const data = await resp.json();

        if (!resp.ok || !data || data.success === false) {
          const err = (data && (data.error || data.details)) || resp.statusText || "Unknown error";
          statusEl.innerHTML = `<span class="error">Error: ${err}</span>`;
          goBtn.disabled = false;
          return;
        }

        if (!data.clips || data.clips.length === 0) {
          statusEl.innerHTML = '<span class="error">No clips returned.</span>';
          goBtn.disabled = false;
          return;
        }

        // Show the strongest available clip (first in list)
        const clip = data.clips[0];
        const start = hmsToSeconds(clip.start_time || "00:00:00");
        const end   = hmsToSeconds(clip.end_time   || "00:00:10");

        statusEl.textContent = "Previewing top clip…";

        // Meta
        const meta = document.createElement('div');
        meta.className = "meta";
        meta.innerHTML = `
          <div><strong>${clip.title || "Suggested Clip"}</strong></div>
          <div>Segment: ${clip.start_time || "00:00:00"} → ${clip.end_time || "00:00:10"}</div>
          ${clip.hook ? `<div>Hook: ${clip.hook}</div>` : ""}
          ${clip.reason ? `<div>Reason: ${clip.reason}</div>` : ""}
          ${typeof clip.viral_score === "number" ? `<div>Viral Score: ${clip.viral_score}</div>` : ""}
          ${Array.isArray(clip.platforms) ? `<div>Platforms: ${clip.platforms.join(", ")}</div>` : ""}
        `;
        previewEl.appendChild(meta);

        if (isYouTube(url)) {
          // YouTube embed with start/end params
          const id = getYouTubeId(url);
          if (!id) {
            statusEl.innerHTML = '<span class="error">Could not parse YouTube video ID.</span>';
          } else {
            const params = new URLSearchParams({
              autoplay: "1",
              controls: "1",
              modestbranding: "1",
              rel: "0",
              start: String(start),
              end: String(end)
            });
            const iframe = document.createElement('iframe');
            iframe.src = `https://www.youtube.com/embed/${id}?${params.toString()}`;
            iframe.allow = "autoplay; encrypted-media";
            iframe.style.border = "0";
            previewEl.appendChild(iframe);
          }
        } else {
          // Direct MP4 or HTML5-playable URL
          const video = document.createElement('video');
          video.controls = true;
          video.playsInline = true;
          // Jump near start, then correct precisely
          video.src = url.includes("#t=") ? url : `${url}#t=${start}`;
          const onLoaded = () => { try { video.currentTime = start; } catch {} video.play().catch(()=>{}); };
          const onTimeUpdate = () => { if (video.currentTime >= end) video.pause(); };
          video.addEventListener("loadedmetadata", onLoaded);
          video.addEventListener("timeupdate", onTimeUpdate);
          previewEl.appendChild(video);
        }

        // Raw JSON for debugging (optional)
        const pre = document.createElement('pre');
        pre.textContent = JSON.stringify(clip, null, 2);
        previewEl.appendChild(pre);

      } catch (e) {
        statusEl.innerHTML = `<span class="error">Request failed: ${e.message || e}</span>`;
      } finally {
        goBtn.disabled = false;
      }
    }
  </script>
</body>
</html>
