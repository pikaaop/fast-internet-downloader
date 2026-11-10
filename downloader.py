"""A small Windows downloader. Run with Python 3.10 or newer."""
import concurrent.futures
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class Cancelled(Exception):
    pass


def video_runtime():
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    node = str(bundled) if bundled.exists() else shutil.which('node')
    return {'node': {'path': node}} if node else {}


def download_video(url, folder, connections=4, progress=lambda size, total: None,
                   cancel=None, notice=lambda message: None):
    import imageio_ffmpeg
    from yt_dlp import YoutubeDL

    cancel = cancel or threading.Event()
    if urlsplit(url).scheme.lower() not in ('http', 'https') or not urlsplit(url).netloc:
        raise ValueError('Enter an HTTP or HTTPS video link.')

    def hook(data):
        if cancel.is_set():
            raise Cancelled()
        if data['status'] == 'downloading':
            progress(data.get('downloaded_bytes', 0),
                     data.get('total_bytes') or data.get('total_bytes_estimate'))
        elif data['status'] == 'finished':
            notice('Preparing video and audio…')

    class Logger:
        def debug(self, message):
            if cancel.is_set():
                raise Cancelled()

        def warning(self, message):
            notice(message)

        def error(self, message):
            notice(message)

    # Keep all fragments and merging inside a temporary folder until success.
    with tempfile.TemporaryDirectory(prefix='.video-', dir=folder) as temporary:
        options = {
            'outtmpl': str(Path(temporary) / '%(title).100B [%(id)s].%(ext)s'),
            'format': 'bv*+ba/b', 'merge_output_format': 'mkv',
            'ffmpeg_location': imageio_ffmpeg.get_ffmpeg_exe(),
            'js_runtimes': video_runtime(),
            'noplaylist': True, 'windowsfilenames': True,
            'concurrent_fragment_downloads': min(max(connections, 1), 8),
            'socket_timeout': 20, 'retries': 3, 'fragment_retries': 3,
            'progress_hooks': [hook], 'logger': Logger(), 'quiet': True,
            'overwrites': False,
        }
        try:
            with YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=True)
        except Exception:
            if cancel.is_set():
                raise Cancelled() from None
            raise
        if cancel.is_set():
            raise Cancelled()
        files = [p for p in Path(temporary).iterdir() if p.is_file() and p.suffix not in ('.part', '.ytdl')]
        if len(files) != 1:
            raise IOError('No complete video was produced. Try a single video link.')
        source = files[0]
        destination = Path(folder) / source.name
        if destination.exists():
            raise FileExistsError(f'Already exists: {destination.name}. Choose another folder.')
        if os.name == 'nt':
            os.rename(source, destination)
        else:
            os.link(source, destination)
        return destination


