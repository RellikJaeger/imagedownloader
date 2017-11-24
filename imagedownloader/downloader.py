#!/usr/bin/env python
# -*- coding: utf-8 -*-

import collections
import hashlib
import logging
import random
import types
from concurrent import futures
from time import sleep
from pathlib import Path
from io import BytesIO

from .settings import config
from .utils import to_bytes

from PIL import Image
import requests

logger = logging.getLogger(__name__)


class ImageDownloader(object):
    """Image downloader that converts to common format and creates thumbs.

    Downloads images and converts them to JPG format and RGB mode. If specified
    it generates thumbnails of the images.

    Parameters
    ----------
    store_path : str
        Root path where images should be stored
    n_workers : int
        Number of simultaneous threads to use
    timeout : float
        Timeout to be given to the url request
    thumbs : bool
        If True, create thumbnails of sizes according to self.thumbs_size
    thumbs_size : dict | list
        Dictionary of the kind {name: (width, height)} indicating the thumbnail
        sizes to be created
    min_wait : float
        Minimum wait time between image downloads
    max_wait : float
        Maximum wait time between image downloads
    proxies : list | dict
        Proxy or list of proxies to use for the requests
    headers : dict
        headers to be given to requests
    user_agent : str
        User agent to be used for the requests
    notebook : bool
        If True, use the notebook version of tqdm
    """

    def __init__(self,
                 store_path=config['STORE_PATH'],
                 n_workers=config['N_WORKERS'],
                 thumbs=config['THUMBS'],
                 thumbs_size=config['THUMBS_SIZES'],
                 timeout=config['TIMEOUT'],
                 min_wait=config['MIN_WAIT'],
                 max_wait=config['MAX_WAIT'],
                 proxies=config['PROXIES'],
                 headers=config['HEADERS'],
                 user_agent=config['USER_AGENT'],
                 notebook=False):

        self.store_path = Path(store_path).expanduser()
        self.timeout = timeout
        self.min_wait = min_wait
        self.max_wait = max_wait
        self.n_workers = n_workers
        self.notebook = notebook
        self.headers = headers
        if user_agent is not None:
            self.headers.update({'User-Agent': user_agent})
        assert (proxies is None) or isinstance(proxies, list) or isinstance(proxies, dict),\
            "proxies should be either a list or a list of dicts"

        if isinstance(proxies, dict):
            self.proxies = proxies
        elif isinstance(thumbs_size, list):
            self.proxies = [
                {
                    "http": proxy,
                    "https": proxy
                }
                for proxy in proxies
            ]
        else:
            self.proxies = None

        thumbs_size = thumbs_size or {}
        if thumbs:
            if isinstance(thumbs_size, dict):
                self.thumbs_size = thumbs_size
            elif isinstance(thumbs_size, list):
                self.thumbs_size = {
                    str(thumb): (thumb, thumb)
                    for thumb in thumbs_size
                }
            else:
                raise Exception(f"thumbs_size must be a dictionary or a list")
        else:
            self.thumbs_size = {}
        self._makedirs()

    def get_proxy(self):
        if isinstance(self.proxies, list):
            return random.choice(self.proxies)
        else:
            return self.proxies

    def _makedirs(self):

        subdirs = ['.']
        if hasattr(self, 'thumbs_size'):
            subdirs += [f'thumbs/{size}' for size in self.thumbs_size.keys()]

        for subdir in subdirs:
            Path(self.store_path, subdir).mkdir(exist_ok=True, parents=True)

    def __call__(self, urls, force=False):
        """Download url or list of urls

        Parameters
        ----------
        urls : str | list
            url or list of urls to be downloaded

        force : bool
            If True force the download even if the files already exists

        Returns
        -------
        paths : str | dict
            If url is a str, path where the image was stored.
            If url is iterable a dict with urls as keys and image path as
            values. If image failed to download, None is given instead of
            image path
        """

        if isinstance(urls, str):
            return self.download_image(urls, force=force)

        assert isinstance(urls, collections.Iterable), \
            "urls should be str or iterable"

        if self.notebook:
            from tqdm import tqdm_notebook as tqdm
        else:
            from tqdm import tqdm

        with futures.ThreadPoolExecutor(max_workers=self.n_workers) as executor:

            future_to_url = dict(
                (executor.submit(self.download_image, url, force), url)
                for url in urls
            )

            paths = {}
            if isinstance(urls, types.GeneratorType):
                total = None
            else:
                total = len(urls)

            for future in tqdm(futures.as_completed(future_to_url), total=total, miniters=1):
                url = future_to_url[future]
                if future.exception() is not None:
                    logger.error(f'Error: {future.exception()}')
                    logger.error(f'For url: {url}')
                    paths[url] = None
                else:
                    paths[url] = future.result()
        return paths

    def download_image(self, url, force=False):
        """Download image, create thumbnails, store and return checksum.

        Downloads image of the given url. If self.thumbs is True, it creates
        thumbnails of sizes according to self.thumbs_size. The md5 checksum of
        the image is returned for checking duplicates.

        If the image path already exists, it considers that the file has
        already been downloaded and does not downloaded again.


        Parameters
        ----------
        url : str
            url of the image to be downloaded

        force : bool
            If True force the download even if the file already exists

        Returns
        -------
        path : str
            Path where the image was stored
        """
        orig_img = None
        path = self.file_path(url)
        if not path.exists() or force:
            response = requests.get(
                url,
                timeout=self.timeout,
                proxies=self.get_proxy(),
                headers=self.headers
            )
            orig_img = Image.open(BytesIO(response.content))
            img, buf = self.convert_image(orig_img)
            self._persist_file(path, buf)
            # Only wait if image had to be downloaded
            sleep(random.uniform(self.min_wait, self.max_wait))

        for thumb_id, size in self.thumbs_size.items():
            thumb_path = self.thumb_path(url, thumb_id)
            if not thumb_path.exists() or force:
                orig_img = orig_img or Image.open(str(path))
                thumb_image, thumb_buf = self.convert_image(orig_img, size)
                self._persist_file(thumb_path, thumb_buf)

        return path

    @staticmethod
    def _persist_file(path, buf):
        with path.open('wb') as f:
            f.write(buf.getvalue())

    @staticmethod
    def convert_image(img, size=None):
        """Convert images to JPG, RGB mode and given size if any.

        Parameters
        ----------
        img : Pil.Image
        size : tuple
            tuple of (width, height)

        Returns
        -------
        img : Pil.Image
            Converted image in Pil format
        buf : BytesIO
            Buffer of the converted image
        """
        if img.format == 'PNG' and img.mode == 'RGBA':
            background = Image.new('RGBA', img.size, (255, 255, 255))
            background.paste(img, img)
            img = background.convert('RGB')
        elif img.mode == 'P':
            img = img.convert("RGBA")
            background = Image.new('RGBA', img.size, (255, 255, 255))
            background.paste(img, img)
            img = background.convert('RGB')
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        if size:
            img = img.copy()
            img.thumbnail(size, Image.ANTIALIAS)

        buf = BytesIO()
        img.save(buf, 'JPEG')
        return img, buf

    def file_path(self, url):
        """Hash url to get file path of full image
        """
        image_guid = hashlib.sha1(to_bytes(url)).hexdigest()
        return Path(self.store_path, image_guid + '.jpg')

    def thumb_path(self, url, thumb_id):
        """Hash url to get file path of thumbnail
        """
        thumb_guid = hashlib.sha1(to_bytes(url)).hexdigest()
        return Path(self.store_path, 'thumbs', thumb_id, thumb_guid + '.jpg')


