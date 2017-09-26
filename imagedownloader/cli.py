#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
CLI for image downloader
"""

import argparse
from multiprocessing import cpu_count

from . import logger, download
from .downloader import DEFAULT_HEADERS

__author__ = "Felipe Aguirre Martinez"
__copyright__ = "Copyright 2017, Workit software"
__email__ = "faguirre@workit-software.com"


def parse():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Bulk image downloader from a list of urls"
    )

    parser.add_argument('urls', type=str,
                        help="Text file with the list of urls to be downloaded")

    parser.add_argument('-o', '--store_path', type=str, default='imgs',
                        help="Root path where images should be stored")

    parser.add_argument('--n_workers', type=int, default=cpu_count(),
                        help="Number of simultaneous threads to use")

    parser.add_argument('-f', '--force', action='store_true',
                        help="Force the download even if the files already exists")

    parser.add_argument('--notebook', action='store_true',
                        help="Use the notebook version of tqdm")

    parser.add_argument('--timeout', type=float, default=5.0,
                        help="Timeout to be given to the url request")

    parser.add_argument('--thumbs', type=int, action='append',
                        help="Thumbnail size to be created. "
                             "Can be specified as many times as thumbs sizes you want")

    parser.add_argument('--min_wait', type=float, default=0.0,
                        help="Minimum wait time between image downloads")

    parser.add_argument('--max_wait', type=float, default=0.0,
                        help="Maximum wait time between image downloads")

    parser.add_argument('--proxy', type=str, action='append',
                        help="Proxy or list of proxies to use for the requests")

    parser.add_argument('-d', '--debug', action='store_true',
                        help="Activate debug mode")

    parser.add_argument('-u', '--user_agent', type=str,
                        help="User agent to be used for the requests")

    args = parser.parse_args()

    return args


def pprint_args_attributes(args):
    """Print arguments parsed from the CLI
    """
    title = '\033[92mImage downloader called with the following arguments :\033[0m'
    print_args = '\n' + '=' * len(title) + '\n{}\n'.format(title)
    for attr in dir(args):
        if attr[0] != '_':
            print_args += attr.ljust(15) + '--> ' + str(getattr(args, attr)) + '\n'

    return print_args + '=' * len(title)


def main():
    args = parse()

    with open(args.urls, 'r') as fh:
        urls = [url.strip('\n') for url in fh.readlines()]

    if args.user_agent is not None:
        DEFAULT_HEADERS.update(
            {
                'User-Agent': args.user_agent,
            }
        )

    if args.proxy is not None:
        proxies = [
            {
                "http": proxy,
                "https": proxy
            }
            for proxy in args.proxy
        ]
    else:
        proxies = None

    if args.thumbs is not None:
        thumbs = True
        thumbs_size = {
            str(thumb): (thumb, thumb)
            for thumb in args.thumbs
        }
    else:
        thumbs = False
        thumbs_size = {}

    if args.debug:
        print(pprint_args_attributes(args))
        logger.debug('User-Agent: ' + DEFAULT_HEADERS['User-Agent'])
        logger.debug('Proxies: ' + str(proxies))
        logger.debug('Thumbs: ' + str(thumbs_size))


    results = download(
        urls,
        args.store_path,
        n_workers=args.n_workers,
        force=args.force,
        notebook=args.notebook,
        timeout=args.timeout,
        thumbs=thumbs,
        thumbs_size=thumbs_size,
        min_wait=args.min_wait,
        max_wait=args.max_wait,
        proxies=proxies,
        headers=DEFAULT_HEADERS
    )

    downloaded_imgs = len([
        response
        for url, response in results.items()
        if response is not None
    ])

    logger.debug(
        "Downloaded {:0.2%} of images ({})".format(
            downloaded_imgs / len(urls),
            downloaded_imgs
        )
    )