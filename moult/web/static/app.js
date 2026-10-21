const state = {
  overview: null,
  currentInspectorFolderId: "work-dump",
};

const MODE_META = {
  sendreceive: {
    label: "↔ Двусторонняя (Send & Receive)",
    badgeClass: "badge-2way",
    desc: "Все изменения и новые файлы синхронизируются в обе стороны между устройствами.",
  },
  sendonly: {
    label: "→ Только отправка / Слив файлов (Send Only)",
    badgeClass: "badge-send",
    desc: "Файлы с этого ПК отправляются на Главный ПК. Удаление на приёмнике не затрагивает источник.",
  },
  receiveonly: {
    label: "← Только приём / Мастер-копия (Receive Only)",
    badgeClass: "badge-receive",
    desc: "Этот ПК принимает файлы (слив с Рабочего ПК или фото Камеры Android) и хранит мастер-копию.",
  },
  receiveencrypted: {
    label: "🔒 Зашифрованное хранение (Encrypted)",
    badgeClass: "badge-tls",
    desc: "Приём и хранение данных в зашифрованном виде.",
  },
};

function showToast(msg, isError = false) {
  const el = document.getElementById("toastBanner");
  el.textContent = msg;
  el.style.borderColor = isError ? "#ef4444" : "rgba(16, 185, 129, 0.45)";
  el.style.color = isError ? "#fca5a5" : "#6ee7b7";
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 4500);
}

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return "0 Б";
  const k = 1024;
  const sizes = ["Б", "КБ", "МБ", "ГБ"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

async function apiRequest(url, method = "GET", body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(url, opts);
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.detail || "Ошибка запроса к API");
  }
  return data;
}

async function loadOverview() {
  const data = await apiRequest("/api/overview");
  state.overview = data;
  renderAll();
}

function renderAll() {
  const ov = state.overview;
  if (!ov) return;

  // Заголовок и роль ПК
  document.getElementById("deviceNameHeader").textContent = `${ov.device_name} • ${
    ov.device_role === "home_master" ? "Главный домашний узел" : "Рабочий ПК (режим слива)"
  }`;
  document.getElementById("pcRoleSelect").value = ov.device_role;
  document.getElementById("roleHint").textContent =
    ov.device_role === "home_master"
      ? "🏠 Главный ПК: принимает слив с Рабочего ПК и фото Камеры Android"
      : "💼 Рабочий ПК: сливает рабочие файлы домой + двусторонние общие папки";

  // Диск
  document.getElementById("diskFreeText").textContent = `${ov.disk.free_gb} ГБ свободно`;
  document.getElementById("diskBarFill").style.width = `${ov.disk.used_percent}%`;
  document.getElementById("diskUsedText").textContent = `Занято ${ov.disk.used_gb} из ${ov.disk.total_gb} ГБ (${ov.disk.used_percent}%)`;

  // Счётчики
  document.getElementById("navFoldersCount").textContent = ov.stats.folders_count;
  document.getElementById("navVersionsCount").textContent = ov.stats.versions_in_trash;
  document.getElementById("statFolders").textContent = ov.stats.folders_count;
  document.getElementById("statFiles").textContent = ov.stats.total_files;
  document.getElementById("statTrash").textContent = ov.stats.versions_in_trash;

  renderFoldersGrid(ov.folders);
  renderRecentEvents(ov.recent_events);
  renderInspectorSelector(ov.folders);
  renderSecurityTab(ov);
  renderDevicesTab(ov);
  renderVersionsTab(ov.versions);
  renderTrayPopup(ov);
}

