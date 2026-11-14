/* Owner-only five reaction playlists. All data comes from authenticated API. */
(() => {
  const STATUS_TEXT = {
    maybe: "🤔 Может добавлю",
    add: "❤️ Добавлю",
    never: "🚫 Никогда не буду слушать",
    later: "⏳ Точно не сейчас",
    interesting: "🧠 Интересно, но слушать не буду",
  };
  const PAGE_SIZE = 16;
  const root = document.getElementById("decision-categories");
  const list = document.getElementById("decision-items");
  if (!root || !list) return;
  let active = "maybe";
  let offset = 0;

  const makeSelect = (value, onChange) => {
    const select = element("select", "decision-select");
    select.setAttribute("aria-label", "Реакция на трек");
    for (const [status, label] of Object.entries(STATUS_TEXT)) {
      const option = element("option", "", label);
      option.value = status;
      select.append(option);
    }
    select.value = value;
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        await onChange(select.value);
        await refreshCategories();
        await refreshItems();
      } catch (error) {
        toast(error.message);
        select.value = value;
      } finally {
        select.disabled = false;
      }
    });
    return select;
  };

  async function refreshCategories() {
    const categories = await api("/api/decisions");
    root.replaceChildren();
    for (const item of categories) {
      const b = action(
        item.label + " · " + item.count,
        async () => {
          active = item.status;
          offset = 0;
          await refreshItems();
        },
      );
      if (item.status === active) b.classList.add("primary");
      root.append(b);
    }
  }

  async function refreshItems() {
    const data = await api(
      "/api/decisions/" + active + "?offset=" + offset + "&limit=" + PAGE_SIZE,
    );
    list.replaceChildren();
    document.getElementById("decision-pagination").textContent =
      data.total ? `${offset + 1}–${offset + data.items.length} из ${data.total}` : "Пусто";
    document.getElementById("decision-prev").disabled = offset === 0;
    document.getElementById("decision-next").disabled =
      offset + PAGE_SIZE >= data.total;
    if (data.items.some(item => item.playable)) {
      const bar = element("div", "actions");
      bar.append(action("▶ Слушать категорию в Telegram", async () => {
        const result = await post("/api/decisions/" + active + "/play");
        toast(`Отправлено ${result.sent} аудиофайлов ▶`);
        if (typeof refreshQueue === "function") await refreshQueue();
      }));
      list.append(bar);
    }
    if (!data.items.length) {
      list.append(element("div", "empty", "Здесь пока нет треков."));
    }
    for (const item of data.items) {
      const row = element("div", "item");
      const info = element("div", "info");
      info.append(
        element("div", "name", item.title),
        element("div", "sub", item.artist),
      );
      const actions = element("div", "track-actions");
      if (item.playable) {
        actions.append(action("▶ Telegram", async () => {
          await post("/api/tracks/" + item.track_id + "/play");
          toast("Аудиофайл отправлен в Telegram");
        }));
      } else {
        const link = element("a", "secondary-action", "🎧 Найти в Яндексе");
        link.href = item.yandex_search_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.addEventListener("click", e => {
          if (tg?.openLink) {
            e.preventDefault();
            tg.openLink(item.yandex_search_url);
          }
        });
        actions.append(link);
      }
      const endpoint = item.source === "local"
        ? "/api/decisions/local/" + item.track_id
        : "/api/decisions/catalog/" + encodeURIComponent(item.mbid);
      actions.append(makeSelect(active, status => post(endpoint, {status})));
      row.append(info, actions);
      list.append(row);
    }
  }

  async function refreshAll() {
    await refreshCategories();
    await refreshItems();
  }
  // Allows the library/discovery cards to refresh verdict category counts.
  window.refreshDecisions = refreshAll;

  document.getElementById("decisions-refresh").addEventListener(
    "click", () => refreshAll().catch(error => toast(error.message)),
  );
  document.getElementById("decision-prev").addEventListener(
    "click", () => {
      offset = Math.max(0, offset - PAGE_SIZE);
      refreshItems().catch(error => toast(error.message));
    },
  );
  document.getElementById("decision-next").addEventListener(
    "click", () => {
      offset += PAGE_SIZE;
      refreshItems().catch(error => toast(error.message));
    },
  );
  if (tg && initData) {
    refreshAll().catch(error => toast(error.message));
  }
})();
