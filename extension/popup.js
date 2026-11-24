document.getElementById("open").addEventListener("click", () => {
  chrome.tabs.create({ url: "https://music.youtube.com/" });
});