def download(url, destination, connections=4, progress=lambda size, total: None,
             cancel=None):
    cancel = cancel or threading.Event()
    if urlsplit(url).scheme.lower() not in ('http', 'https'):
        raise ValueError('Enter a direct HTTP or HTTPS download link.')
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('That file already exists. Choose a different name.')
    headers = {'User-Agent': 'InternetDownloader/1.0', 'Accept-Encoding': 'identity'}
    transferred = 0
    lock = threading.Lock()

    def check():
        if cancel.is_set():
            raise Cancelled()

    def report(amount, total):
        nonlocal transferred
        with lock:
            transferred += amount
            progress(transferred, total)

    def request(extra=None):
        check()
        return urlopen(Request(url, headers=headers | (extra or {})), timeout=20)

    # Probe with GET: many file servers don't implement HEAD correctly.
    with request({'Range': 'bytes=0-0'}) as response:
        if 'text/html' in response.headers.get('Content-Type', '').lower():
            raise ValueError('This link is a web page. Select Video / website for video links, or copy the actual file download URL.')
        match = re.fullmatch(r'bytes 0-0/(\d+)', response.headers.get('Content-Range', ''))
        total = int(match[1]) if response.status == 206 and match else None
        validator = response.headers.get('ETag')
        if validator and validator.startswith('W/'):
            validator = None
        validator = validator or response.headers.get('Last-Modified')
        parallel = bool(total and total >= 1024 * 1024 and validator and connections > 1)

    fd, temporary = tempfile.mkstemp(prefix='.download-', suffix='.part', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            if parallel:
                output.truncate(total)

        def segment(start, end):
            extra = {'Range': f'bytes={start}-{end}', 'If-Range': validator}
            with request(extra) as response, open(temporary, 'r+b') as output:
                expected = f'bytes {start}-{end}/{total}'
                if response.status != 206 or response.headers.get('Content-Range') != expected:
                    raise ValueError('Server changed or rejected parallel downloads.')
                if response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise ValueError('Server returned encoded data for a byte range.')
                output.seek(start)
                remaining = end - start + 1
                while remaining:
                    check()
                    chunk = response.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise IOError('Connection ended before the file was complete.')
                    output.write(chunk)
                    remaining -= len(chunk)
                    report(len(chunk), total)

        if parallel:
            count = min(max(1, connections), 8)
            with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
                futures = [pool.submit(segment, total * i // count,
                                       total * (i + 1) // count - 1) for i in range(count)]
                try:
                    for future in concurrent.futures.as_completed(futures):
                        future.result()
                except Exception:
                    cancel.set()
                    raise
        else:
            with request() as response, open(temporary, 'wb') as output:
                if response.status != 200:
                    raise IOError(f'Unexpected server response: {response.status}')
                length = response.headers.get('Content-Length')
                total = int(length) if length else None
                while True:
                    check()
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    report(len(chunk), total)
                if total is not None and transferred != total:
                    raise IOError('Connection ended before the file was complete.')
        check()
        # Windows rename fails if the destination exists, preserving existing files.
        if os.name == 'nt':
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
            os.unlink(temporary)
        return destination
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(initial_url=''):
    root = tk.Tk()
    root.title('Internet Downloader — Video + Files')
    root.geometry('700x400')
    root.minsize(650, 400)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Internet Downloader', font=('Segoe UI', 20, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='12 Mbps internet = up to 1.5 MB/s before overhead.').pack(anchor='w', pady=(4, 18))
    ttk.Label(frame, text='YouTube / video link or direct file URL').pack(anchor='w')
    url = ttk.Entry(frame)
    url.pack(fill='x', pady=(5, 12))
    url.insert(0, initial_url)
    url.focus()
    mode = ttk.Combobox(frame, values=['Video / website', 'Direct file'], state='readonly')
    mode.set('Video / website')
    mode.pack(anchor='w', pady=(0, 10))
    row = ttk.Frame(frame)
    row.pack(fill='x')
    ttk.Label(row, text='Connections').pack(side='left')
    connections = ttk.Combobox(row, values=['1', '2', '4', '8'], width=4, state='readonly')
    connections.set('4')
    connections.pack(side='left', padx=8)
    events = queue.SimpleQueue()
    stop = threading.Event()
    active = False
    started = 0
    previous = (0, 0)
    latest = (0, None)
    bar = ttk.Progressbar(frame, maximum=100)
    status = tk.StringVar(value='Paste a video link or select Direct file for other downloads.')

    def worker(link, path, count, video):
        try:
            callback = lambda size, total: events.put(('progress', (size, total)))
            if video:
                result = download_video(link, path, count, callback, stop,
                                        lambda message: events.put(('notice', message)))
            else:
                result = download(link, path, count, callback, stop)
            events.put(('done', str(result)))
        except Cancelled:
            events.put(('cancelled', None))
        except Exception as error:
            events.put(('error', str(error)))

    def start():
        nonlocal active, started, previous, latest
        link = url.get().strip()
        if urlsplit(link).scheme.lower() not in ('http', 'https') or not urlsplit(link).netloc:
            messagebox.showerror('Invalid link', 'Paste an HTTP or HTTPS link.')
            return
        video = mode.get() == 'Video / website'
        path = (filedialog.askdirectory(title='Choose video download folder') if video else
                filedialog.asksaveasfilename(title='Save download', initialfile=Path(urlsplit(link).path).name or 'download.bin'))
        if not path:
            return
        if not video and Path(path).exists():
            messagebox.showerror('File exists', 'Choose a new filename to keep your existing file safe.')
            return
        active = True
        started = time.monotonic()
        previous, latest = (0, started), (0, None)
        stop.clear()
        start_button.config(state='disabled')
        mode.config(state='disabled')
        cancel_button.config(state='normal')
        bar['value'] = 0
        status.set('Connecting…')
        threading.Thread(target=worker, args=(link, path, int(connections.get()), video), daemon=True).start()

    def cancel_download():
        stop.set()
        cancel_button.config(state='disabled')
        status.set('Cancelling… waiting for active connections to stop.')

    start_button = ttk.Button(row, text='Download…', command=start)
    start_button.pack(side='right')
    cancel_button = ttk.Button(row, text='Cancel', command=cancel_download, state='disabled')
    cancel_button.pack(side='right', padx=8)
    bar.pack(fill='x', pady=(20, 8))
    ttk.Label(frame, textvariable=status, wraplength=570).pack(anchor='w')
    ttk.Label(frame, text='Video mode saves the best available quality with audio. File mode handles ordinary files.', foreground='#666666', wraplength=640).pack(anchor='w', pady=(12, 0))

    def poll():
        nonlocal active, previous, latest
        while not events.empty():
            kind, value = events.get()
            if kind == 'progress':
                if value[0] < latest[0]:
                    previous = (0, time.monotonic())
                latest = value
                continue
            if kind == 'notice':
                latest = (0, None)
                status.set(value)
                continue
            active = False
            start_button.config(state='normal')
            mode.config(state='readonly')
            cancel_button.config(state='disabled')
            if kind == 'done':
                bar['value'] = 100
                status.set(f'Saved: {value}')
            elif kind == 'cancelled':
                status.set('Cancelled. Incomplete file removed.')
            else:
                status.set('Download failed. Incomplete file removed.')
                messagebox.showerror('Download failed', value)
        if active and not stop.is_set():
            size, total = latest
            now = time.monotonic()
            speed = (size - previous[0]) / max(now - previous[1], 0.001)
            previous = (size, now)
            if size:
                bar['value'] = size / total * 100 if total else 0
                amount = f'{size / 1e6:.1f} MB' + (f' / {total / 1e6:.1f} MB' if total else '')
                eta = f' • {int((total-size)/speed)}s remaining' if total and speed else ''
                status.set(f'{speed / 1e6:.2f} MB/s ({speed * 8 / 1e6:.2f} Mbps) • {amount}{eta}')
        root.after(500, poll)

    def close():
        if active:
            cancel_download()
            root.after(200, close_when_stopped)
        else:
            root.destroy()

    def close_when_stopped():
        if active:
            root.after(200, close_when_stopped)
        else:
            root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    poll()
    root.mainloop()


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '')
