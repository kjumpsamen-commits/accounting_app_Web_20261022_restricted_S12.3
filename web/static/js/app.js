// app.js — 唯讀系統共用前端腳本

document.addEventListener("DOMContentLoaded", () => {
  // ── 頂端日期時間 ──────────────────────────────────────────────
  const dateEl = document.getElementById('topbarDate');
  if (dateEl) {
    const update = () => {
      const now  = new Date();
      const opts = { year: 'numeric', month: '2-digit', day: '2-digit',
                     hour: '2-digit', minute: '2-digit', hour12: false };
      dateEl.textContent = now.toLocaleString('zh-TW', opts);
    };
    update();
    setInterval(update, 30000);
  }

  // ── 自動關閉 alert ────────────────────────────────────────────
  document.querySelectorAll(".alert-dismissible").forEach((el) => {
    setTimeout(() => bootstrap.Alert.getOrCreateInstance(el)?.close(), 4000);
  });

  // ── 行動版：點側邊欄連結後自動收合 ───────────────────────────
  document.querySelectorAll('.sidebar .nav-link').forEach(link => {
    link.addEventListener('click', () => {
      if (window.innerWidth < 992) closeSidebar();
    });
  });
});

// ── Sidebar 開關 ──────────────────────────────────────────────────
function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  const ov = document.getElementById('sidebarOverlay');
  sb.classList.toggle('open');
  ov.classList.toggle('open');
}

function closeSidebar() {
  document.getElementById('sidebar')?.classList.remove('open');
  document.getElementById('sidebarOverlay')?.classList.remove('open');
}
