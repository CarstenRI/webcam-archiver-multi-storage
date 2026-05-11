// Webcam-Uploader – Mini-JS für Async-Aktionen.

async function testCam(camId) {
  try {
    const res = await fetch(`/cams/${camId}/test`, { method: 'POST' });
    if (!res.ok) {
      alert(`Test fehlgeschlagen: HTTP ${res.status}`);
      return;
    }
    const data = await res.json();
    if (data.ok) {
      alert(`✓ URL erreichbar – ${data.bytes} Bytes geladen.\nSeite neu laden, um die Vorschau zu sehen.`);
      // Vorschau-Bild ggf. neu laden
      const img = document.querySelector(`img[alt*="cam${camId}"], img[src*="cam/${camId}"]`);
      if (img) img.src = img.src.split('?')[0] + '?ts=' + Date.now();
    } else {
      alert(`✗ Test fehlgeschlagen:\n${data.error}`);
    }
  } catch (e) {
    alert(`Fehler: ${e}`);
  }
}

// Auto-Refresh des Dashboards alle 60s, sofern auf der Dashboard-Seite
if (location.pathname.startsWith('/dashboard') || location.pathname === '/') {
  setTimeout(() => location.reload(), 60_000);
}
