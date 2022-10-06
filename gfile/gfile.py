from math import ceil
import os
import re
import sys
import tempfile
from threading import Lock, Thread
import uuid
import requests
from requests_toolbelt import MultipartEncoderMonitor
import requests as r

from requests_toolbelt.multipart import encoder
from tqdm import tqdm


class GFile:

    def __init__(self, uri, progress=False, thread_num=8, chunk_size=1024*1024*100, chunk_copy_size=1024*1024, **kwargs) -> None:
        self.uri = uri
        self.chunk_size = chunk_size
        self.chunk_copy_size = chunk_copy_size
        self.thread_num=thread_num
        self.progress = progress
        self.data = None
        self.pbar: tqdm = None
        self.session = requests.Session()
        self.index = 0


    def upload_chunk(self, chunks ,server):
        self.lock.acquire()
        with open(self.uri, 'rb') as ff:
            while not self.failed and self.index < chunks:
                chunk_id = f'chunk {self.index}'
                if self.pbar:
                    self.pbar.desc = chunk_id
                with tempfile.NamedTemporaryFile() as f:
                    i = 0
                    chunk = ff.read(self.chunk_copy_size)
                    while i < self.chunk_size and chunk:
                        f.write(chunk)
                        i += self.chunk_copy_size
                        chunk = ff.read(self.chunk_copy_size)

                    f.seek(0)

                    fields = {
                        "id": self.token,
                        "name": os.path.basename(self.uri),
                        "chunk": str(self.index),
                        "chunks": str(chunks),
                        "lifetime": "100",
                        "file": ("blob", f, "application/octet-stream"),
                    }
                    # print(fields)

                    released = False
                    
                    self.index += 1


                    def progress(monitor: MultipartEncoderMonitor):
                        nonlocal released
                        self.pbar.update(monitor.bytes_read - monitor.prog)                        
                        monitor.prog = monitor.bytes_read
                        if self.failed: self.session.close()
                        if not released and monitor.bytes_read > i/10:
                            self.lock.release()
                            released = True

                    form = encoder.MultipartEncoder(fields)
                    if self.pbar:
                        
                        form = encoder.MultipartEncoderMonitor(form, progress)
                        setattr(form, 'prog', 0)
                    
                    headers = {
                        "content-type": form.content_type,
                        "Referer": "https://gigafile.nu/",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36",
                        "Origin": "https://gigafile.nu"
                    }
                    # print(headers)
                    try:   
                        resp = self.session.post(
                            f"https://{server}/upload_chunk.php", headers=headers, data=form)

                        resp = resp.json()

                        if 'url' in resp:
                            self.data = resp
                    
                        if 'status' not in resp or resp['status']:
                            print(resp)
                            self.failed = True
                    except OSError as reason:
                        print(reason)
                        self.failed = True

                    
                    if self.failed: break
                    self.lock.acquire()
        if self.lock.locked(): self.lock.release()
        
        
    def upload(self):
        self.token = uuid.uuid1().hex
        self.pbar = None
        self.failed = False
        self.index = 0
        self.lock = Lock()
        size = os.path.getsize(self.uri)
        chunks = ceil(size / self.chunk_size)
        if self.progress:
            self.pbar = tqdm(total=size, unit="B", unit_scale=True, leave=False, unit_divisor=1024)
        self.session = requests.Session()
        server = re.search(
            r'var server = "(.+?)"', self.session.get('https://gigafile.nu/').text)[1]
        # self.session.get('https://gigafile.nu/')
        threads = []
        for _ in range(self.thread_num):
            t = Thread(target=self.upload_chunk, args=(chunks,server,))
            threads.append(t)
            t.start()
        
        try:
            for t in threads:
                t.join()
                
            self.pbar.close()
            if 'url' not in self.data:
                print('something went wrong', self.data)
        except KeyboardInterrupt:
            self.pbar.close()
            self.failed = True
            print('Aborted! cleaning...')
        return self

    def get_download_page(self): return self.data and self.data['url']
    def get_file_id(self): return self.data and self.data['filename']
    def get_file_name(self): return os.path.basename(self.uri)
    def get_file_size(self): return f"{round(os.path.getsize(self.uri) / 1024**3,3)}GB"

    def get_download(self):
        _data: dict[str, str] = self.data
        if not os.path.exists(self.uri):
            data = re.search(r'^https?:\/\/\d+?\.gigafile\.nu\/([a-z0-9-]+)$', self.uri)
            if data:
                _data = {'url': self.uri, 'filename': data[1]}
            else:
                raise ValueError('URL invalid')

        if not _data:
            return ValueError('You specified no file to upload nor to download')

        sess = requests.Session()
        sess.get(_data['url'])
        return (_data['url'].replace(_data['filename'], 'download.php?file='+_data['filename']), sess.cookies)

    def download(self, copy_size=1024*1024, progress=True, filename=None):
        url, cookies = self.get_download()
        if not filename:
            headers = r.head(url, cookies=cookies).headers
            filesize = int(headers['Content-Length'])
            filename = re.search(r'filename="(.+?)";', headers['Content-Disposition'])[1]
            filename = re.sub(r'\\|\/|:|\*|\?|"|<|>|\|', '_', filename)
        if progress:
            pbar = tqdm(total=filesize, unit='B', unit_scale=True, desc=filename)
            
        with open(filename, 'wb') as f:
            with r.get(url, cookies=cookies, stream=True) as req:
                req.raise_for_status()
                for chunk in req.iter_content(chunk_size=copy_size):
                    f.write(chunk)
                    if pbar: pbar.update(len(chunk))
        return filename
