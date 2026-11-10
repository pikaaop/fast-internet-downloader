# Internet Downloader

Double-click **Start Downloader.bat**. For YouTube or another supported video site, keep **Video / website** selected, paste a single video link, click **Download**, and choose a folder. The app saves the best available video with audio and its actual extension (merged streams use MKV).

For ZIPs, installers, documents, and other ordinary files, select **Direct file** and paste the actual file URL. A website's download page is not the file URL.

Python 3.10+ with Tkinter is required. Video support uses yt-dlp, its default dependencies, Node.js 22+, and the FFmpeg binary bundled with imageio-ffmpeg. These are available on this computer; the app uses Codex's bundled Node.js when present. On another computer run `python -m pip install -r requirements.txt` and install Node.js 22+. Run **Update Video Support.bat** when video websites change.

Your 12 Mbps connection has a theoretical maximum of **1.5 MB/s**. Network overhead, Wi-Fi, other devices, and the file server can reduce that. This app has no speed cap; four connections may help when a server limits each connection. No downloader can guarantee full speed or exceed your internet plan.

Parallel downloads require byte-range support and an ETag or Last-Modified validator to avoid combining different file versions. Other servers use one connection automatically. Use 1 connection if a server rejects parallel requests. Failed or cancelled downloads remove temporary data and never replace an existing destination file. Cancellation may take up to the connection timeout while waiting for a server.

Video mode uses yt-dlp's supported extractors. Private, login-required, DRM-protected, or blocked videos may fail; the app displays the error. Playlists and torrents are not supported. Downloads restart from the beginning after cancellation. Temporary video fragments are removed after failure or cancellation; existing files are preserved.

Run `python test_downloader.py` for local integrity and fallback checks.
