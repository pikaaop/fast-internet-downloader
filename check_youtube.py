"""Inspect a supplied video URL without downloading it."""
import sys
import tempfile
import threading
from downloader import Cancelled, download_video, video_runtime
from yt_dlp import YoutubeDL

if __name__ == '__main__':
    with YoutubeDL({'skip_download': True, 'noplaylist': True,
                    'socket_timeout': 20, 'retries': 1,
                    'js_runtimes': video_runtime()}) as ydl:
        info = ydl.extract_info(sys.argv[1], download=False)
        print('Title:', info.get('title'))
        print('Duration:', info.get('duration'))
        print('Available formats:', len(info.get('formats', [])))
    if '--transfer' in sys.argv:
        stop = threading.Event()
        def received(size, total):
            if size >= 65536:
                print('PASS: received video bytes from the supplied YouTube link')
                stop.set()
        with tempfile.TemporaryDirectory() as folder:
            try:
                download_video(sys.argv[1], folder, progress=received, cancel=stop)
            except Cancelled:
                assert stop.is_set()
