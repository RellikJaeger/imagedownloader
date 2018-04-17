#!/usr/bin/env python
# -*- coding: utf-8 -*-

import collections
import hashlib
import logging
import random
from concurrent import futures
from io import BytesIO
from pathlib import Path
from pprint import pformat
from time import sleep
from uuid import uuid4

import attr
import requests
from PIL import Image
from tqdm import tqdm, tqdm_notebook

from .settings import config
from .utils import to_bytes

logger = logging.getLogger(__name__)


def make_session(proxies=None, headers=None):
    proxies = proxies or {}
    headers = headers or {}
    s = requests.Session()
    s.proxies.update(proxies)
    s.headers.update(headers)
    s.id = uuid4().hex

    return s


@attr.s
class ImageDownloader(object):
    """Image downloader that converts to common format.

    Downloads images and converts them to JPG format and RGB mode.

    Parameters
    ----------
    store_path : str
        Root path where images should be stored
    n_workers : int
        Number of simultaneous threads to use
    timeout : float
        Timeout to be given to the url request
    min_wait : float
        Minimum wait time between image downloads
    max_wait : float
        Maximum wait time between image downloads
    proxies : str | list
        Proxy or list of proxies to use for the requests
    headers : dict
        headers to be given to requests
    user_agent : str
        User agent to be used for the requests
    notebook : bool
        If True, use the notebook version of tqdm
    debug : bool
        If True, log urls that could not be downloaded
    """

    store_path = attr.ib(converter=lambda v: Path(v).expanduser(), default=config['STORE_PATH'])
    n_workers = attr.ib(converter=int, default=config['N_WORKERS'])
    timeout = attr.ib(converter=float, default=config['TIMEOUT'])
    min_wait = attr.ib(converter=float, default=config['MIN_WAIT'])
    max_wait = attr.ib(converter=float, default=config['MAX_WAIT'])
    proxies = attr.ib(default=config['PROXIES'])
    headers = attr.ib(converter=dict, default=config['HEADERS'])
    user_agent = attr.ib(converter=str, default=config['USER_AGENT'])
    notebook = attr.ib(converter=bool, default=False)
    debug = attr.ib(converter=bool, default=False)

    @user_agent.validator
    def update_headers(self, attribute, value):
        if value is not None:
            self.headers.update({'User-Agent': value})

    @proxies.validator
    def resolve_proxies(self, attribute, value):

        def format_as_dict(proxy):
            return {
                "http": proxy,
                "https": proxy
            }

        self.proxies = None
        if isinstance(value, str):
            self.proxies = [format_as_dict(value)]
        elif isinstance(value, list) and len(value) > 0:
            self.proxies = [format_as_dict(proxy) for proxy in value]
        elif value is not None:
            raise ValueError("proxies should be either a string, a list of strings or None")

    @notebook.validator
    def set_tqdm(self, attribute, value):
        self.tqdm = tqdm_notebook if value else tqdm

    def __attrs_post_init__(self):
        Path(self.store_path).mkdir(exist_ok=True, parents=True)

    def get_proxy(self):
        if isinstance(self.proxies, list):
            return random.choice(self.proxies)
        else:
            return self.proxies

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
        paths : str | list
            If url is a str, path where the image was stored.
            If url is iterable the list of image paths is returned. If
            image failed to download, None is given instead of image path
        """

        if self.debug:
            title = '\033[92mImage downloader called with the following arguments :\033[0m'
            arguments = pformat(attr.asdict(self))
            separation = '=' * max(map(len, arguments.split("\n")))
            print(f"{separation}\n{title}\n{arguments}\n{separation}")

        if not isinstance(urls, (str, collections.Iterable)):
            raise ValueError("urls should be str or iterable")

        if isinstance(urls, str):
            return str(self._download_image(urls, force=force))

        with futures.ThreadPoolExecutor(max_workers=self.n_workers) as executor:
            n_fail = 0
            future_to_url = {
                executor.submit(self._download_image, url, force): (i, url)
                for i, url in enumerate(urls)
            }
            total = len(future_to_url)
            paths = [None] * total
            for future in self.tqdm(futures.as_completed(future_to_url), total=total, miniters=1):
                i, url = future_to_url[future]
                if future.exception() is None:
                    paths[i] = str(future.result())
                else:
                    n_fail += 1
                    if self.debug:
                        logger.error(f'Error: {future.exception()}')
                        logger.error(f'For url: {url}')

            logger.info(f"{n_fail} images failed to download")

        return paths

    def _download_image(self, url, force=False, session=None, timeout=None):
        """Download image and convert to jpeg rgb mode.

        If the image path already exists, it considers that the file has
        already been downloaded and does not downloaded again.


        Parameters
        ----------
        url : str
            url of the image to be downloaded

        force : bool
            If True force the download even if the file already exists

        session : requests.Session
            An instance of requests.Session with which image will be downloaded.
            Useful when you want to use the same session for several downloads.

        timeout : float
            Timeout to be given to the url request

        Returns
        -------
        path : str
            Path where the image was stored
        """
        session = session or make_session(proxies=self.get_proxy(), headers=self.headers)
        timeout = timeout or self.timeout
        path = self.file_path(url)
        if not path.exists() or force:
            response = session.get(url, timeout=timeout)
            orig_img = Image.open(BytesIO(response.content))
            img, buf = self.convert_image(orig_img)
            self._persist_file(path, buf)
            # Only wait if image had to be downloaded
            sleep(random.uniform(self.min_wait, self.max_wait))


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


def download(urls,
             store_path=config['STORE_PATH'],
             n_workers=config['N_WORKERS'],
             timeout=config['TIMEOUT'],
             min_wait=config['MIN_WAIT'],
             max_wait=config['MAX_WAIT'],
             proxies=config['PROXIES'],
             headers=config['HEADERS'],
             user_agent=config['USER_AGENT'],
             notebook=False,
             debug=False,
             force=False):
    """Asynchronously download images using multiple threads.

    Parameters
    ----------
    urls : iterator
        Iterator of urls
    store_path : str
        Root path where images should be stored
    n_workers : int
        Number of simultaneous threads to use
    timeout : float
        Timeout to be given to the url request
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
    debug : bool
        If True, log urls that could not be downloaded
    force : bool
        If True force the download even if the files already exists

    Returns
    -------
    paths : str | list
        If url is a str, path where the image was stored.
        If url is iterable the list of image paths is returned. If
        image failed to download, None is given instead of image path
    """
    downloader = ImageDownloader(
        store_path,
        n_workers=n_workers,
        timeout=timeout,
        min_wait=min_wait,
        max_wait=max_wait,
        proxies=proxies,
        headers=headers,
        user_agent=user_agent,
        notebook=notebook,
        debug=debug
    )

    return downloader(urls, force=force)
