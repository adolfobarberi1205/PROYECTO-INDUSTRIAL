// Apply the saved preference before the app paints. Storage may be blocked.
;(function () {
  var theme = 'dark'
  try {
    if (localStorage.getItem('etl_theme') === 'light') theme = 'light'
  } catch {
    // Keep the existing dark appearance when storage is unavailable.
  }
  document.documentElement.dataset.theme = theme
})()