def download(iterator,
             store_path=config['STORE_PATH'],
             thumbs=config['THUMBS'],
             thumbs_size=config['THUMBS_SIZES'],
             n_workers=config['N_WORKERS'],
             timeout=config['TIMEOUT'],
             min_wait=config['MIN_WAIT'],
             max_wait=config['MAX_WAIT'],
             proxies=config['PROXIES'],
             headers=config['HEADERS'],
             user_agent=config['USER_AGENT'],
             force=False,
             notebook=False):
    """Asynchronously download images using multiple threads.

    Parameters
    ----------
    iterator : iterator
        Iterator of urls
    store_path : str
        Root path where images should be stored
    n_workers : int
        Number of simultaneous threads to use
    force : bool
        If True force the download even if the files already exists
    notebook : bool
        If True, use the notebook version of tqdm
    timeout : float
        Timeout to be given to the url request
    thumbs : bool
        If True, create thumbnails of sizes according to thumbs_size
    thumbs_size : dict
        Dictionary of the kind {name: (width, height)} indicating the thumbnail
        sizes to be created
    min_wait : float
        Minimum wait time between image downloads
    max_wait : float
        Maximum wait time between image downloads
    proxies : list | dict
        Proxy or list of proxies to use for the requests
    headers : dict
        headers to be given to requests
    user_agent : str
        User agent to be used for the requests

    Returns
    -------
    paths : str | dict
        If url is a str, path where the image was stored.
        If url is iterable a dict with urls as keys and image path as
        values. If image failed to download, None is given instead of
        image path
    """
    downloader = ImageDownloader(
        store_path,
        n_workers=n_workers,
        thumbs=thumbs,
        thumbs_size=thumbs_size,
        timeout=timeout,
        min_wait=min_wait,
        max_wait=max_wait,
        proxies=proxies,
        headers=headers,
        user_agent=user_agent,
        notebook=notebook
    )

    results = downloader(iterator, force=force)

    paths = [
        str(response)
        if response is not None else None
        for url, response in results.items()
    ]

    return paths