function renderFoldersGrid(folders) {
  const container = document.getElementById("foldersGrid");
  container.innerHTML = "";

  folders.forEach((f) => {
    const meta = MODE_META[f.sync_mode] || MODE_META.sendreceive;
    const card = document.createElement("div");
    card.className = "folder-card";

    const encBadge = f.encryption_enabled
      ? `<span class="badge badge-tls">🔒 AES-256-GCM</span>`
      : `<span class="badge badge-receive">🛡 TLS 1.3 + SHA-256</span>`;

    card.innerHTML = `
      <div class="folder-card-top">
        <div>
          <div class="folder-title">
            <span>📁 ${f.label}</span>
          </div>
          <div class="folder-path">${f.path}</div>
        </div>
        <div style="display:flex; gap:6px; flex-wrap:wrap;">
          <span class="badge ${meta.badgeClass}">${meta.label}</span>
          ${encBadge}
        </div>
      </div>

      <div class="folder-controls-row">
        <select class="folder-mode-select" data-folder-id="${f.id}">
          <option value="sendreceive" ${f.sync_mode === "sendreceive" ? "selected" : ""}>↔ Двусторонняя синхронизация (Send &amp; Receive)</option>
          <option value="sendonly" ${f.sync_mode === "sendonly" ? "selected" : ""}>→ Только отправка / Слив файлов (Send Only)</option>
          <option value="receiveonly" ${f.sync_mode === "receiveonly" ? "selected" : ""}>← Только приём / Мастер-копия (Receive Only)</option>
        </select>
        <button class="btn btn-sm btn-primary btn-sync-folder" data-folder-id="${f.id}">🔄 Синхр.</button>
      </div>

      <div class="folder-actions">
        <button class="btn btn-sm btn-secondary btn-inspect-folder" data-folder-id="${f.id}">
          🗁 Файлы и проверка слива (${f.files_count})
        </button>
        <button class="btn btn-sm btn-outline btn-open-explorer" data-folder-id="${f.id}">
          📂 Открыть в Проводнике
        </button>
      </div>

      <div class="folder-meta-row">
        <span>Статус: <strong>${f.last_status}</strong></span>
        <span>Файлов: ${f.files_count} (${formatBytes(f.total_bytes)})</span>
      </div>
    `;
    container.appendChild(card);
  });

  container.querySelectorAll(".folder-mode-select").forEach((sel) => {
    sel.addEventListener("change", async (e) => {
      const fid = e.target.getAttribute("data-folder-id");
      const newMode = e.target.value;
      await apiRequest("/api/folders", "POST", { id: fid, sync_mode: newMode });
      showToast(`Режим папки обновлён: ${MODE_META[newMode].label}`);
      await loadOverview();
    });
  });

  container.querySelectorAll(".btn-sync-folder").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const fid = e.currentTarget.getAttribute("data-folder-id");
      const res = await apiRequest(`/api/folders/${fid}/sync`, "POST");
      const r = res.result;
      showToast(
        `Папка "${r.folder_label}" синхронизирована: отправлено ↑${r.uploaded}, принято ↓${r.downloaded}, проверено SHA-256: ${r.verified_files}`
      );
      state.overview = res.overview;
      renderAll();
    });
  });

  container.querySelectorAll(".btn-inspect-folder").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const fid = e.currentTarget.getAttribute("data-folder-id");
      state.currentInspectorFolderId = fid;
      switchTab("inspector");
      await loadInspectorFiles(fid);
    });
  });

  container.querySelectorAll(".btn-open-explorer").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const fid = e.currentTarget.getAttribute("data-folder-id");
      const res = await apiRequest(`/api/folders/${fid}/open-explorer`, "POST");
      showToast(`Открыта папка: ${res.path}`);
    });
  });
}

function renderRecentEvents(events) {
  const list = document.getElementById("recentEventsList");
  list.innerHTML = "";
  if (!events || events.length === 0) {
    list.innerHTML = `<div class="event-row">Пока нет событий. Нажмите «Синхронизировать все папки».</div>`;
    return;
  }

  events.slice(0, 10).forEach((ev) => {
    const row = document.createElement("div");
    row.className = "event-row";
    row.innerHTML = `
      <div>
        <strong>${ev.direction} ${ev.file_name}</strong>
        <span style="color:var(--text-muted); margin-left:8px;">[${ev.folder_label}]</span>
        <div style="font-size:11.5px; color:var(--text-muted);">${ev.details}</div>
      </div>
      <div style="text-align:right; font-size:12px;">
        <div>${ev.timestamp}</div>
        <code style="color:#fbbf24; font-size:11px;">SHA-256: ${ev.sha256_short || "OK"}</code>
      </div>
    `;
    list.appendChild(row);
  });
}

