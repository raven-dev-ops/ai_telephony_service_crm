(function () {
  function getScript() {
    return document.currentScript || document.querySelector('script[src$="embed.js"]');
  }

  function buildWidgetUrl() {
    var script = getScript();
    var widgetUrl =
      (script && script.getAttribute("data-widget-url")) || "/widget/chat.html";

    if (script && script.src && !/^https?:\/\//i.test(widgetUrl)) {
      try {
        var scriptUrl = new URL(script.src);
        widgetUrl = scriptUrl.origin + widgetUrl;
      } catch (e) {
        // Fallback: leave widgetUrl as-is.
      }
    }

    var widgetToken = script && script.getAttribute("data-widget-token");
    if (widgetToken) {
      var sep = widgetUrl.indexOf("?") === -1 ? "?" : "&";
      widgetUrl += sep + "widget_token=" + encodeURIComponent(widgetToken);
    }
    return widgetUrl;
  }

  function createWidget() {
    if (document.getElementById("bristol-chat-widget")) {
      return;
    }

    var widgetUrl = buildWidgetUrl();

    var container = document.createElement("div");
    container.id = "bristol-chat-widget";
    container.style.position = "fixed";
    container.style.bottom = "72px";
    container.style.right = "16px";
    container.style.width = "360px";
    container.style.height = "520px";
    container.style.maxWidth = "100%";
    container.style.borderRadius = "12px";
    container.style.overflow = "hidden";
    container.style.boxShadow = "0 4px 12px rgba(0, 0, 0, 0.2)";
    container.style.zIndex = "9999";
    container.style.background = "#ffffff";

    var iframe = document.createElement("iframe");
    iframe.src = widgetUrl;
    iframe.title = "Chat with Bristol Plumbing";
    iframe.style.border = "0";
    iframe.style.width = "100%";
    iframe.style.height = "100%";

    container.appendChild(iframe);
    document.body.appendChild(container);
  }

  function setOpenState(isOpen) {
    try {
      if (window.localStorage) {
        localStorage.setItem("bristol_chat_open", isOpen ? "1" : "0");
      }
    } catch (e) {
      // Ignore storage errors.
    }
  }

  function getOpenState() {
    try {
      if (window.localStorage) {
        return localStorage.getItem("bristol_chat_open");
      }
    } catch (e) {
      // Ignore storage errors.
    }
    return null;
  }

  function toggleWidgetVisibility() {
    var container = document.getElementById("bristol-chat-widget");
    if (container) {
      var isHidden = container.style.display === "none";
      container.style.display = isHidden ? "block" : "none";
      setOpenState(isHidden);
    } else {
      createWidget();
      setOpenState(true);
    }
  }

  function ensureLauncher() {
    if (document.getElementById("bristol-chat-launcher")) {
      return;
    }
    var button = document.createElement("button");
    button.id = "bristol-chat-launcher";
    button.type = "button";
    button.setAttribute("aria-label", "Chat with us");
    button.textContent = "Chat";
    button.style.position = "fixed";
    button.style.bottom = "16px";
    button.style.right = "16px";
    button.style.padding = "0.5rem 0.9rem";
    button.style.borderRadius = "999px";
    button.style.border = "none";
    button.style.background = "#0d47a1";
    button.style.color = "#ffffff";
    button.style.boxShadow = "0 4px 12px rgba(0, 0, 0, 0.25)";
    button.style.cursor = "pointer";
    button.style.zIndex = "9999";
    button.style.fontFamily = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    button.style.fontSize = "14px";

    button.addEventListener("click", toggleWidgetVisibility);
    document.body.appendChild(button);
  }

  function init() {
    ensureLauncher();
    var openPref = getOpenState();
    if (openPref === "1") {
      createWidget();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
