const $ = (id) => document.getElementById(id);
chrome.storage.local.get(["apiUrl", "token"]).then(({ apiUrl, token }) => {
  $("apiUrl").value = apiUrl ?? "http://127.0.0.1:8765";
  $("token").value = token ?? "";
});
$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ apiUrl: $("apiUrl").value.trim().replace(/\/+$/, ""), token: $("token").value.trim() });
  $("saved").textContent = "Saved.";
});
