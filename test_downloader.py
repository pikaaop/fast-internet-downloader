"""Local end-to-end checks; no internet or external packages required."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from downloader import Cancelled, download, download_video

DATA = bytes(range(256)) * 8192
ranges = []


class Handler(BaseHTTPRequestHandler):
    media = None
    streams = {}
    def log_message(self, *args):
        pass

    def do_GET(self):
        requested = self.headers.get('Range')
        body = DATA
        status = 200
        if self.path == '/video.html':
            body = b'<html><title>Test clip</title><video src="/clip.mp4" controls></video></html>'
        elif self.path == '/clip.mp4':
            body = self.media
        elif self.path in self.streams:
            body = self.streams[self.path]
        if requested and self.path not in ('/single', '/video.html', '/clip.mp4') and self.path not in self.streams:
            start, end = map(int, re.fullmatch(r'bytes=(\d+)-(\d+)', requested).groups())
            ranges.append((start, end))
            body = DATA[start:end + 1]
            status = 206
        self.send_response(status)
        self.send_header('ETag', '"test-v1"')
        self.send_header('Content-Length', str(len(body)))
        if self.path == '/video.html':
            self.send_header('Content-Type', 'text/html')
        elif self.path == '/clip.mp4':
            self.send_header('Content-Type', 'video/mp4')
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(DATA)}')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with tempfile.TemporaryDirectory() as folder:
            parallel = Path(folder) / 'parallel.bin'
            download(base + '/parallel', parallel)
            assert parallel.read_bytes() == DATA
            assert len([r for r in ranges if r != (0, 0)]) == 4
            single = Path(folder) / 'single.bin'
            download(base + '/single', single)
            assert single.read_bytes() == DATA
            try:
                download(base + '/single', single)
                raise AssertionError('Existing file was overwritten')
            except FileExistsError:
                assert single.read_bytes() == DATA
            stop = threading.Event()
            cancelled = Path(folder) / 'cancelled.bin'
            try:
                download(base + '/single', cancelled,
                         progress=lambda *_: stop.set(), cancel=stop)
                raise AssertionError('Cancellation was ignored')
            except Cancelled:
                assert not cancelled.exists()
            assert not list(Path(folder).glob('.download-*'))
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            clip = Path(folder) / 'source.mp4'
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:d=1',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(clip)], check=True)
            Handler.media = clip.read_bytes()
            saved = download_video(base + '/video.html', folder)
            assert saved.read_bytes() == Handler.media
            subprocess.run([ffmpeg, '-v', 'error', '-i', str(saved), '-map', '0:v', '-map', '0:a',
                            '-f', 'null', '-'], check=True)
            for name, flag in [('video.mp4', '-an'), ('audio.m4a', '-vn')]:
                stream = Path(folder) / name
                subprocess.run([ffmpeg, '-v', 'error', '-i', str(clip), flag, '-c', 'copy', str(stream)], check=True)
                Handler.streams['/' + name] = stream.read_bytes()
            from unittest.mock import patch
            from yt_dlp import YoutubeDL
            from yt_dlp.extractor.common import InfoExtractor
            class SplitIE(InfoExtractor):
                _VALID_URL = r'http://127\.0\.0\.1:\d+/split/(?P<id>test)'
                def _real_extract(self, url):
                    return {'id': 'test', 'title': 'Merged test', 'formats': [
                        {'format_id': 'v', 'url': base + '/video.mp4', 'ext': 'mp4',
                         'vcodec': 'h264', 'acodec': 'none', 'height': 90},
                        {'format_id': 'a', 'url': base + '/audio.m4a', 'ext': 'm4a',
                         'vcodec': 'none', 'acodec': 'aac'}]}
            class LocalYDL(YoutubeDL):
                def __init__(self, options):
                    super().__init__(options, auto_init=False)
                    self.add_info_extractor(SplitIE())
                    self.add_default_info_extractors()
            with patch('yt_dlp.YoutubeDL', LocalYDL):
                merged = download_video(base + '/split/test', folder)
            assert merged.suffix == '.mkv'
            subprocess.run([ffmpeg, '-v', 'error', '-i', str(merged), '-map', '0:v', '-map', '0:a',
                            '-f', 'null', '-'], check=True)
            try:
                download(base + '/video.html', Path(folder) / 'bad.bin')
                raise AssertionError('Web page downloaded as a file')
            except ValueError:
                pass
            try:
                download_video(base + '/video.html', folder)
                raise AssertionError('Existing video was overwritten')
            except FileExistsError:
                assert saved.read_bytes() == Handler.media
            assert not list(Path(folder).glob('.video-*'))
        print('PASS: parallel integrity, single fallback, overwrite protection, cancellation cleanup')
        print('PASS: video page extraction, separate video/audio merge, playback, overwrite protection, HTML rejection')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