function renderInspectorSelector(folders) {
  const sel = document.getElementById("inspectorFolderSelect");
  sel.innerHTML = "";
  folders.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f.id;
    opt.textContent = `${f.label} (${MODE_META[f.sync_mode]?.label || f.sync_mode})`;
    if (f.id === state.currentInspectorFolderId) opt.selected = true;
    sel.appendChild(opt);
  });
}

async function loadInspectorFiles(folderId) {
  const data = await apiRequest(`/api/folders/${folderId}/files`);
  const f = data.folder;
  const meta = MODE_META[f.sync_mode] || MODE_META.sendreceive;

  document.getElementById("inspectorFolderSelect").value = folderId;
  document.getElementById("localPanePath").textContent = f.path;
  document.getElementById("remotePanePath").textContent = f.paired_local_path || "Удалённый узел TLS 1.3";

  document.getElementById("inspectorFolderBanner").innerHTML = `
    <strong>Активный режим папки «${f.label}»: ${meta.label}</strong><br/>
    <span>${meta.desc}</span>
  `;

  renderFilePane("localFilesTable", data.local_files);
  renderFilePane("remoteFilesTable", data.remote_files);
}

function renderFilePane(containerId, files) {
  const el = document.getElementById(containerId);
  el.innerHTML = "";
  if (!files || files.length === 0) {
    el.innerHTML = `<div class="file-row" style="color:var(--text-muted);">В этой папке пока нет файлов</div>`;
    return;
  }

  files.forEach((file) => {
    const row = document.createElement("div");
    row.className = "file-row";
    const badge = file.is_encrypted
      ? `<span class="badge badge-tls">🔒 AES-256-GCM</span>`
      : `<span class="badge badge-receive">SHA256: ${file.sha256_short}</span>`;

    row.innerHTML = `
      <div>
        <strong>📄 ${file.name}</strong>
        <div style="font-size:11px; color:var(--text-muted);">
          ${formatBytes(file.size_bytes)} • Изменён: ${file.modified_at}
        </div>
      </div>
      <div>${badge}</div>
    `;
    el.appendChild(row);
  });
}

function renderSecurityTab(ov) {
  document.getElementById("secDeviceId").textContent = ov.device_id;
  document.getElementById("secProfileSelect").value = ov.security.profile;
  document.getElementById("secVerifySha256").checked = ov.security.verify_sha256_integrity;
  document.getElementById("secLocalhostOnly").checked = ov.security.api_bind_localhost_only;
  document.getElementById("secAutostartWin").checked = ov.security.autostart_windows;
  document.getElementById("syncthingXmlBox").textContent = ov.syncthing_xml_preview;
}

function renderDevicesTab(ov) {
  document.getElementById("qrCodeImage").src = ov.qr_code_data_url;
  document.getElementById("qrDeviceIdText").textContent = ov.device_id;

  const list = document.getElementById("devicesList");
  list.innerHTML = "";
  ov.devices.forEach((dev) => {
    const row = document.createElement("div");
    row.className = "device-row";
    const icon = dev.role === "android_mobile" ? "📱" : "💻";
    row.innerHTML = `
      <div>
        <strong>${icon} ${dev.name}</strong>
        <div style="font-size:11.5px; color:var(--text-muted);">ID: ${dev.device_id}</div>
        <div style="font-size:11.5px; color:#34d399;">Шифр: ${dev.tls_cipher} • ${dev.last_seen}</div>
      </div>
      <span class="badge badge-receive">Доверенный сертификат</span>
    `;
    list.appendChild(row);
  });
}

