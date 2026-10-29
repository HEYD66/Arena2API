/* Browser-independent conversation sources; optional local transport adapter. */
importScripts('conversation/local-transport.js','conversation/identity.js','conversation/background.js');

// No popup/window creation. The tab supplied by the click is the sole target.
if (chrome.action?.onClicked) chrome.action.onClicked.addListener(tab => {
  if (!Number.isInteger(tab?.id) || !/^https:\/\/arena\.ai(?:\/|$)/.test(tab.url || '')) return;
  chrome.tabs.sendMessage(tab.id, {type:'WORKSPACE_PANEL_TOGGLE'}, () => {
    // Missing content script after an extension reload: refresh that page.
    void chrome.runtime.lastError;
  });
});
