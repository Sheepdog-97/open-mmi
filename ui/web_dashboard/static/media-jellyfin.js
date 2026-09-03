(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.openMmiJellyfinMedia = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatTime(seconds) {
    const n = Number(seconds);
    if (!Number.isFinite(n) || n < 0) return "--:--";
    const total = Math.floor(n);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h > 0
      ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
      : `${m}:${String(s).padStart(2, "0")}`;
  }


  const JELLYFIN_BLOCKING_PROVIDER_STATES = new Set([
    "connecting",
    "reconnecting",
    "authentication-error",
    "configuration-missing",
    "server-error",
  ]);

  function jellyfinProviderBlocksControls(
    providerStatus,
    sourceId = "jellyfin",
  ) {
    return sourceId === "jellyfin"
      && JELLYFIN_BLOCKING_PROVIDER_STATES.has(
        String(providerStatus || ""),
      );
  }

  function installController(options = {}) {
    const window = options.window || root;
    const document = options.document || window?.document;
    const apiClient = options.api || window?.openMmiApi;
    const reconnectApi = options.reconnect || window?.openMmiJellyfinReconnect;
    if (!window || !document || !apiClient || !reconnectApi) {
      throw new Error("Jellyfin media controller requires window, document, API client and reconnection controller");
    }
    if (window.__openMmiJellyfinMediaControllerLoaded && window.openMmiJellyfinPlayer) {
      return window.openMmiJellyfinPlayer;
    }
    window.__openMmiJellyfinMediaControllerLoaded = true;

    /*
      Jellyfin Media v5
      - actual Bootstrap classes for layout: container-fluid/row/col/card/d-flex/overflow/list-group/input-group/btn/progress
      - Bootstrap Icons-style inline SVG controls, so controls are icons again and do not rely on icon font loading
      - measured viewport height so the Media page fits above the dashboard footer/status strip
      - local browser audio is primary; remote Jellyfin Web session is secondary status only
    */
    const openMmiMedia = {
      queue: [],
      index: -1,
      current: null,
      bound: false,
      lastQuery: "",
      filter: "recent",
      loading: false,
      providerStatus: "idle",
      providerMessage: "",
    };
    let reconnectController = null;
    const performanceMetrics = { layout_requests: 0, layout_runs: 0, media_key_boots: 0 };
    window.openMmiMediaPerformanceMetrics = performanceMetrics;

    function ommiMediaIsActive() {
      return document.visibilityState !== "hidden"
        && document.querySelector("#pageElectrical.page-media")?.classList.contains("active");
    }

    function ommiMediaNotifyLayout(reason = "content") {
      performanceMetrics.layout_requests += 1;
      if (!ommiMediaIsActive()) return false;
      window.dispatchEvent(new window.CustomEvent("openmmi:medialayout", { detail: { reason } }));
      return true;
    }

    function ommiMediaEsc(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function ommiMediaText(value, fallback = "--") {
      return value === null || value === undefined || value === "" ? fallback : String(value);
    }

    function ommiMediaTime(seconds) {
      const n = Number(seconds);
      if (!Number.isFinite(n) || n < 0) return "--:--";
      const total = Math.floor(n);
      const h = Math.floor(total / 3600);
      const m = Math.floor((total % 3600) / 60);
      const s = total % 60;
      return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
    }

    function ommiMediaIcon(name, cls = "") {
      // Bootstrap Icons SVG paths, embedded inline so the controls work offline after the page loads.
      const paths = {
        "play-fill": '<path d="m11.596 8.697-6.363 3.692C4.693 12.702 4 12.323 4 11.692V4.308c0-.631.693-1.01 1.233-.697l6.363 3.692a.802.802 0 0 1 0 1.394z"/>',
        "pause-fill": '<path d="M5.5 3.5A1.5 1.5 0 0 1 7 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5zm5 0A1.5 1.5 0 0 1 12 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5z"/>',
        "skip-start-fill": '<path d="M4 4a.5.5 0 0 1 1 0v3.248l6.267-3.636c.54-.313 1.233.066 1.233.696v7.384c0 .63-.693 1.009-1.233.696L5 8.752V12a.5.5 0 0 1-1 0V4z"/>',
        "skip-end-fill": '<path d="M12.5 4a.5.5 0 0 0-1 0v3.248L5.233 3.612C4.693 3.299 4 3.678 4 4.308v7.384c0 .63.693 1.009 1.233.696L11.5 8.752V12a.5.5 0 0 0 1 0V4z"/>',
        "stop-fill": '<path d="M5 3.5h6A1.5 1.5 0 0 1 12.5 5v6A1.5 1.5 0 0 1 11 12.5H5A1.5 1.5 0 0 1 3.5 11V5A1.5 1.5 0 0 1 5 3.5z"/>',
        "search": '<path d="M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001c.03.04.062.078.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1.007 1.007 0 0 0-.115-.099zM12 6.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0z"/>',
        "clock-history": '<path d="M8.515 1.019A7 7 0 1 1 1.004 8.5.5.5 0 0 1 2 8a6 6 0 1 0 1.76-4.243l-.04.04h1.533a.5.5 0 0 1 0 1H2.5a.5.5 0 0 1-.5-.5V1.543a.5.5 0 0 1 1 0v1.52A6.974 6.974 0 0 1 8.515 1.019z"/><path d="M7.5 3a.5.5 0 0 1 .5.5v5.21l3.248 1.856a.5.5 0 0 1-.496.868l-3.5-2A.5.5 0 0 1 7 9V3.5a.5.5 0 0 1 .5-.5z"/>',
        "music-note-beamed": '<path d="M6 13c0 1.105-1.12 2-2.5 2S1 14.105 1 13s1.12-2 2.5-2 2.5.895 2.5 2z"/><path fill-rule="evenodd" d="M11 11V2h1v9h-1z"/><path d="M11 11c0 1.105-1.12 2-2.5 2S6 12.105 6 11s1.12-2 2.5-2 2.5.895 2.5 2z"/><path fill-rule="evenodd" d="M6 3v10H5V3h1z"/><path d="M5 2.905a1 1 0 0 1 .9-.995l6-.6a1 1 0 0 1 1.1.995V4L5 4.905v-2z"/>',
        "volume-up-fill": '<path d="M11.536 14.01A8.473 8.473 0 0 0 14.026 8a8.473 8.473 0 0 0-2.49-6.01l-.708.707A7.476 7.476 0 0 1 13.025 8c0 2.071-.84 3.946-2.197 5.303l.708.707z"/><path d="M10.121 12.596A6.48 6.48 0 0 0 12.025 8a6.48 6.48 0 0 0-1.904-4.596l-.707.707A5.48 5.48 0 0 1 11.025 8a5.48 5.48 0 0 1-1.61 3.89l.706.706z"/><path d="M8.707 11.182A4.486 4.486 0 0 0 10.025 8a4.486 4.486 0 0 0-1.318-3.182L8 5.525A3.489 3.489 0 0 1 9.025 8 3.49 3.49 0 0 1 8 10.475l.707.707zM6.717 3.55A.5.5 0 0 1 7 4v8a.5.5 0 0 1-.812.39L3.825 10.5H1.5A.5.5 0 0 1 1 10V6a.5.5 0 0 1 .5-.5h2.325l2.363-1.89a.5.5 0 0 1 .529-.06z"/>',
      };
      return `<svg class="bi ${cls}" xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" fill="currentColor" viewBox="0 0 16 16" aria-hidden="true" focusable="false">${paths[name] || paths["music-note-beamed"]}</svg>`;
    }

    // --- Open MMI Media icon/live search follow-up start ---
    const OMMI_MEDIA_LIVE_SEARCH_DELAY_MS = 320;

    function ommiMediaCleanMusicIcon(cls = "") {
      // A speaker silhouette is deliberately used instead of a note, disc, or
      // rounded-square/circle combination, which can resemble a social-media logo.
      const className = ["ommi-music-icon", "ommi-media-speaker-icon", cls]
        .filter(Boolean)
        .join(" ");
      return `<svg class="${className}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M4.5 9.25h3.2l4.55-3.45v12.4L7.7 14.75H4.5z"
          fill="currentColor"/>
        <path d="M15.15 9.35c1.45 1.45 1.45 3.85 0 5.3"
          fill="none" stroke="currentColor" stroke-width="1.65"
          stroke-linecap="round"/>
        <path d="M17.7 7.15c2.75 2.75 2.75 6.95 0 9.7"
          fill="none" stroke="currentColor" stroke-width="1.65"
          stroke-linecap="round"/>
      </svg>`;
    }

    function ommiMediaInvalidateRequest() {
      openMmiMedia.requestSerial = (Number(openMmiMedia.requestSerial) || 0) + 1;
      ommiMediaSetLoading(false);
    }

    function ommiMediaRunSearchNow(value) {
      if (openMmiMedia.searchTimer) {
        clearTimeout(openMmiMedia.searchTimer);
        openMmiMedia.searchTimer = null;
      }
      return ommiMediaLoadLibrary(value || "", openMmiMedia.filter);
    }

    function ommiMediaScheduleLiveSearch(input) {
      if (openMmiMedia.searchTimer) clearTimeout(openMmiMedia.searchTimer);
      ommiMediaInvalidateRequest();
      const value = input?.value || "";
      openMmiMedia.searchTimer = setTimeout(() => {
        openMmiMedia.searchTimer = null;
        ommiMediaLoadLibrary(value, openMmiMedia.filter);
      }, OMMI_MEDIA_LIVE_SEARCH_DELAY_MS);
    }

    function ommiMediaBindLiveSearch(root) {
      const input = root?.querySelector?.("#ommiMediaSearch");
      if (!input || input.dataset.openMmiLiveSearchBound === "true") return;

      input.dataset.openMmiLiveSearchBound = "true";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.placeholder = "Search music…";
      input.setAttribute("aria-label", "Search music; results update as you type");

      let composing = false;
      input.addEventListener("compositionstart", () => { composing = true; });
      input.addEventListener("compositionend", () => {
        composing = false;
        ommiMediaScheduleLiveSearch(input);
      });
      input.addEventListener("input", () => {
        if (!composing) ommiMediaScheduleLiveSearch(input);
      });
      input.addEventListener("keydown", (event) => {
        // Keep dashboard-wide shortcuts (H/Home and page arrows) out of text entry.
        // Native cursor movement is preserved; Enter remains an immediate search.
        event.stopPropagation();
        if (event.key === "Enter") {
          event.preventDefault();
          ommiMediaRunSearchNow(input.value || "");
        }
      });
      input.addEventListener("search", () => ommiMediaRunSearchNow(input.value || ""));
    }
    // --- Open MMI Media icon/live search follow-up end ---

    function ommiMediaPage() {
      let page = document.querySelector("#pageElectrical") || Array.from(document.querySelectorAll(".page"))[3];
      if (!page) {
        page = document.createElement("section");
        const footer = document.querySelector("footer.status-strip") || document.querySelector("footer");
        (footer?.parentNode || document.body).insertBefore(page, footer || null);
      }
      const active = page.classList.contains("active");
      page.id = "pageElectrical";
      page.className = `page page-media${active ? " active" : ""}`;
      page.setAttribute("aria-label", "Media page");

      document.querySelectorAll(".media-shell, .open-mmi-media-v2, .open-mmi-media-v3, .open-mmi-media-v4, .open-mmi-media-v5").forEach((node) => {
        if (!page.contains(node)) node.remove();
      });

      if (!page.querySelector("#openMmiMediaRoot")) {
        page.replaceChildren(ommiMediaBuildRoot());
        openMmiMedia.bound = false;
      }
      ommiMediaUpdatePagerLabels();
      ommiMediaInstallFilters();
      ommiMediaBind();
      if (window.openMmiMediaSources) window.openMmiMediaSources.apply();
      if (active) ommiMediaFitViewport();
      return page;
    }

    function ommiMediaBuildRoot() {
      const root = document.createElement("div");
      root.id = "openMmiMediaRoot";
      root.dataset.bootstrap = "true";
      root.className = "open-mmi-media-v5 container-fluid p-0 overflow-hidden";
      root.innerHTML = `
        <div class="row gx-2 gy-0 h-100 min-h-0 overflow-hidden ommi-media-row align-items-stretch">
          <section class="col-12 col-md-5 col-xl-4 h-100 min-h-0 d-flex overflow-hidden ommi-player-col" aria-label="Local Jellyfin player">
            <div class="card flex-fill h-100 min-h-0 overflow-hidden ommi-media-card">
              <div class="card-body d-flex flex-column min-h-0 h-100 gap-2 ommi-player-body">
                <div id="ommiMediaArt" class="ommi-art flex-shrink-0" aria-hidden="true"><span>${ommiMediaCleanMusicIcon("ommi-art-icon")}</span></div>
                <div class="min-w-0 flex-shrink-0 ommi-now-copy">
                  <div class="text-uppercase small text-secondary ommi-kicker">${ommiMediaIcon("volume-up-fill")} Local player</div>
                  <h2 id="ommiMediaTitle" class="ommi-now-title mb-1">Select music</h2>
                  <div id="ommiMediaSubtitle" class="ommi-now-subtitle text-secondary">Tap a track to play through this dashboard</div>
                  <div id="ommiMediaMessage" class="ommi-message text-secondary" role="status"></div>
                </div>
                <audio id="ommiMediaAudio" preload="metadata"></audio>
                <div id="ommiMediaProgressTrack" class="progress ommi-progress flex-shrink-0" role="slider" aria-label="Playback progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
                  <div id="ommiMediaProgressFill" class="progress-bar ommi-progress-fill" style="width:0%"></div>
                </div>
                <div class="d-flex justify-content-between small text-secondary ommi-time flex-shrink-0">
                  <span id="ommiMediaElapsed">--:--</span><span id="ommiMediaDuration">--:--</span>
                </div>
                <div class="btn-group w-100 flex-shrink-0 ommi-controls" role="group" aria-label="Playback controls">
                  <button type="button" id="ommiMediaPrev" class="btn btn-outline-light ommi-icon-btn" aria-label="Previous track">${ommiMediaIcon("skip-start-fill")}</button>
                  <button type="button" id="ommiMediaPlay" class="btn btn-info ommi-icon-btn ommi-play-btn" aria-label="Play">${ommiMediaIcon("play-fill")}</button>
                  <button type="button" id="ommiMediaNext" class="btn btn-outline-light ommi-icon-btn" aria-label="Next track">${ommiMediaIcon("skip-end-fill")}</button>
                  <button type="button" id="ommiMediaStop" class="btn btn-outline-secondary ommi-icon-btn" aria-label="Stop">${ommiMediaIcon("stop-fill")}</button>
                </div>
              </div>
            </div>
          </section>

          <section class="col-12 col-md-7 col-xl-8 h-100 min-h-0 d-flex overflow-hidden ommi-browser-col" aria-label="Jellyfin music browser">
            <div class="card flex-fill h-100 min-h-0 overflow-hidden ommi-media-card">
              <div class="card-body d-flex flex-column min-h-0 h-100 gap-2 ommi-browser-body">
                <div class="input-group input-group-lg flex-shrink-0 ommi-search">
                  <input id="ommiMediaSearch" type="search" class="form-control" autocomplete="off" placeholder="Search songs, artists, albums" aria-label="Search Jellyfin music">
                  <button type="button" id="ommiMediaSearchBtn" class="btn btn-outline-light ommi-search-btn" aria-label="Search">${ommiMediaIcon("search")}</button>
                  <button type="button" id="ommiMediaRecentBtn" class="btn btn-outline-secondary ommi-recent-btn" aria-label="Recent music">${ommiMediaIcon("clock-history")}</button>
                </div>
                <div class="d-flex justify-content-between align-items-center flex-shrink-0 ommi-list-heading">
                  <span id="ommiMediaListTitle">Recent music</span>
                  <span class="d-inline-flex align-items-center gap-2">
                    <button type="button" id="ommiMediaRetry" class="btn btn-sm btn-outline-warning" hidden>Retry</button>
                    <small id="ommiMediaRemoteState" class="text-secondary ommi-remote-state">--</small>
                    <span id="ommiMediaCount" class="badge rounded-pill text-bg-secondary">--</span>
                  </span>
                </div>
                <div id="ommiMediaResults" class="list-group list-group-flush flex-grow-1 min-h-0 overflow-auto ommi-results" role="list" aria-label="Tracks"></div>
              </div>
            </div>
          </section>
        </div>`;
      return root;
    }

    function ommiMediaFitViewport() {
      const page = document.querySelector("#pageElectrical.page-media");
      const root = document.querySelector("#openMmiMediaRoot");
      if (!page || !root) return;

      const pageRect = page.getBoundingClientRect();
      let bottom = window.innerHeight;
      const candidates = Array.from(document.querySelectorAll("footer, .footer, .status-strip, .nav-dots, .pager, .page-dots, .dashboard-footer, .bottom-bar"));
      for (const el of candidates) {
        if (page.contains(el) || !el.getClientRects().length) continue;
        const rect = el.getBoundingClientRect();
        if (rect.top > pageRect.top + 20 && rect.top < bottom) bottom = rect.top;
      }

      const safeGap = 8;
      const height = Math.max(220, Math.floor(bottom - pageRect.top - safeGap));
      root.style.height = `${height}px`;
      root.style.maxHeight = `${height}px`;
      root.style.minHeight = "0";
    }

    function ommiMediaUpdatePagerLabels() {
      document.querySelectorAll('[data-page="3"]').forEach((button) => {
        button.title = "Media";
        button.setAttribute("aria-label", "Media");
      });
      if (document.querySelector('[data-page="3"].active')) {
        const title = document.querySelector("#pageTitle");
        if (title) title.textContent = "Media";
      }
    }

    function ommiMediaSetMessage(text, kind = "") {
      const el = document.querySelector("#ommiMediaMessage");
      if (!el) return;
      el.textContent = text || "";
      el.className = `ommi-message ${kind === "error" ? "text-danger" : "text-secondary"}`;
    }

    function ommiMediaSetArtwork(track) {
      const art = document.querySelector("#ommiMediaArt");
      if (!art) return;
      if (track?.image_url) {
        art.classList.add("has-art");
        art.innerHTML = `<img src="${ommiMediaEsc(track.image_url)}" alt="">`;
      } else {
        art.classList.remove("has-art");
        art.innerHTML = `<span>${ommiMediaCleanMusicIcon("ommi-art-icon")}</span>`;
      }
    }

    function ommiMediaSetNowPlaying(track) {
      const title = document.querySelector("#ommiMediaTitle");
      const subtitle = document.querySelector("#ommiMediaSubtitle");
      if (!title || !subtitle) return;
      if (!track) {
        title.textContent = "Select music";
        subtitle.textContent = "Tap a track to play through this dashboard";
        ommiMediaSetArtwork(null);
        return;
      }
      title.textContent = ommiMediaText(track.name, "Untitled");
      subtitle.textContent = [track.artist, track.album].filter(Boolean).join(" · ") || "Jellyfin music";
      ommiMediaSetArtwork(track);
    }

    function ommiMediaUpdateProgress() {
      const audio = document.querySelector("#ommiMediaAudio");
      const elapsed = document.querySelector("#ommiMediaElapsed");
      const duration = document.querySelector("#ommiMediaDuration");
      const fill = document.querySelector("#ommiMediaProgressFill");
      const track = document.querySelector("#ommiMediaProgressTrack");
      if (!audio || !elapsed || !duration || !fill) return;
      const dur = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : Number(openMmiMedia.current?.duration_seconds || 0);
      const pct = dur > 0 ? Math.max(0, Math.min(100, (audio.currentTime / dur) * 100)) : 0;
      elapsed.textContent = ommiMediaTime(audio.currentTime);
      duration.textContent = ommiMediaTime(dur);
      fill.style.width = `${pct}%`;
      track?.setAttribute("aria-valuenow", String(Math.round(pct)));
    }

    function ommiMediaUpdatePlayState() {
      const audio = document.querySelector("#ommiMediaAudio");
      const play = document.querySelector("#ommiMediaPlay");
      if (play && audio) {
        play.innerHTML = audio.paused ? ommiMediaIcon("play-fill") : ommiMediaIcon("pause-fill");
        play.setAttribute("aria-label", audio.paused ? "Play" : "Pause");
      }
    }

    async function ommiMediaFetchJson(path) {
      return apiClient.getJson(path);
    }

    function ommiMediaProviderActive() {
      const page = document.querySelector("#pageElectrical.page-media");
      const usesJellyfin = !window.openMmiMediaSources || window.openMmiMediaSources.shouldUseJellyfin();
      return Boolean(page?.classList.contains("active") && usesJellyfin && !document.hidden);
    }

    function ommiMediaSyncLiveControls() {
      let sourceId = "jellyfin";
      try {
        const active = window.openMmiMediaSources?.activeSourceId?.();
        if (typeof active === "string" && active) sourceId = active;
      } catch (_) {}

      const unavailable = jellyfinProviderBlocksControls(
        openMmiMedia.providerStatus,
        sourceId,
      );
      document.querySelectorAll("#ommiMediaSearchBtn, #ommiMediaFilter, [data-open-mmi-track]").forEach((control) => {
        control.disabled = Boolean(openMmiMedia.loading || unavailable);
        control.setAttribute("aria-disabled", String(control.disabled));
      });
    }

    function ommiMediaApplyProviderState(snapshot = {}) {
      const status = String(snapshot.status || "idle");
      const message = String(snapshot.message || "");
      openMmiMedia.providerStatus = status;
      openMmiMedia.providerMessage = message;
      const rootNode = document.querySelector("#openMmiMediaRoot");
      if (rootNode) rootNode.dataset.jellyfinState = status;

      const remote = document.querySelector("#ommiMediaRemoteState");
      if (remote) {
        const labels = {
          idle: "--",
          connecting: "CONNECTING",
          ready: "READY",
          reconnecting: "RECONNECTING",
          "configuration-missing": "NOT CONFIGURED",
          "authentication-error": "AUTH ERROR",
          "server-error": "SERVER ERROR",
        };
        remote.textContent = labels[status] || status.toUpperCase();
        remote.title = message;
      }

      const retry = document.querySelector("#ommiMediaRetry");
      if (retry) retry.hidden = !["reconnecting", "authentication-error", "server-error"].includes(status);

      if (status === "connecting") ommiMediaSetMessage("Connecting to Jellyfin…");
      else if (status === "reconnecting") ommiMediaSetMessage("Jellyfin reconnecting…", "error");
      else if (status === "configuration-missing") ommiMediaSetMessage(message || "Jellyfin is not configured.", "error");
      else if (status === "authentication-error") ommiMediaSetMessage(message || "Jellyfin credentials were rejected.", "error");
      else if (status === "server-error") ommiMediaSetMessage(message || "Jellyfin returned an error.", "error");
      ommiMediaSyncLiveControls();
    }

    function ommiMediaReconnectController() {
      if (reconnectController) return reconnectController;
      reconnectController = reconnectApi.createController({
        window,
        document,
        requestStatus: () => ommiMediaFetchJson("/api/jellyfin/status"),
        isActive: ommiMediaProviderActive,
        onStateChange: ommiMediaApplyProviderState,
        onRecovered: () => {
          ommiMediaSetMessage("Jellyfin is available again.");
          void ommiMediaLoadLibrary(openMmiMedia.lastQuery, openMmiMedia.filter);
        },
      });
      return reconnectController;
    }

    const OMMI_MEDIA_FILTERS = {
      recent: "Recent music",
      favorites: "Favourites",
      az: "A–Z",
    };

    function ommiMediaUpdateFilters() {
      const select = document.querySelector("#ommiMediaFilter");
      if (select && select.value !== openMmiMedia.filter) {
        select.value = openMmiMedia.filter;
      }
    }

    function ommiMediaInstallFilters() {
      let select = document.querySelector("#ommiMediaFilter");
      const recent = document.querySelector("#ommiMediaRecentBtn");

      if (!select && recent) {
        select = document.createElement("select");
        select.id = "ommiMediaFilter";
        select.className = "form-select ommi-filter-select";
        select.setAttribute("aria-label", "Music library view");
        select.title = "Choose music library view";

        Object.entries(OMMI_MEDIA_FILTERS).forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          select.appendChild(option);
        });

        select.addEventListener("change", () => {
          const input = document.querySelector("#ommiMediaSearch");
          if (input) input.value = "";
          openMmiMedia.filter = select.value || "recent";
          ommiMediaRunSearchNow("");
        });
        recent.replaceWith(select);
      }

      // Remove the previous button implementation when upgrading an already-patched UI.
      document.querySelectorAll("[data-open-mmi-filter]").forEach((button) => button.remove());

      const search = document.querySelector("#ommiMediaSearchBtn");
      if (search) {
        search.title = "Search music";
        search.setAttribute("aria-label", "Search music");
      }
      ommiMediaUpdateFilters();
    }

    function ommiMediaSetLoading(loading) {
      openMmiMedia.loading = Boolean(loading);
      const root = document.querySelector("#openMmiMediaRoot");
      root?.setAttribute("aria-busy", String(openMmiMedia.loading));
      document
        .querySelectorAll("#ommiMediaSearchBtn, #ommiMediaFilter")
        .forEach((control) => {
          control.disabled = Boolean(openMmiMedia.loading || openMmiMedia.providerStatus !== "ready");
          control.setAttribute("aria-disabled", String(control.disabled));
        });
      ommiMediaSyncLiveControls();
    }

    function ommiMediaRenderResults(items) {
      const results = document.querySelector("#ommiMediaResults");
      const count = document.querySelector("#ommiMediaCount");
      if (!results) return;

      openMmiMedia.queue = Array.isArray(items) ? items.filter((item) => item && item.id) : [];
      if (count) count.textContent = String(openMmiMedia.queue.length);

      if (!openMmiMedia.queue.length) {
        results.innerHTML = `<div class="ommi-empty">No tracks found. Try search, or check <code>/api/jellyfin/search?limit=5</code>.</div>`;
        ommiMediaNotifyLayout("results-empty");
        return;
      }

      results.innerHTML = openMmiMedia.queue.map((item, index) => `
        <button type="button" class="list-group-item list-group-item-action d-grid ommi-track" data-open-mmi-track="${index}" role="listitem" aria-label="Play ${ommiMediaEsc(item.name || "track")}">
          <span class="ommi-track-art">${item.image_url ? `<img src="${ommiMediaEsc(item.image_url)}" alt="">` : ommiMediaCleanMusicIcon()}</span>
          <span class="ommi-track-copy"><strong>${ommiMediaEsc(item.name || "Untitled")}</strong><small>${ommiMediaEsc([item.artist, item.album].filter(Boolean).join(" · ") || "Unknown artist")}</small></span>
          <span class="ommi-track-duration">${ommiMediaTime(item.duration_seconds)}</span>
        </button>`).join("");
      ommiMediaNotifyLayout("results-rendered");
    }

    async function ommiMediaLoadLibrary(query = "", filter = openMmiMedia.filter) {
      ommiMediaPage();
      ommiMediaInstallFilters();
      if (window.openMmiMediaSources && !window.openMmiMediaSources.shouldUseJellyfin()) {
        window.openMmiMediaSources.renderPlaceholder();
        return;
      }
      const listTitle = document.querySelector("#ommiMediaListTitle");
      const q = String(query || "").trim();
      const selectedFilter = Object.prototype.hasOwnProperty.call(OMMI_MEDIA_FILTERS, filter)
        ? filter
        : "recent";
      const requestSerial = (Number(openMmiMedia.requestSerial) || 0) + 1;
      openMmiMedia.requestSerial = requestSerial;
      openMmiMedia.lastQuery = q;
      openMmiMedia.filter = selectedFilter;
      ommiMediaUpdateFilters();
      if (listTitle) {
        listTitle.textContent = q
          ? `Search results · ${OMMI_MEDIA_FILTERS[selectedFilter]}`
          : OMMI_MEDIA_FILTERS[selectedFilter];
      }
      ommiMediaSetMessage(q ? "Searching…" : `Loading ${OMMI_MEDIA_FILTERS[selectedFilter].toLowerCase()}…`);
      ommiMediaSetLoading(true);
      try {
        const params = new URLSearchParams({
          q,
          limit: "60",
          filter: selectedFilter,
        });
        const payload = await ommiMediaFetchJson(`/api/jellyfin/search?${params}`);
        if (requestSerial !== openMmiMedia.requestSerial) return;
        ommiMediaReconnectController().reportPayload(payload);
        if (payload.error) {
          ommiMediaSetMessage(payload.error, "error");
          if (!openMmiMedia.queue.length) ommiMediaRenderResults([]);
        } else {
          ommiMediaSetMessage("Tap any track to play locally.");
          ommiMediaRenderResults(payload.items || []);
        }
      } catch (err) {
        if (requestSerial !== openMmiMedia.requestSerial) return;
        ommiMediaReconnectController().reportFailure(err);
        ommiMediaSetMessage(`Could not load library: ${err.message}`, "error");
        if (!openMmiMedia.queue.length) ommiMediaRenderResults([]);
      } finally {
        if (requestSerial === openMmiMedia.requestSerial) ommiMediaSetLoading(false);
      }
      if (requestSerial === openMmiMedia.requestSerial) ommiMediaFitViewport();
    }

    async function ommiMediaRefreshStatus() {
      ommiMediaPage();
      if (window.openMmiMediaSources && !window.openMmiMediaSources.shouldUseJellyfin()) {
        window.openMmiMediaSources.renderPlaceholder();
        return null;
      }
      return ommiMediaReconnectController().refreshNow("media-status");
    }

    async function ommiMediaPlayIndex(index) {
      ommiMediaPage();
      if (window.openMmiMediaSources && !window.openMmiMediaSources.shouldUseJellyfin()) { window.openMmiMediaSources.renderPlaceholder(); return; }
      const audio = document.querySelector("#ommiMediaAudio");
      const item = openMmiMedia.queue[Number(index)];
      if (!audio || !item) return;

      openMmiMedia.index = Number(index);
      openMmiMedia.current = item;
      ommiMediaSetNowPlaying(item);
      ommiMediaSetMessage("Loading audio…");

      document.querySelectorAll(".ommi-track.is-playing").forEach((node) => node.classList.remove("is-playing", "active"));
      document.querySelector(`[data-open-mmi-track="${openMmiMedia.index}"]`)?.classList.add("is-playing", "active");

      audio.src = `/api/jellyfin/stream/${encodeURIComponent(item.id)}`;
      audio.load();
      try {
        await audio.play();
        ommiMediaSetMessage("Playing locally on this dashboard.");
      } catch (err) {
        ommiMediaSetMessage(`Tap play to start audio: ${err.message}`, "error");
      }
      ommiMediaUpdatePlayState();
      ommiMediaFitViewport();
    }

    function ommiMediaNext() {
      if (!openMmiMedia.queue.length) return;
      const next = openMmiMedia.index < 0 ? 0 : (openMmiMedia.index + 1) % openMmiMedia.queue.length;
      ommiMediaPlayIndex(next);
    }

    function ommiMediaPrev() {
      if (!openMmiMedia.queue.length) return;
      const prev = openMmiMedia.index <= 0 ? openMmiMedia.queue.length - 1 : openMmiMedia.index - 1;
      ommiMediaPlayIndex(prev);
    }

    function ommiMediaBind() {
      if (openMmiMedia.bound) return;
      const root = document.querySelector("#openMmiMediaRoot");
      const audio = document.querySelector("#ommiMediaAudio");
      if (!root || !audio) return;
      ommiMediaBindLiveSearch(root);

      root.addEventListener("click", async (event) => {
        const trackButton = event.target.closest?.("[data-open-mmi-track]");
        if (trackButton) {
          event.preventDefault();
          await ommiMediaPlayIndex(trackButton.dataset.openMmiTrack);
          return;
        }
        if (event.target.closest?.("#ommiMediaRetry")) {
          ommiMediaSetMessage("Retrying Jellyfin…");
          return ommiMediaReconnectController().retryNow();
        }
        if (event.target.closest?.("#ommiMediaSearchBtn")) {
          return ommiMediaRunSearchNow(
            document.querySelector("#ommiMediaSearch")?.value || "",
          );
        }
        if (event.target.closest?.("#ommiMediaPrev")) return ommiMediaPrev();
        if (event.target.closest?.("#ommiMediaNext")) return ommiMediaNext();
        if (event.target.closest?.("#ommiMediaStop")) {
          audio.pause();
          audio.currentTime = 0;
          openMmiMedia.current = null;
          openMmiMedia.index = -1;
          ommiMediaSetNowPlaying(null);
          document.querySelectorAll(".ommi-track.is-playing").forEach((node) => node.classList.remove("is-playing", "active"));
          ommiMediaUpdateProgress();
          ommiMediaUpdatePlayState();
          ommiMediaSetMessage("Stopped.");
          return;
        }
        if (event.target.closest?.("#ommiMediaPlay")) {
          if (!openMmiMedia.current && openMmiMedia.queue.length) return ommiMediaPlayIndex(0);
          if (audio.paused) {
            try { await audio.play(); ommiMediaSetMessage("Playing locally on this dashboard."); }
            catch (err) { ommiMediaSetMessage(`Could not start audio: ${err.message}`, "error"); }
          } else {
            audio.pause();
          }
          ommiMediaUpdatePlayState();
          return;
        }
        const progress = event.target.closest?.("#ommiMediaProgressTrack");
        if (progress && Number.isFinite(audio.duration) && audio.duration > 0) {
          const rect = progress.getBoundingClientRect();
          const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
          audio.currentTime = ratio * audio.duration;
          ommiMediaUpdateProgress();
        }
      });

      root.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.target?.id === "ommiMediaSearch") {
          event.preventDefault();
          ommiMediaRunSearchNow(event.target.value || "");
        }
      });

      audio.addEventListener("timeupdate", ommiMediaUpdateProgress);
      audio.addEventListener("durationchange", ommiMediaUpdateProgress);
      audio.addEventListener("play", ommiMediaUpdatePlayState);
      audio.addEventListener("pause", ommiMediaUpdatePlayState);
      audio.addEventListener("ended", ommiMediaNext);
      audio.addEventListener("error", () => ommiMediaSetMessage("Audio stream failed. Check Jellyfin access and codec support.", "error"));

      window.addEventListener("resize", ommiMediaFitViewport);
      window.addEventListener("orientationchange", () => setTimeout(ommiMediaFitViewport, 100));
      openMmiMedia.bound = true;
    }


    // Media source shell and Radio privacy are owned by media.js and media-radio.js.


    function ommiMediaBoot() {
      ommiMediaPage();
      if (window.openMmiMediaSources && !window.openMmiMediaSources.shouldUseJellyfin()) { window.openMmiMediaSources.renderPlaceholder(); return; }
      ommiMediaSetNowPlaying(openMmiMedia.current);
      ommiMediaReconnectController().start();
      if (!openMmiMedia.queue.length) ommiMediaLoadLibrary("");
    }

    function boot() {
      if (openMmiMedia.booted) return false;
      openMmiMedia.booted = true;
      const syncActiveMedia = () => {
        const page = document.querySelector("#pageElectrical");
        if (!page?.classList.contains("active") || document.visibilityState === "hidden") return;
        ommiMediaPage();
        ommiMediaNotifyLayout("page-active");
      };
      ommiMediaBoot();
      document.addEventListener("DOMContentLoaded", ommiMediaBoot);
      window.addEventListener("openmmi:pagechange", syncActiveMedia);
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState !== "hidden") syncActiveMedia();
      });
      return true;
    }
    // --- Open MMI Jellyfin real Bootstrap media v5 end ---


    // --- Open MMI Jellyfin viewport v6 start ---
    /*
      Corrective viewport fit for the Media page.
      The previous Bootstrap pass measured the available height but then put a
      height:100% row with vertical gutters and full-height cards inside it, which
      caused the bottom of the player/card to be clipped. This wrapper replaces the
      fit function without touching the Jellyfin API/audio code.
    */
    try {
      const ommiPreviousMediaFitViewport = typeof ommiMediaFitViewport === "function" ? ommiMediaFitViewport : null;
      ommiMediaFitViewport = function ommiMediaFitViewportV6() {
        const page = document.querySelector("#pageElectrical.page-media");
        const root = document.querySelector("#openMmiMediaRoot");
        if (!page || !root) {
          if (ommiPreviousMediaFitViewport) ommiPreviousMediaFitViewport();
          return;
        }
        if (!page.classList.contains("active") || document.visibilityState === "hidden") return;
        performanceMetrics.layout_runs += 1;

        const pageRect = page.getBoundingClientRect();
        let bottom = window.innerHeight;
        const selectors = [
          "footer", ".footer", ".status-strip", ".nav-dots", ".pager", ".page-dots",
          ".dashboard-footer", ".bottom-bar", ".footer-status", ".status-row"
        ].join(",");

        for (const el of Array.from(document.querySelectorAll(selectors))) {
          if (page.contains(el) || !el.getClientRects().length) continue;
          const style = window.getComputedStyle(el);
          if (style.display === "none" || style.visibility === "hidden") continue;
          const rect = el.getBoundingClientRect();
          if (rect.top > pageRect.top + 16 && rect.top < bottom) bottom = rect.top;
        }

        // Leave enough gap for the page/card rounded corners to be visible instead
        // of ending exactly at the footer boundary and looking clipped.
        const safeGap = 14;
        const height = Math.max(220, Math.floor(bottom - pageRect.top - safeGap));
        page.style.setProperty("--ommi-media-viewport-height", `${height}px`);
        page.style.height = `${height}px`;
        page.style.maxHeight = `${height}px`;
        root.style.height = "100%";
        root.style.maxHeight = "100%";
        root.style.minHeight = "0";
      };

      window.addEventListener("resize", () => requestAnimationFrame(ommiMediaFitViewport));
      window.addEventListener("orientationchange", () => setTimeout(ommiMediaFitViewport, 150));
      requestAnimationFrame(ommiMediaFitViewport);
    } catch (error) {
      console.warn("Open MMI Media viewport v6 failed", error);
    }
    // --- Open MMI Jellyfin viewport v6 end ---


    // --- Open MMI Jellyfin render stability v8b start ---
    /*
      Tolerant Media layout stabiliser.

      This patch intentionally avoids replacing the Jellyfin player functions; the
      current app.js has drifted through several small fixes, so exact function-name
      matching is fragile. Instead it stabilises the rendered Media DOM:
        - marks the active Media page/root with v8b classes,
        - reserves the track-list slot immediately with skeleton rows,
        - keeps page/root scroll at the top,
        - lets only #ommiMediaResults scroll,
        - reruns the existing viewport fit hook when the list mutates.
    */
    (function () {
      const PAGE_SELECTOR = "#pageElectrical.page-media, #pageElectrical";
      const ROOT_SELECTOR = "#openMmiMediaRoot";
      const RESULTS_SELECTOR = "#ommiMediaResults";

      function fit() {
        const page = document.querySelector(PAGE_SELECTOR);
        const root = document.querySelector(ROOT_SELECTOR);
        const results = document.querySelector(RESULTS_SELECTOR);

        if (page) {
          page.classList.add("page-media-v8b");
          if (page.classList.contains("active")) page.scrollTop = 0;
        }
        if (root) {
          root.classList.add("open-mmi-media-v8b");
          root.scrollTop = 0;
        }
        if (results) {
          results.classList.add("ommi-results-v8b");
          if (!results.dataset.openMmiUserScrolled) results.scrollTop = 0;
        }

        if (typeof window.ommiMediaFitViewport === "function") {
          try { window.ommiMediaFitViewport(); } catch (_) {}
        } else if (typeof ommiMediaFitViewport === "function") {
          try { ommiMediaFitViewport(); } catch (_) {}
        }
      }

      function skeleton() {
        const results = document.querySelector(RESULTS_SELECTOR);
        if (!results) return;
        const hasRealRows = results.querySelector(".ommi-track:not(.ommi-track-skeleton-v8b), .list-group-item:not(.ommi-track-skeleton-v8b)");
        const hasChildren = results.children.length > 0;
        if (hasRealRows || hasChildren) return;

        results.dataset.openMmiState = "loading";
        results.innerHTML = Array.from({ length: 8 }, (_, index) => `
          <div class="ommi-track ommi-track-skeleton-v8b" aria-hidden="true">
            <span class="ommi-track-art ommi-skeleton-box-v8b"></span>
            <span class="ommi-track-copy">
              <strong class="ommi-skeleton-line-v8b ${index % 3 === 0 ? "is-short" : ""}"></strong>
              <small class="ommi-skeleton-line-v8b ${index % 2 === 0 ? "is-tiny" : ""}"></small>
            </span>
            <span class="ommi-track-duration ommi-skeleton-line-v8b is-time"></span>
          </div>`).join("");
      }

      function stabilise() {
        if (!ommiMediaIsActive()) return;
        skeleton();
        fit();
        requestAnimationFrame(fit);
      }

      function bindResultsScroll() {
        const results = document.querySelector(RESULTS_SELECTOR);
        if (!results || results.__openMmiV8bScrollBound) return;
        results.addEventListener("scroll", () => {
          if (results.scrollTop > 8) results.dataset.openMmiUserScrolled = "1";
        }, { passive: true });
        results.__openMmiV8bScrollBound = true;
      }

      document.addEventListener("DOMContentLoaded", () => {
        bindResultsScroll();
        stabilise();
        setTimeout(stabilise, 250);
        setTimeout(stabilise, 1000);
      });

      window.addEventListener("resize", () => requestAnimationFrame(stabilise));
      window.addEventListener("orientationchange", () => setTimeout(stabilise, 150));
      window.addEventListener("openmmi:medialayout", () => requestAnimationFrame(stabilise));
      window.addEventListener("openmmi:pagechange", () => requestAnimationFrame(stabilise));

      // If this script is appended after DOMContentLoaded, run immediately too.
      if (document.readyState !== "loading") {
        bindResultsScroll();
        stabilise();
        setTimeout(stabilise, 250);
        setTimeout(stabilise, 1000);
      }
    })();
    // --- Open MMI Jellyfin render stability v8b end ---


    // --- Open MMI media early footer guard start ---
    /*
      Keep the Media page inside the content row immediately, before the Jellyfin
      library request completes. This avoids the brief first-paint overlap where the
      Media player can cover the bottom status/nav strip and then snap back.
    */
    (function () {
      function clampMediaToContentRow() {
        const page = document.querySelector("#pageElectrical.page-media, #pageElectrical");
        const root = document.querySelector("#openMmiMediaRoot");
        const screen = document.querySelector(".screen");
        const footer = document.querySelector(".status-strip");
        if (!page || !screen) return;

        const screenRect = screen.getBoundingClientRect();
        const pageRect = page.getBoundingClientRect();
        const footerRect = footer ? footer.getBoundingClientRect() : null;
        const bottom = footerRect ? footerRect.top : screenRect.bottom;
        const available = Math.max(180, Math.floor(bottom - pageRect.top));

        page.style.height = `${available}px`;
        page.style.maxHeight = `${available}px`;
        page.style.minHeight = "0";
        page.style.overflow = "hidden";

        if (root) {
          root.style.height = "100%";
          root.style.maxHeight = "100%";
          root.style.minHeight = "0";
          root.style.overflow = "hidden";
        }

        const results = document.querySelector("#ommiMediaResults");
        if (results) {
          results.style.minHeight = "0";
          results.style.overflowY = "auto";
          results.style.overflowX = "hidden";
        }
      }

      function scheduleClamp() {
        if (!ommiMediaIsActive()) return;
        clampMediaToContentRow();
        requestAnimationFrame(clampMediaToContentRow);
        setTimeout(clampMediaToContentRow, 80);
        setTimeout(clampMediaToContentRow, 300);
      }

      document.addEventListener("DOMContentLoaded", scheduleClamp);
      window.addEventListener("resize", scheduleClamp);
      window.addEventListener("orientationchange", () => setTimeout(scheduleClamp, 150));
      window.addEventListener("openmmi:medialayout", scheduleClamp);
      window.addEventListener("openmmi:pagechange", scheduleClamp);
      if (document.readyState !== "loading") scheduleClamp();
    })();
    // --- Open MMI media early footer guard end ---


    // --- Open MMI media footer scoped repair start ---
    /*
      Media-only footer clamp.

      The previous first-paint fix used global .screen/.page grid rules, which could
      break Drive/Climate/Vehicle. This repair measures the actual distance from the
      active Media page top to the status/footer strip and applies that height only
      to #pageElectrical.page-media and #openMmiMediaRoot.
    */
    (function () {
      const PAGE_SELECTOR = "#pageElectrical.page-media, #pageElectrical";
      const ROOT_SELECTOR = "#openMmiMediaRoot";
      const FOOTER_SELECTOR = ".status-strip, footer.status-strip, .screen > footer";

      let raf = 0;

      function getMediaPage() {
        const page = document.querySelector(PAGE_SELECTOR);
        if (!page) return null;
        // Only clamp the Jellyfin/Media page. If the old id exists on a non-media
        // page, require the Media root to be present before changing dimensions.
        const root = page.querySelector(ROOT_SELECTOR) || document.querySelector(ROOT_SELECTOR);
        if (!root) return null;
        return { page, root };
      }

      function clearWhenInactive(page, root) {
        if (page.classList.contains("active")) return false;
        page.classList.remove("ommi-media-footer-scoped");
        page.style.removeProperty("--ommi-media-page-height");
        root.style.removeProperty("--ommi-media-root-height");
        return true;
      }

      function clampMediaFooter() {
        raf = 0;
        const media = getMediaPage();
        if (!media) return;
        const { page, root } = media;
        if (clearWhenInactive(page, root)) return;

        const footer = document.querySelector(FOOTER_SELECTOR);
        const screen = document.querySelector(".screen") || document.body;
        if (!footer || !screen) return;

        const pageRect = page.getBoundingClientRect();
        const footerRect = footer.getBoundingClientRect();
        const screenRect = screen.getBoundingClientRect();

        // Use the earlier of footer top and screen bottom, so the media root cannot
        // cover the footer even during Jellyfin/library first paint.
        const bottomLimit = Math.min(footerRect.top, screenRect.bottom);
        const available = Math.floor(bottomLimit - pageRect.top - 6);

        // Ignore impossible measurements during the earliest layout ticks; retry
        // shortly instead of writing nonsense dimensions.
        if (!Number.isFinite(available) || available < 180) {
          setTimeout(requestClamp, 40);
          return;
        }

        const px = `${available}px`;
        page.classList.add("ommi-media-footer-scoped");
        page.style.setProperty("--ommi-media-page-height", px);
        root.style.setProperty("--ommi-media-root-height", px);

        const results = document.querySelector("#ommiMediaResults");
        if (results) results.classList.add("ommi-media-results-scoped");
      }

      function requestClamp() {
        if (!ommiMediaIsActive() || raf) return;
        raf = requestAnimationFrame(clampMediaFooter);
      }

      function scheduleStartupClamps() {
        requestClamp();
        requestAnimationFrame(requestClamp);
        setTimeout(requestClamp, 50);
        setTimeout(requestClamp, 200);
        setTimeout(requestClamp, 800);
      }

      document.addEventListener("DOMContentLoaded", scheduleStartupClamps);
      window.addEventListener("resize", requestClamp, { passive: true });
      window.addEventListener("orientationchange", () => setTimeout(requestClamp, 120), { passive: true });
      window.addEventListener("openmmi:medialayout", requestClamp);
      window.addEventListener("openmmi:pagechange", requestClamp);
      if (document.readyState !== "loading") scheduleStartupClamps();

      window.openMmiClampMediaFooter = requestClamp;
    })();
    // --- Open MMI media footer scoped repair end ---


    // --- Open MMI Jellyfin media keys fix start ---
    /*
      System media-key integration for the local Jellyfin player.

      Browsers often handle play/pause automatically for an <audio> element, but
      next/previous track usually require Media Session action handlers. The helper
      functions below delegate to the existing Jellyfin player functions when they
      exist, and fall back to clicking the visible dashboard controls otherwise.
    */
    (function () {
      const AUDIO_SELECTORS = ["#ommiMediaAudio", "#mediaAudio"];

      function mediaAudio() {
        for (const selector of AUDIO_SELECTORS) {
          const audio = document.querySelector(selector);
          if (audio) return audio;
        }
        return null;
      }

      function clickFirst(selectors) {
        for (const selector of selectors) {
          const element = document.querySelector(selector);
          if (element) {
            element.click();
            return true;
          }
        }
        return false;
      }

      function callIfFunction(name) {
        const fn = window[name] || (typeof globalThis !== "undefined" ? globalThis[name] : null);
        if (typeof fn === "function") {
          fn();
          return true;
        }
        return false;
      }

      function mediaNext() {
        if (callIfFunction("openMmiMediaNext")) return;
        if (callIfFunction("mediaNextTrack")) return;
        clickFirst(["#ommiMediaNext", "#mediaNext", '[aria-label="Next"]', '[aria-label="Next track"]']);
      }

      function mediaPrev() {
        if (callIfFunction("openMmiMediaPrev")) return;
        if (callIfFunction("mediaPreviousTrack")) return;
        clickFirst(["#ommiMediaPrev", "#mediaPrev", '[aria-label="Previous"]', '[aria-label="Previous track"]']);
      }

      async function mediaPlay() {
        const audio = mediaAudio();
        if (!audio) {
          clickFirst(["#ommiMediaPlay", "#mediaPlayPause", '[aria-label="Play or pause"]']);
          return;
        }
        try {
          if (audio.paused) await audio.play();
        } catch (_) {
          clickFirst(["#ommiMediaPlay", "#mediaPlayPause", '[aria-label="Play or pause"]']);
        }
      }

      function mediaPause() {
        const audio = mediaAudio();
        if (audio && !audio.paused) audio.pause();
      }

      function mediaPlayPause() {
        const audio = mediaAudio();
        if (!audio) {
          clickFirst(["#ommiMediaPlay", "#mediaPlayPause", '[aria-label="Play or pause"]']);
          return;
        }
        if (audio.paused) mediaPlay();
        else audio.pause();
      }

      function mediaStop() {
        const audio = mediaAudio();
        if (clickFirst(["#ommiMediaStop", "#mediaStop", '[aria-label="Stop"]'])) return;
        if (audio) {
          audio.pause();
          audio.currentTime = 0;
        }
      }

      function updateMediaSessionMetadata() {
        if (!("mediaSession" in navigator) || !("MediaMetadata" in window)) return;

        const title = document.querySelector("#ommiMediaTitle, #mediaTitle")?.textContent?.trim() || "Open MMI";
        const subtitle = document.querySelector("#ommiMediaSubtitle, #mediaSubtitle")?.textContent?.trim() || "Jellyfin";
        const art = document.querySelector("#ommiMediaArt img, #mediaArt img, .ommi-track.is-playing img")?.src;

        const metadata = {
          title,
          artist: subtitle,
          album: "Open MMI",
        };

        if (art) {
          metadata.artwork = [
            { src: art, sizes: "96x96", type: "image/jpeg" },
            { src: art, sizes: "256x256", type: "image/jpeg" },
            { src: art, sizes: "512x512", type: "image/jpeg" },
          ];
        }

        try {
          navigator.mediaSession.metadata = new MediaMetadata(metadata);
        } catch (_) {
          // Metadata is nice-to-have; media-key handlers are the important part.
        }
      }

      function updateMediaSessionPlaybackState() {
        if (!("mediaSession" in navigator)) return;
        const audio = mediaAudio();
        try {
          navigator.mediaSession.playbackState = audio && !audio.paused ? "playing" : "paused";
        } catch (_) {}
      }

      function bindMediaSession() {
        if (!("mediaSession" in navigator)) return;

        const handlers = {
          play: mediaPlay,
          pause: mediaPause,
          stop: mediaStop,
          previoustrack: mediaPrev,
          nexttrack: mediaNext,
        };

        for (const [action, handler] of Object.entries(handlers)) {
          try {
            navigator.mediaSession.setActionHandler(action, handler);
          } catch (_) {
            // Some browsers omit specific actions; ignore unsupported ones.
          }
        }

        updateMediaSessionMetadata();
        updateMediaSessionPlaybackState();
      }

      function bindKeyboardMediaKeys() {
        if (window.__openMmiMediaKeyKeyboardBound) return;
        window.__openMmiMediaKeyKeyboardBound = true;

        document.addEventListener("keydown", (event) => {
          switch (event.key) {
            case "MediaTrackNext":
              event.preventDefault();
              mediaNext();
              break;
            case "MediaTrackPrevious":
              event.preventDefault();
              mediaPrev();
              break;
            case "MediaPlayPause":
              event.preventDefault();
              mediaPlayPause();
              break;
            case "MediaStop":
              event.preventDefault();
              mediaStop();
              break;
            default:
              break;
          }
        }, true);
      }

      function bindAudioEvents() {
        const audio = mediaAudio();
        if (!audio || audio.__openMmiMediaKeysBound) return;
        audio.__openMmiMediaKeysBound = true;

        for (const eventName of ["play", "pause", "ended", "loadedmetadata", "durationchange"]) {
          audio.addEventListener(eventName, () => {
            updateMediaSessionMetadata();
            updateMediaSessionPlaybackState();
          });
        }
      }

      function bootMediaKeys() {
        performanceMetrics.media_key_boots += 1;
        bindKeyboardMediaKeys();
        bindMediaSession();
        bindAudioEvents();
      }

      document.addEventListener("DOMContentLoaded", bootMediaKeys);
      window.addEventListener("openmmi:medialayout", bootMediaKeys);
      window.addEventListener("openmmi:pagechange", bootMediaKeys);
      if (document.readyState !== "loading") bootMediaKeys();
      // Cover delayed initial construction without leaving a permanent timer behind.
      setTimeout(bootMediaKeys, 100);
      setTimeout(bootMediaKeys, 500);
      setTimeout(bootMediaKeys, 1500);
    })();

    // Radio, USB and Bluetooth currently adapt the shared player through these
    // compatibility bindings. Accessors keep their assignments inside this
    // controller instead of creating a second copy of player state.
    const globalBindings = {
      openMmiMedia: [() => openMmiMedia, null],
      ommiMediaEsc: [() => ommiMediaEsc, null],
      ommiMediaText: [() => ommiMediaText, null],
      ommiMediaTime: [() => ommiMediaTime, null],
      ommiMediaIcon: [() => ommiMediaIcon, null],
      ommiMediaCleanMusicIcon: [() => ommiMediaCleanMusicIcon, null],
      ommiMediaInvalidateRequest: [() => ommiMediaInvalidateRequest, null],
      ommiMediaRunSearchNow: [() => ommiMediaRunSearchNow, null],
      ommiMediaBindLiveSearch: [() => ommiMediaBindLiveSearch, null],
      ommiMediaPage: [() => ommiMediaPage, null],
      ommiMediaSetArtwork: [() => ommiMediaSetArtwork, null],
      ommiMediaSetMessage: [() => ommiMediaSetMessage, null],
      ommiMediaSetLoading: [() => ommiMediaSetLoading, null],
      ommiMediaFetchJson: [() => ommiMediaFetchJson, null],
      ommiMediaFitViewport: [() => ommiMediaFitViewport, (value) => { ommiMediaFitViewport = value; }],
      ommiMediaInstallFilters: [() => ommiMediaInstallFilters, (value) => { ommiMediaInstallFilters = value; }],
      ommiMediaUpdateFilters: [() => ommiMediaUpdateFilters, (value) => { ommiMediaUpdateFilters = value; }],
      ommiMediaRenderResults: [() => ommiMediaRenderResults, (value) => { ommiMediaRenderResults = value; }],
      ommiMediaSetNowPlaying: [() => ommiMediaSetNowPlaying, (value) => { ommiMediaSetNowPlaying = value; }],
      ommiMediaUpdateProgress: [() => ommiMediaUpdateProgress, (value) => { ommiMediaUpdateProgress = value; }],
      ommiMediaUpdatePlayState: [() => ommiMediaUpdatePlayState, (value) => { ommiMediaUpdatePlayState = value; }],
      ommiMediaLoadLibrary: [() => ommiMediaLoadLibrary, (value) => { ommiMediaLoadLibrary = value; }],
      ommiMediaRefreshStatus: [() => ommiMediaRefreshStatus, (value) => { ommiMediaRefreshStatus = value; }],
      ommiMediaPlayIndex: [() => ommiMediaPlayIndex, (value) => { ommiMediaPlayIndex = value; }],
      ommiMediaNext: [() => ommiMediaNext, (value) => { ommiMediaNext = value; }],
      ommiMediaPrev: [() => ommiMediaPrev, (value) => { ommiMediaPrev = value; }],
      ommiMediaBind: [() => ommiMediaBind, (value) => { ommiMediaBind = value; }],
    };
    for (const [name, [get, set]] of Object.entries(globalBindings)) {
      const descriptor = { configurable: true, enumerable: false, get };
      if (set) descriptor.set = set;
      Object.defineProperty(window, name, descriptor);
    }

    const controller = {
      boot,
      state: openMmiMedia,
      loadLibrary: (...args) => ommiMediaLoadLibrary(...args),
      refreshStatus: (...args) => ommiMediaRefreshStatus(...args),
      playIndex: (...args) => ommiMediaPlayIndex(...args),
      setMessage: (...args) => ommiMediaSetMessage(...args),
      reconnection: ommiMediaReconnectController(),
      formatTime,
      escapeHtml,
    };
    window.openMmiJellyfinPlayer = controller;
    return controller;
  }

  return {
    escapeHtml,
    formatTime,
    jellyfinProviderBlocksControls,
    installController,
  };
});