function renderVersionsTab(versions) {
  const list = document.getElementById("versionsList");
  list.innerHTML = "";
  if (!versions || versions.length === 0) {
    list.innerHTML = `<div class="version-row">В Корзине версий пока пусто. При любом обновлении файла старая копия сохраняется сюда автоматически.</div>`;
    return;
  }

  versions.forEach((v) => {
    const row = document.createElement("div");
    row.className = "version-row";
    row.innerHTML = `
      <div>
        <strong>🕓 ${v.file_name}</strong>
        <span style="color:var(--text-muted); margin-left:6px;">(${v.folder_label})</span>
        <div style="font-size:11.5px; color:var(--text-muted);">
          Архивировано: ${v.archived_at} • Причина: ${v.reason} • SHA-256: ${v.sha256.slice(0, 12)}
        </div>
      </div>
      <button class="btn btn-sm btn-outline btn-restore-version" data-version-id="${v.version_id}">
        ↺ Восстановить эту версию
      </button>
    `;
    list.appendChild(row);
  });

  list.querySelectorAll(".btn-restore-version").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const vid = e.currentTarget.getAttribute("data-version-id");
      const res = await apiRequest(`/api/versions/${vid}/restore`, "POST");
      showToast(`Версия файла «${res.restored.file_name}» успешно восстановлена!`);
      state.overview = res.overview;
      renderAll();
    });
  });
}

function renderTrayPopup(ov) {
  const list = document.getElementById("trayRecentList");
  list.innerHTML = "";
  const events = ov.recent_events || [];
  if (events.length === 0) {
    list.innerHTML = `<div style="font-size:12px; color:var(--text-muted);">Все файлы синхронизированы</div>`;
    return;
  }
  events.slice(0, 5).forEach((ev) => {
    const item = document.createElement("div");
    item.className = "event-row";
    item.style.padding = "7px 10px";
    item.innerHTML = `
      <div style="font-size:12px;">
        <strong>${ev.direction} ${ev.file_name}</strong>
        <div style="font-size:10.5px; color:var(--text-muted);">${ev.folder_label} • ${ev.timestamp}</div>
      </div>
    `;
    list.appendChild(item);
  });
}

function switchTab(tabName) {
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-tab") === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.classList.toggle("active", p.id === `tab-${tabName}`);
  });
  if (tabName === "inspector") {
    loadInspectorFiles(state.currentInspectorFolderId);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  // Навигация по вкладкам
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchTab(btn.getAttribute("data-tab"));
    });
  });

  // Смена роли ПК (Главный ПК <-> Рабочий ПК)
  document.getElementById("pcRoleSelect").addEventListener("change", async (e) => {
    const newRole = e.target.value;
    const ov = await apiRequest("/api/role", "POST", { role: newRole });
    state.overview = ov;
    renderAll();
    showToast(
      newRole === "home_master"
        ? "Включён профиль «Главный ПК (Домашний)»: папка слива переведена в режим приёма"
        : "Включён профиль «Рабочий ПК»: папка слива переведена в режим «Только отправка»"
    );
  });

  // Синхронизировать все папки
  document.getElementById("btnSyncAll").addEventListener("click", async () => {
    const res = await apiRequest("/api/sync-all", "POST");
    const r = res.result;
    showToast(
      `Синхронизация завершена: отправлено ↑${r.total_uploaded}, принято ↓${r.total_downloaded} (SHA-256 проверен)`
    );
    state.overview = res.overview;
    renderAll();
  });

  // Модалка новой папки
  document.getElementById("btnShowAddFolder").addEventListener("click", () => {
    document.getElementById("addFolderModal").classList.toggle("hidden");
  });
  document.getElementById("btnCancelFolder").addEventListener("click", () => {
    document.getElementById("addFolderModal").classList.add("hidden");
  });
  document.getElementById("btnCreateFolder").addEventListener("click", async () => {
    const label = document.getElementById("newFolderLabel").value.trim();
    if (!label) {
      showToast("Укажите название папки", true);
      return;
    }
    const payload = {
      label,
      sync_mode: document.getElementById("newFolderMode").value,
      path: document.getElementById("newFolderPath").value.trim() || null,
      versioning_mode: document.getElementById("newFolderVersioning").value,
      encryption_enabled: document.getElementById("newFolderEncrypted").checked,
      process_guard_enabled: document.getElementById("newFolderProcessGuard").checked,
    };
    const res = await apiRequest("/api/folders", "POST", payload);
    document.getElementById("addFolderModal").classList.add("hidden");
    document.getElementById("newFolderLabel").value = "";
    state.overview = res.overview;
    renderAll();
    showToast(`Папка «${label}» добавлена в синхронизацию`);
  });

  // Инспектор файлов
  document.getElementById("inspectorFolderSelect").addEventListener("change", (e) => {
    state.currentInspectorFolderId = e.target.value;
    loadInspectorFiles(state.currentInspectorFolderId);
  });

  document.getElementById("btnSyncCurrentFolder").addEventListener("click", async () => {
    const fid = state.currentInspectorFolderId;
    const res = await apiRequest(`/api/folders/${fid}/sync`, "POST");
    state.overview = res.overview;
    renderAll();
    await loadInspectorFiles(fid);
    showToast(
      `Папка синхронизирована: ↑${res.result.uploaded} ↓${res.result.downloaded}`
    );
  });

  document.getElementById("btnOpenExplorerInspector").addEventListener("click", async () => {
    const fid = state.currentInspectorFolderId;
    const res = await apiRequest(`/api/folders/${fid}/open-explorer`, "POST");
    showToast(`Открыто в файловом менеджере: ${res.path}`);
  });

  document.getElementById("btnAddLocalFile").addEventListener("click", async () => {
    const fid = state.currentInspectorFolderId;
    const name = document.getElementById("localFileName").value.trim() || `Документ_ПК_${Date.now().toString().slice(-4)}.txt`;
    const content = document.getElementById("localFileContent").value || "Тестовый документ, созданный на этом ПК.";
    const pass = document.getElementById("localFilePass").value.trim() || null;

    const res = await apiRequest(`/api/folders/${fid}/files`, "POST", {
      file_name: name,
      content_text: content,
      side: "local",
      encrypt_passphrase: pass,
    });
    document.getElementById("localFileName").value = "";
    document.getElementById("localFileContent").value = "";
    state.overview = res.overview;
    renderAll();
    await loadInspectorFiles(fid);
    showToast(
      pass
        ? `Файл «${res.created.file_name}» зашифрован AES-256-GCM и синхронизирован!`
        : `Файл «${res.created.file_name}» создан на этом ПК и обработан согласно режиму папки`
    );
  });

  document.getElementById("btnAddRemoteFile").addEventListener("click", async () => {
    const fid = state.currentInspectorFolderId;
    const name = document.getElementById("remoteFileName").value.trim() || `Слив_с_Работы_${Date.now().toString().slice(-4)}.txt`;
    const content = document.getElementById("remoteFileContent").value || "Файл, поступивший со второго ПК или с Камеры Android.";

    const res = await apiRequest(`/api/folders/${fid}/files`, "POST", {
      file_name: name,
      content_text: content,
      side: "remote",
    });
    document.getElementById("remoteFileName").value = "";
    document.getElementById("remoteFileContent").value = "";
    state.overview = res.overview;
    renderAll();
    await loadInspectorFiles(fid);
    showToast(`Файл «${res.created.file_name}» поступил со 2-го устройства и синхронизирован!`);
  });

  // Безопасность
  document.getElementById("btnSaveSecurity").addEventListener("click", async () => {
    const payload = {
      profile: document.getElementById("secProfileSelect").value,
      verify_sha256_integrity: document.getElementById("secVerifySha256").checked,
      api_bind_localhost_only: document.getElementById("secLocalhostOnly").checked,
      autostart_windows: document.getElementById("secAutostartWin").checked,
    };
    const ov = await apiRequest("/api/security/settings", "POST", payload);
    state.overview = ov;
    renderAll();
    showToast("Политика безопасности TLS 1.3 и конфигурация Syncthing обновлены");
  });

  document.getElementById("btnGenerateInvite").addEventListener("click", async () => {
    const pin = document.getElementById("pairPinCreate").value;
    const role = document.getElementById("pairTargetRole").value;
    try {
      const res = await apiRequest("/api/security/pairing-invite", "POST", {
        pin_code: pin,
        target_role: role,
      });
      document.getElementById("generatedInviteBox").value = res.token;
      document.getElementById("pairTokenImport").value = res.token;
      showToast("Сгенерирован зашифрованный токен сопряжения AES-256-GCM!");
    } catch (err) {
      showToast(err.message, true);
    }
  });

  document.getElementById("btnImportInvite").addEventListener("click", async () => {
    const token = document.getElementById("pairTokenImport").value.trim();
    const pin = document.getElementById("pairPinImport").value;
    try {
      const res = await apiRequest("/api/security/import-invite", "POST", {
        token,
        pin_code: pin,
      });
      state.overview = res.overview;
      renderAll();
      showToast(`Успешно расшифровано и добавлено устройство: ${res.imported.peer.name}`);
    } catch (err) {
      showToast(err.message, true);
    }
  });

  // Добавление устройства и проверка Luhn mod 32
  document.getElementById("btnValidateDevId").addEventListener("click", async () => {
    const devId = document.getElementById("newDevId").value.trim();
    const res = await apiRequest("/api/security/validate-device-id", "POST", { device_id: devId });
    const msgEl = document.getElementById("devIdValidationResult");
    if (res.valid) {
      msgEl.style.color = "#34d399";
      msgEl.textContent = `✓ Контрольная сумма Luhn mod 32 верна: ${res.result}`;
    } else {
      msgEl.style.color = "#f87171";
      msgEl.textContent = `✕ ${res.result}`;
    }
  });

  document.getElementById("btnAddDevice").addEventListener("click", async () => {
    const name = document.getElementById("newDevName").value.trim();
    const devId = document.getElementById("newDevId").value.trim();
    const role = document.getElementById("newDevRole").value;
    if (!name || !devId) {
      showToast("Укажите имя устройства и его 56-символьный Device ID", true);
      return;
    }
    try {
      const res = await apiRequest("/api/devices", "POST", {
        name,
        device_id: devId,
        role,
      });
      state.overview = res.overview;
      renderAll();
      showToast(`Доверенное устройство «${name}» успешно подключено!`);
    } catch (err) {
      showToast(err.message, true);
    }
  });

  // Виджет трея Windows
  document.getElementById("btnToggleTrayPopup").addEventListener("click", () => {
    document.getElementById("trayPopupWidget").classList.toggle("hidden");
  });
  document.getElementById("btnCloseTrayPopup").addEventListener("click", () => {
    document.getElementById("trayPopupWidget").classList.add("hidden");
  });
  document.getElementById("trayBtnSyncNow").addEventListener("click", async () => {
    const res = await apiRequest("/api/sync-all", "POST");
    state.overview = res.overview;
    renderAll();
    showToast("Синхронизация из системного трея выполнена!");
  });
  document.getElementById("trayBtnOpenFolder").addEventListener("click", async () => {
    const fid = state.overview.folders[0].id;
    const res = await apiRequest(`/api/folders/${fid}/open-explorer`, "POST");
    showToast(`Открыта папка: ${res.path}`);
  });

  await loadOverview();
  // Выполним первичную синхронизацию при старте, чтобы демонстрационный слив с Рабочего ПК и Камеры отобразился в ленте
  const initSync = await apiRequest("/api/sync-all", "POST");
  state.overview = initSync.overview;
  renderAll();
});
